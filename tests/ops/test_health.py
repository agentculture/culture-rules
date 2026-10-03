"""Node health status (t22)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.claims import idempotency_key
from culture_rules.engine.runs import Executor
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION
from culture_rules.model.common import RetryPolicy
from culture_rules.ops.health import health_status
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import (
    Clock,
    FakeActor,
    edge,
    port,
    ports_for,
    rule,
    step,
    workflow,
)

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _beat(store, machine, age_s):
    ts = (NOW - timedelta(seconds=age_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.put(HEARTBEAT_COLLECTION, {"id": machine, "machine": machine, "ts": ts})


def test_healthy_node():
    store = MemoryStore()
    _beat(store, "node-a", 4)
    h = health_status(store, NOW, "node-a")
    assert h["status"] == "ok"
    assert h["host"] == "node-a"
    assert h["store"] == {"reachable": True, "schema_version": "1.0"}
    assert h["heartbeat"]["age_s"] == 4.0 and h["heartbeat"]["online"] is True
    assert h["executor"]["lag_s"] == 0.0


def test_stale_or_missing_heartbeat_is_degraded():
    store = MemoryStore()
    assert health_status(store, NOW, "node-a")["status"] == "degraded"
    assert health_status(store, NOW, "node-a")["heartbeat"]["age_s"] is None
    _beat(store, "node-a", 120)
    h = health_status(store, NOW, "node-a")
    assert h["status"] == "degraded" and h["heartbeat"]["online"] is False


def test_unreachable_store_is_down_and_never_raises():
    class Broken:
        node_schema_version = "1.0"

        def find(self, *a, **k):
            raise RuntimeError("connection refused")

        get = find

    h = health_status(Broken(), NOW, "node-a")
    assert h["status"] == "down"
    assert h["store"]["reachable"] is False
    assert "connection refused" in h["store"]["error"]


def _executor_rig(start=NOW):
    clock = Clock(start)
    store = MemoryStore(clock=clock)
    return clock, store


def _beat_now(store, clock, machine="node-a"):
    ts = clock().strftime("%Y-%m-%dT%H:%M:%SZ")
    store.put(HEARTBEAT_COLLECTION, {"id": machine, "machine": machine, "ts": ts})


def test_executor_lag_from_due_work():
    """Lag counts ready pending steps (aged from when they became ready) and due retries."""
    clock, store = _executor_rig()
    actor = FakeActor().on("a", ("accept",)).on("s", ("fail", "flaky", True))
    ex = Executor(store, "node-a", ports_for(actor), clock=clock)
    chain = workflow(
        (step("a", outputs=(port("ok", "boolean"),)), step("b", inputs=(port("ok", "boolean"),))),
        (edge("a", "ok", "b", "ok"),),
    )
    r1 = ex.start(rule(), chain)
    retried = workflow((step("s", retry=RetryPolicy(max_attempts=3, backoff_s=50)),), id="wf2")
    ex.start(rule(id="r2", workflow_id="wf2"), retried)
    ex.run_until_idle()  # a accepted (waiting), b pending but not ready; s -> retry_wait (+50 s)
    finished = ex.start(rule(id="r3", workflow_id=None))
    ex.run_until_idle()
    assert ex.run(finished["id"])["status"] == "succeeded"
    clock.advance(10)
    assert ex.deliver(idempotency_key(r1["id"], "a"), InvocationResult.completed({"ok": True}))
    clock.advance(60)  # b ready for 60 s; s due for 20 s; nothing has ticked since
    _beat_now(store, clock)
    h = health_status(store, clock(), "node-a")
    assert h["executor"]["lag_s"] == 60.0
    assert h["executor"]["due_steps"] == 2
    assert h["status"] == "degraded"  # lag above threshold


def test_pending_steps_waiting_on_dependencies_are_not_lag():
    """R1 repro: an accepted step answered hours later must not degrade health."""
    clock, store = _executor_rig()
    actor = FakeActor().on("h", ("accept",))
    ex = Executor(store, "node-a", ports_for(actor), clock=clock)
    wf = workflow(
        (
            step("h", "actor_task", timeout_s=86400, outputs=(port("ok", "boolean"),)),
            step("after", inputs=(port("ok", "boolean"),)),
        ),
        (edge("h", "ok", "after", "ok"),),
    )
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    states = {s["key"]: s["status"] for s in ex.run(run["id"])["steps"]}
    assert states == {"h": "waiting", "after": "pending"}
    clock.advance(120)
    _beat_now(store, clock)
    h = health_status(store, clock(), "node-a")
    assert h["executor"] == {"lag_s": 0.0, "due_steps": 0}
    assert h["status"] == "ok"


def test_ready_step_lag_is_measured_from_its_dependency_not_the_last_history_entry():
    clock, store = _executor_rig()
    actor = FakeActor().on("h1", ("accept",)).on("h2", ("accept",))
    ex = Executor(store, "node-a", ports_for(actor), clock=clock)
    wf = workflow(
        (
            step("h1", outputs=(port("ok", "boolean"),)),
            step("after1", inputs=(port("ok", "boolean"),)),
            step("h2"),
        ),
        (edge("h1", "ok", "after1", "ok"),),
    )
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    clock.advance(100)
    assert ex.deliver(idempotency_key(run["id"], "h1"), InvocationResult.completed({"ok": True}))
    clock.advance(30)  # an unrelated step changes later: the run's last history entry moves
    assert ex.deliver(idempotency_key(run["id"], "h2"), InvocationResult.completed({}))
    clock.advance(15)
    _beat_now(store, clock)
    h = health_status(store, clock(), "node-a")
    assert h["executor"] == {"lag_s": 45.0, "due_steps": 1}
    assert h["status"] == "degraded"


def test_only_live_runs_are_scanned():
    clock, store = _executor_rig()
    actor = FakeActor().on("a", ("fail", "boom", False))
    ex = Executor(store, "node-a", ports_for(actor), clock=clock)
    run = ex.start(rule(), workflow((step("a"), step("b"))))
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "failed"
    clock.advance(600)
    _beat_now(store, clock)
    assert health_status(store, clock(), "node-a")["executor"] == {"lag_s": 0.0, "due_steps": 0}


def test_json_serialisable():
    import json

    store = MemoryStore()
    _beat(store, "n", 1)
    json.dumps(health_status(store, NOW, "n"))
