"""Shared fixtures for the run-executor tests: a fake actor, a clock, model builders.

The fake actor stands in for every adapter (agent, code, human, service). It keeps a
side-effect ledger keyed by idempotency key that survives an engine "restart" (a new
Executor instance), exactly like a remote actor outlives the engine process.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import culture_rules.engine.runs  # noqa: F401 - registers the run verbs in MUTATING_VERBS
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.machines.enrol import enrol
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION
from culture_rules.model.action import Action
from culture_rules.model.common import RetryPolicy
from culture_rules.model.machine import Machine
from culture_rules.model.placement import Placement
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.model.workflow import Edge, Output, Port, Step, Workflow

T0 = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Crash(BaseException):
    """Simulates the engine process dying mid-call (not an ordinary Exception)."""


class FakeActor:
    """An ActorPort double, idempotent on the key unless ``idempotent=False``.

    Behaviours are scripted per step key with :meth:`on`; each invocation consumes one.
    ``("complete", output)``, ``("accept",)``, ``("fail", error, retryable)``,
    ``("block", reason)``, ``("lose_ack", output)`` (side effect happens, the ack is
    lost: raises ConnectionError), ``("crash", output)`` (side effect happens, then the
    engine dies: raises :class:`Crash`). Unscripted calls complete with ``default(input)``.
    """

    def __init__(
        self,
        *,
        idempotent: bool = True,
        default: Callable[[Mapping[str, Any], InvocationContext], dict] | None = None,
    ) -> None:
        self.supports_idempotency_key = idempotent
        self.idempotent = idempotent
        self.default = default or (lambda inp, ctx: {})
        self.calls: list[tuple[str, dict, InvocationContext, datetime]] = []
        self.effects: dict[str, int] = defaultdict(int)
        self.effect_log: list[tuple[str, dict]] = []
        self._done: dict[str, InvocationResult] = {}
        self._script: dict[str, list[tuple]] = defaultdict(list)

    def on(self, step_key: str, *behaviours: tuple) -> FakeActor:
        self._script[step_key].extend(behaviours)
        return self

    def calls_for(self, step_key: str) -> list[tuple[str, dict, InvocationContext, datetime]]:
        return [c for c in self.calls if c[2].step_id == step_key]

    def effects_for(self, step_key: str) -> int:
        return sum(1 for k, _ in self.effect_log if k == step_key)

    def _effect(self, key: str, step_key: str, inp: dict) -> None:
        self.effects[key] += 1
        self.effect_log.append((step_key, inp))

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        inp = dict(input)
        self.calls.append((idempotency_key, inp, context, deadline))
        if self.idempotent and idempotency_key in self._done:
            return self._done[idempotency_key]
        script = self._script.get(context.step_id)
        behaviour = script.pop(0) if script else ("complete", self.default(inp, context))
        verb = behaviour[0]
        if verb == "complete":
            self._effect(idempotency_key, context.step_id, inp)
            result = InvocationResult.completed(behaviour[1])
        elif verb == "accept":
            self._effect(idempotency_key, context.step_id, inp)
            result = InvocationResult.accepted()
        elif verb == "fail":
            return InvocationResult.failed(behaviour[1], retryable=behaviour[2])
        elif verb == "block":
            return InvocationResult.blocked(behaviour[1])
        elif verb in ("lose_ack", "crash"):
            self._effect(idempotency_key, context.step_id, inp)
            self._done[idempotency_key] = InvocationResult.completed(behaviour[1])
            if verb == "crash":
                raise Crash("engine killed after the actor did the work")
            raise ConnectionError("ack lost")
        else:  # pragma: no cover - test bug
            raise AssertionError(f"unknown behaviour {behaviour!r}")
        self._done[idempotency_key] = result
        return result


# --------------------------------------------------------------------------- builders


def port(name: str, type: str = "any", required: bool = True) -> Port:
    return Port(name=name, type=type, required=required)


def step(
    id: str,
    kind: str = "code",
    *,
    inputs: tuple[Port, ...] = (),
    outputs: tuple[Port, ...] = (),
    placement: Placement | None = None,
    timeout_s: float | None = None,
    retry: RetryPolicy | None = None,
    config: dict | None = None,
    max_iterations: int | None = None,
    body: tuple[Step, ...] = (),
    enabled: bool = True,
) -> Step:
    return Step(
        id=id,
        kind=kind,
        inputs=inputs,
        outputs=outputs,
        placement=placement,
        timeout_s=timeout_s,
        retry=retry,
        config=config or {},
        max_iterations=max_iterations,
        body=body,
        enabled=enabled,
    )


def edge(source: str, source_port: str, target: str, target_port: str) -> Edge:
    return Edge(source=source, source_port=source_port, target=target, target_port=target_port)


def workflow(
    steps: tuple[Step, ...],
    edges: tuple[Edge, ...] = (),
    *,
    id: str = "wf",
    version: int = 1,
    inputs: tuple[Port, ...] = (),
    outputs: tuple[Output, ...] = (),
) -> Workflow:
    return Workflow(
        id=id,
        name=id,
        version=version,
        inputs=inputs,
        steps=steps,
        edges=edges,
        outputs=outputs,
    )


def rule(
    *,
    id: str = "r1",
    workflow_id: str | None = "wf",
    workflow_inputs: dict[str, str] | None = None,
    action: Action | None = None,
    version: int | None = None,
) -> Rule:
    ref = (
        WorkflowRef(id=workflow_id, version=version, inputs=workflow_inputs or {})
        if workflow_id
        else None
    )
    return Rule(
        id=id,
        name=id,
        trigger=Trigger(kind="manual"),
        workflow=ref,
        action=action or Action(kind="noop"),
    )


def ports_for(actor: FakeActor) -> dict[str, FakeActor]:
    """Route every step kind and the rule action to one fake actor."""
    return {"*": actor}


def enrol_online(store: Any, clock: Clock, *machines: Machine) -> None:
    """Enrol engine-node machines and give each a fresh heartbeat at ``clock()``."""
    for m in machines:
        enrol(store, m, apply=True)
        store.put(
            HEARTBEAT_COLLECTION,
            {
                "id": m.name,
                "machine": m.name,
                "ts": clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )


def machine(name: str, *caps: str) -> Machine:
    return Machine(name=name, capabilities=caps, roles=("engine_node",))


# ------------------------------------------------- audit scenarios (test_audit_lifecycle)


def _scenario_run_start(life: Any, store: Any) -> Callable[[], Any]:
    from culture_rules.engine.runs import Executor

    ex = Executor(store, "spark", ports_for(FakeActor()))
    return lambda: ex.start(rule(), workflow((step("a"),)), identity="alice")


def _scenario_run_cancel(life: Any, store: Any) -> Callable[[], Any]:
    from culture_rules.engine.runs import Containment, Executor

    run = Executor(store, "spark", ports_for(FakeActor())).start(rule(), workflow((step("a"),)))
    return lambda: Containment(store).cancel(run["id"], "alice")


def _scenario_pause(life: Any, store: Any) -> Callable[[], Any]:
    from culture_rules.engine.runs import Containment

    return lambda: Containment(store).pause("alice")


def _scenario_resume(life: Any, store: Any) -> Callable[[], Any]:
    from culture_rules.engine.runs import Containment

    Containment(store).pause("alice")
    return lambda: Containment(store).resume("alice")


def _scenario_drain(life: Any, store: Any) -> Callable[[], Any]:
    from culture_rules.engine.runs import Containment

    return lambda: Containment(store).drain("thor", "alice")


def _scenario_undrain(life: Any, store: Any) -> Callable[[], Any]:
    from culture_rules.engine.runs import Containment

    Containment(store).drain("thor", "alice")
    return lambda: Containment(store).undrain("thor", "alice")


RUN_AUDIT_SCENARIOS = {
    "runs.start": _scenario_run_start,
    "runs.cancel": _scenario_run_cancel,
    "engine.pause": _scenario_pause,
    "engine.resume": _scenario_resume,
    "machine.drain": _scenario_drain,
    "machine.undrain": _scenario_undrain,
}


def three_step_workflow_for_mongo() -> Workflow:
    """s1 -> s2 (human, accepted) -> s3, typed integer/boolean ports."""
    return workflow(
        (
            step("s1", outputs=(port("n", "integer"),)),
            step(
                "s2", "actor_task", inputs=(port("n", "integer"),), outputs=(port("ok", "boolean"),)
            ),
            step("s3", inputs=(port("ok", "boolean"),)),
        ),
        (edge("s1", "n", "s2", "n"), edge("s2", "ok", "s3", "ok")),
    )


def human_actor_for_mongo() -> FakeActor:
    a = FakeActor(default=lambda inp, ctx: {"n": 7} if ctx.step_id == "s1" else {})
    a.on("s2", ("accept",), ("accept",))
    return a
