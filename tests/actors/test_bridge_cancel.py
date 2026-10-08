"""d21 phase 2 (E4): a bridge job whose step attempt is over is cancelled at the bridge.

Live finding: the engine never cancelled a bridge job when its step timed out, so an
orphaned Qwen session held the fixer's only seat for about 30 minutes. The cultureagent
bridge serves ``POST /v1/invocations/<id>/cancel`` (a cooperative SIGTERM, always 202).
Each node cycle, :func:`~culture_rules.actors.agent.cancel_orphans` sends it once for every
invocation the bridge accepted whose attempt is over - superseded by a newer attempt (a
timeout's retry), its step failed, timed out or was cancelled, its run finished, failed or
was cancelled - from the node where the actor lives (its bridge token is there).
"""

from __future__ import annotations

import pytest

from culture_rules.actors.agent import BRIDGE_INVOCATIONS, CANCEL_ATTEMPTS, cancel_orphans
from culture_rules.engine.runs import Containment, step_state
from culture_rules.model.common import RetryPolicy
from culture_rules.store.memory import MemoryStore
from tests.actors.test_bridge_agent import (
    FakeBridge,
    executor,
    make_actor,
    start,
)
from tests.engine.run_helpers import Clock


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


def cancels(bridge: FakeBridge) -> list[str]:
    return [r["url"] for r in bridge.requests if r["url"].endswith("/cancel")]


def sweep(store, clock, actor):
    return cancel_orphans(store, lambda actor_id: actor, clock=clock)


def test_a_live_attempt_is_left_alone(store, clock):
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge)
    ex = executor(store, clock, actor)
    start(ex)
    assert sweep(store, clock, actor) == 0
    assert cancels(bridge) == []


def test_a_timed_out_attempt_is_cancelled_once(store, clock):
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge)
    ex = executor(store, clock, actor)
    run_id = start(ex, retry=RetryPolicy(max_attempts=2, backoff_s=1.0))
    clock.advance(3601)  # past the attempt's deadline: it times out
    ex.run_until_idle()
    # the attempt is over (a bridge job is never retried blind: unknown outcome)
    assert step_state(ex.run(run_id), "fix")["status"] in ("retry_wait", "failed")
    assert sweep(store, clock, actor) == 1
    assert cancels(bridge) == ["http://127.0.0.1:8765/v1/invocations/inv-1/cancel"]
    req = next(r for r in bridge.requests if r["url"].endswith("/cancel"))
    assert req["method"] == "POST"
    assert req["headers"]["Authorization"] == "Bearer bridge-secret"
    assert sweep(store, clock, actor) == 0  # once
    assert len(cancels(bridge)) == 1


def test_an_attempt_superseded_by_a_newer_one_is_cancelled(store, clock):
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge)
    ex = executor(store, clock, actor)
    start(ex)
    (doc,) = store.find(BRIDGE_INVOCATIONS)
    # what a newer attempt's dispatch does to the old invocation (_expire_previous)
    store.update_if(BRIDGE_INVOCATIONS, doc["id"], {"status": "accepted"}, {"status": "expired"})
    assert sweep(store, clock, actor) == 1
    assert store.get(BRIDGE_INVOCATIONS, doc["id"])["cancel_reason"] == "superseded"


def test_a_step_that_failed_on_its_deadline_is_cancelled(store, clock):
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge)
    ex = executor(store, clock, actor)
    run_id = start(ex)  # no retry: the timeout fails the step and the run
    clock.advance(3601)
    ex.run_until_idle()
    assert ex.run(run_id)["status"] == "failed"
    assert sweep(store, clock, actor) == 1
    assert cancels(bridge) == ["http://127.0.0.1:8765/v1/invocations/inv-1/cancel"]


def test_a_cancelled_run_cancels_its_bridge_job(store, clock):
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge)
    ex = executor(store, clock, actor)
    run_id = start(ex)
    Containment(store, clock=clock).cancel(run_id, "ops@test")
    assert sweep(store, clock, actor) == 1
    (doc,) = store.find(BRIDGE_INVOCATIONS)
    assert doc["cancel_sent_at"]
    assert doc["cancel_attempts"] == 1


def test_a_failing_cancel_is_retried_a_bounded_number_of_times(store, clock):
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge)
    ex = executor(store, clock, actor)
    run_id = start(ex)
    Containment(store, clock=clock).cancel(run_id, "ops@test")
    bridge.answers = [(500, {"error": "boom"})] * (CANCEL_ATTEMPTS + 2)
    for _ in range(CANCEL_ATTEMPTS + 2):
        sweep(store, clock, actor)
    assert len(cancels(bridge)) == CANCEL_ATTEMPTS
    (doc,) = store.find(BRIDGE_INVOCATIONS)
    assert doc["cancel_attempts"] == CANCEL_ATTEMPTS
    assert not doc.get("cancel_sent_at")


def test_a_job_the_bridge_never_accepted_is_not_cancelled(store, clock):
    bridge = FakeBridge((503, {"error": "busy"}))
    actor = make_actor(store, clock, bridge)
    ex = executor(store, clock, actor)
    run_id = start(ex)
    Containment(store, clock=clock).cancel(run_id, "ops@test")
    assert sweep(store, clock, actor) == 0
    assert cancels(bridge) == []


def test_only_the_node_that_can_reach_the_actor_sends_it(store, clock):
    bridge = FakeBridge()
    actor = make_actor(store, clock, bridge)
    ex = executor(store, clock, actor)
    run_id = start(ex)
    Containment(store, clock=clock).cancel(run_id, "ops@test")
    assert cancel_orphans(store, lambda actor_id: None, clock=clock) == 0  # not this node's
    assert cancels(bridge) == []
    assert sweep(store, clock, actor) == 1


def test_the_node_cycle_cancels_only_the_jobs_of_actors_on_its_machine():
    from tests.node.test_node import Cluster

    c = Cluster("spark", "spark2")
    bridge = FakeBridge()
    for host in ("spark", "spark2"):
        c.nodes[host] = c.node(
            host,
            adapters={"agent": lambda actor: make_actor(c.base, c.clock, bridge)},
        )
    c.base.put(
        "actors",
        {"id": "qwen-fixer", "name": "q", "kind": "agent", "machine": "spark2", "params": {}},
    )
    c.base.put(
        BRIDGE_INVOCATIONS,
        {
            "id": "bri_x",
            "idempotency_key": "k",
            "run_id": "run-gone",
            "step_id": "fix",
            "attempt": 1,
            "actor": "qwen-fixer",
            "status": "accepted",
            "invocation_id": "inv-9",
        },
    )
    c.cycle("spark")
    assert cancels(bridge) == []  # not spark's actor: its token lives on spark2
    c.cycle("spark2")
    assert cancels(bridge) == ["http://127.0.0.1:8765/v1/invocations/inv-9/cancel"]
    assert c.base.get(BRIDGE_INVOCATIONS, "bri_x")["cancel_reason"] == "run_gone"
