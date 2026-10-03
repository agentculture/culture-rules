"""Completions of accepted work free the actor's limit slot on every path (#6, E2).

``culture_rules.node.completions.deliver`` is the one deliver path: the node, the API's
ask answer and the node's redelivery of answered asks all use it, so a LimitedActor slot
is released however the completion arrives, and a key is remembered as done only when
the work completed.
"""

from __future__ import annotations

import pytest

from culture_rules.actors.human import ASKS_COLLECTION, HumanAdapter, answer_ask
from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.claims import idempotency_key
from culture_rules.engine.runs import step_state
from culture_rules.events.emit import Emitter
from culture_rules.model.actor import Actor
from culture_rules.model.common import RetryPolicy
from culture_rules.model.placement import Placement
from culture_rules.node.actors import ActorRouter
from culture_rules.node.completions import actor_of, release_slot
from tests.actors.test_human import FakeSink
from tests.engine.run_helpers import FakeActor, port, step, workflow
from tests.events.fakes import envelope
from tests.node.test_node import Cluster, event_rule


def usage(c: Cluster, actor: str) -> dict:
    return c.base.get("actor_usage", actor)


def accepting_cluster(retry: RetryPolicy | None = None) -> tuple[Cluster, FakeActor]:
    c = Cluster("spark")
    bot = Actor(id="bot", name="bot", kind="agent", machine="spark", params={"max_concurrency": 2})
    c.base.put("actors", bot.to_dict())
    accepting = FakeActor().on("s1", ("accept",), ("accept",))
    c.nodes["spark"] = c.node("spark", adapters={"agent": lambda actor: accepting})
    wf = workflow(
        (
            step(
                "s1", outputs=(port("n", "integer"),), placement=Placement(actor="bot"), retry=retry
            ),
        )
    )
    c.define(wf, event_rule("r", "wf"))
    c.start()
    return c, accepting


def waiting_key(c: Cluster, event: int) -> str:
    c.publish(envelope(event))
    c.cycle()
    run = c.run("r", f"evt_{event}")
    assert step_state(run, "s1")["status"] == "waiting"
    return idempotency_key(run["id"], "s1")


def test_deliver_marks_the_key_done_only_when_the_work_completed():
    c, _ = accepting_cluster(retry=RetryPolicy(max_attempts=1))
    node = c.nodes["spark"]
    failed_key = waiting_key(c, 1)
    assert node.deliver(failed_key, InvocationResult.failed("boom", retryable=True)) is True
    after_failure = usage(c, "bot")
    assert after_failure["inflight"] == []
    assert after_failure["done"] == []  # a retry of this key must be admitted like new work

    done_key = waiting_key(c, 2)
    assert node.deliver(done_key, InvocationResult.completed({"n": 1, "tokens": 4})) is True
    after_success = usage(c, "bot")
    assert after_success["inflight"] == []
    assert after_success["done"] == [done_key]
    assert after_success["tokens"] == 4


def test_router_release_passes_completed_through():
    c, _ = accepting_cluster()
    router: ActorRouter = c.nodes["spark"].router
    failed_key = waiting_key(c, 1)
    assert router.release("bot", failed_key, InvocationResult.failed("x")) is True
    assert usage(c, "bot")["done"] == []
    done_key = waiting_key(c, 2)
    assert router.release("bot", done_key, InvocationResult.completed({})) is True
    assert usage(c, "bot")["done"] == [done_key]


def test_release_slot_is_idempotent_and_counts_tokens_once():
    c, _ = accepting_cluster()
    key = waiting_key(c, 1)
    assert actor_of(c.base, key) == "bot"
    result = InvocationResult.completed({"n": 1, "tokens": 5})
    assert release_slot(c.base, key, result, clock=c.clock) is True
    assert release_slot(c.base, key, result, clock=c.clock) is True
    after = usage(c, "bot")
    assert after["tokens"] == 5
    assert after["done"] == [key]


def test_release_slot_ignores_keys_without_a_limited_actor():
    c = Cluster("spark")
    assert release_slot(c.base, "nope", InvocationResult.completed({})) is False


# --------------------------------------------------------------------------- E2: redelivery


class DeadEngine:
    """An executor that dies before recording the delivery (crash after the answer)."""

    def __init__(self, clock) -> None:
        self._clock = clock

    def deliver(self, key, result):
        raise RuntimeError("engine died between the answer and the delivery")


@pytest.fixture
def asking():
    c = Cluster("spark", "thor")
    alice = Actor(
        id="alice", name="alice", kind="human", machine="spark", params={"max_concurrency": 1}
    )
    c.base.put("actors", alice.to_dict())
    emitter = Emitter(FakeSink(), source="culture-rules/test")
    for host in ("spark", "thor"):
        c.nodes[host] = c.node(
            host, adapters={"human": lambda a: HumanAdapter(c.base, emitter, clock=c.clock)}
        )
    wf = workflow(
        (
            step(
                "s1",
                "actor_task",
                outputs=(port("answer", "any"),),
                placement=Placement(actor="alice"),
                config={"question": "Ship it?", "options": ["yes", "no"]},
            ),
        )
    )
    c.define(wf, event_rule("r", "wf"))
    c.start()
    c.publish(envelope(1))
    c.cycle()
    return c


def delivered_events(run: dict) -> list[dict]:
    return [h for h in run["history"] if h["event"].startswith("delivered:")]


def test_a_crash_between_answer_and_delivery_is_delivered_once_by_the_next_cycle(asking):
    c = asking
    (ask,) = c.base.find(ASKS_COLLECTION)
    with pytest.raises(RuntimeError):
        answer_ask(c.base, DeadEngine(c.clock), ask["id"], "yes", "alice")
    stored = c.base.get(ASKS_COLLECTION, ask["id"])
    assert (stored["status"], stored["delivered"]) == ("answered", False)
    assert step_state(c.run("r", "evt_1"), "s1")["status"] == "waiting"

    reports = c.cycle()  # both nodes redeliver; the claims/CAS model keeps it exactly once
    assert sum(r.redelivered for r in reports.values()) == 1
    run = c.run("r", "evt_1")
    assert run["status"] == "succeeded"
    assert step_state(run, "s1")["outputs"] == {"answer": "yes"}
    assert len(delivered_events(run)) == 1
    assert c.base.get(ASKS_COLLECTION, ask["id"])["delivered"] is True
    assert usage(c, "alice")["inflight"] == []

    again = c.cycle()
    assert sum(r.redelivered for r in again.values()) == 0
    assert len(delivered_events(c.run("r", "evt_1"))) == 1


def test_a_crash_after_delivery_but_before_the_flag_still_frees_the_slot(asking):
    c = asking
    (ask,) = c.base.find(ASKS_COLLECTION)
    node = c.nodes["spark"]
    # the run recorded the answer, but the process died before the slot and the flag
    with c.base.transaction() as tx:
        tx.update_if(
            ASKS_COLLECTION,
            ask["id"],
            {"status": "open"},
            {"status": "answered", "answer": "no", "delivered": False},
        )
    node.executor.deliver(ask["idempotency_key"], InvocationResult.completed({"answer": "no"}))
    assert len(usage(c, "alice")["inflight"]) == 1

    c.cycle()
    assert usage(c, "alice")["inflight"] == []
    run = c.run("r", "evt_1")
    assert len(delivered_events(run)) == 1
    assert run["status"] == "succeeded"
