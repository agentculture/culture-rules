"""t8: rule actions dispatch through the actor named in ``params.actor``, with limits.

* A rule action naming an enabled actor runs through the ``action:<kind>`` port wrapped
  in :class:`~culture_rules.actors.limits.LimitedActor` with *that* actor's limits; the
  port sees ``context.actor`` and loads the actor document itself (here: a runner actor
  whose registered commands run through :class:`~culture_rules.actors.code.CodeRunner`).
* A rule action naming an unknown or disabled actor fails the run with code
  ``actor_unavailable`` (non-retryable) and never falls back to another action port.
* No actor named: today's ``action:<kind>`` fallback, no limits.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pytest

from culture_rules.actors.code import CodeRunner
from culture_rules.actors.limits import USAGE_COLLECTION, LimitedActor
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.claims import idempotency_key
from culture_rules.engine.runs import ACTION_STEP, ACTOR_UNAVAILABLE, Executor, step_state
from culture_rules.model.action import Action
from culture_rules.model.actor import Actor
from culture_rules.model.common import RetryPolicy
from culture_rules.node.actors import ACTORS_COLLECTION, ActorRouter
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, enrol_online, machine, rule

COMMANDS = {"say": {"argv": ["echo", "hello"], "params": {}, "timeout": 10}}


class MachineCommandPort:
    """Test stand-in for t24's port: loads the runner actor by ``context.actor``."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.contexts: list[InvocationContext] = []

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        self.contexts.append(context)
        doc = self.store.get(ACTORS_COLLECTION, context.actor)
        runner = CodeRunner(doc["params"]["commands"], is_admin=lambda _identity: False)
        # the catalogue types machine.command ``args`` as a list; t24 owns mapping it onto
        # the command's declared params, so this stand-in runs parameterless commands only
        call = {"command": input["command"]}
        return runner.invoke(call, idempotency_key, deadline, context=context)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock) -> MemoryStore:
    s = MemoryStore(clock=clock)
    enrol_online(s, clock, machine("spark"))  # the runner actor lives on spark
    return s


def put_runner(store: MemoryStore, *, enabled: bool = True, actor_id: str = "box") -> None:
    actor = Actor(
        id=actor_id,
        name=actor_id,
        kind="runner",
        machine="spark",
        params={"commands": COMMANDS, "max_concurrency": 1},
        enabled=enabled,
    )
    store.put(ACTORS_COLLECTION, actor.to_dict())


def command_rule(actor: str | None = "box", kind: str = "machine.command") -> Any:
    params: dict[str, Any] = {"command": "say"}
    if actor is not None:
        params["actor"] = actor
    action = Action(kind=kind, params=params, retry=RetryPolicy(max_attempts=3))
    return rule(workflow_id=None, action=action)


def setup(store: MemoryStore, clock: Clock) -> tuple[Executor, MachineCommandPort, FakeActor]:
    command = MachineCommandPort(store)
    noop = FakeActor()
    router = ActorRouter(
        store,
        ports={"action:machine.command": command, "action": noop, "*": noop},
        clock=clock,
    )
    return Executor(store, "spark", router, clock=clock), command, noop


def test_named_enabled_runner_executes_through_code_runner_wrapped_in_limited_actor(store, clock):
    put_runner(store)
    ex, command, noop = setup(store, clock)
    run = ex.start(command_rule(), None)
    ex.run_until_idle()

    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    action = step_state(doc, ACTION_STEP)
    assert action["outputs"]["stdout"].strip() == "hello"
    assert [c.actor for c in command.contexts] == ["box"]
    assert noop.calls == []
    usage = store.get(USAGE_COLLECTION, "box")  # LimitedActor accounted the attempt
    assert usage["done"] == [idempotency_key(run["id"], ACTION_STEP)]
    assert usage["inflight"] == []


def test_router_wraps_the_action_port_in_limited_actor_with_the_named_actors_limits(store, clock):
    put_runner(store)
    command = MachineCommandPort(store)
    router = ActorRouter(store, ports={"action:machine.command": command}, clock=clock)
    ctx = InvocationContext(
        "run1",
        ACTION_STEP,
        "action",
        "spark",
        1,
        "box",
        {"kind": "machine.command", "params": {"actor": "box", "command": "say"}},
    )
    port = router(ctx)
    assert isinstance(port, LimitedActor)
    assert port.inner is command
    assert port.actor == "box"
    assert port.limits.max_concurrency == 1
    assert router(ctx) is port  # cached per (actor, action kind)


def test_action_on_a_capped_actor_is_blocked_not_run(store, clock):
    put_runner(store)
    store.put(
        USAGE_COLLECTION,
        {
            "id": "box",
            "actor": "box",
            "day": clock().strftime("%Y-%m-%d"),
            "tokens": 0,
            "inflight": [{"key": "other", "until": "2999-01-01T00:00:00+00:00"}],
            "done": [],
            "rev": 1,
        },
    )
    ex, command, _ = setup(store, clock)
    run = ex.start(command_rule(), None)
    ex.run_until_idle()
    assert step_state(ex.run(run["id"]), ACTION_STEP)["status"] == "blocked"
    assert command.contexts == []


@pytest.mark.parametrize("make", ["disabled", "unknown"])
def test_named_disabled_or_unknown_actor_fails_with_actor_unavailable_and_never_runs_noop(
    store, clock, make
):
    if make == "disabled":
        put_runner(store, enabled=False)
    ex, command, noop = setup(store, clock)
    run = ex.start(command_rule(), None)
    ex.run_until_idle()

    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert doc["error"]["code"] == ACTOR_UNAVAILABLE
    assert doc["error"]["step"] == ACTION_STEP
    assert "box" in doc["error"]["message"]
    action = step_state(doc, ACTION_STEP)
    assert action["attempt"] == 1  # non-retryable: the retry policy is not consumed
    assert command.contexts == []
    assert noop.calls == []
    assert store.get(USAGE_COLLECTION, "box") is None


def test_unavailable_actor_port_reports_actor_unavailable_non_retryable(store, clock):
    router = ActorRouter(store, ports={"*": FakeActor()}, clock=clock)
    ctx = InvocationContext(
        "run1",
        ACTION_STEP,
        "action",
        "spark",
        1,
        "ghost",
        {"kind": "noop", "params": {"actor": "ghost"}},
    )
    result = router(ctx).invoke({}, "k", clock(), context=ctx)
    assert result.outcome == "failed"
    assert result.error == ACTOR_UNAVAILABLE
    assert result.retryable is False


def test_action_without_a_named_actor_keeps_the_action_kind_fallback(store, clock):
    noop = FakeActor()
    router = ActorRouter(store, ports={"action:message": noop}, clock=clock)
    ex = Executor(store, "spark", router, clock=clock)
    run = ex.start(command_rule(actor=None, kind="message"), None)
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "succeeded"
    assert [c[2].actor for c in noop.calls] == [None]
    assert store.find(USAGE_COLLECTION) == []


def test_an_action_through_an_actor_on_a_machine_runs_only_on_that_machine(store, clock):
    """App credentials live on the actor's machine, so its actions must run there."""
    enrol_online(store, clock, machine("thor"))
    put_runner(store)  # box lives on spark
    command = MachineCommandPort(store)

    def node(host: str) -> Executor:
        ports = {"action:machine.command": command, "*": FakeActor()}
        return Executor(store, host, ActorRouter(store, ports=ports, clock=clock), clock=clock)

    thor, spark = node("thor"), node("spark")
    run = thor.start(command_rule(), None)
    thor.run_until_idle()
    assert command.contexts == []  # thor never dispatched it
    spark.run_until_idle()
    doc = spark.run(run["id"])
    assert doc["status"] == "succeeded"
    assert [c.host for c in command.contexts] == ["spark"]
