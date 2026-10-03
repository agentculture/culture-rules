"""Node health status (t22)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION
from culture_rules.ops.health import health_status
from culture_rules.store.memory import MemoryStore

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


def test_executor_lag_from_due_work():
    store = MemoryStore()
    _beat(store, "node-a", 1)
    store.insert(
        "runs",
        {
            "id": "r1",
            "status": "running",
            "history": [
                {
                    "rev": 1,
                    "at": "2026-01-01T11:59:00Z",
                    "host": "n",
                    "event": "start",
                    "step": None,
                }
            ],
            "steps": [{"key": "s1", "status": "pending"}],
        },
    )
    store.insert(
        "runs",
        {
            "id": "r2",
            "status": "running",
            "history": [],
            "steps": [
                {"key": "s", "status": "retry_wait", "next_attempt_at": "2026-01-01T11:59:50Z"}
            ],
        },
    )
    store.insert(
        "runs",
        {
            "id": "r3",
            "status": "succeeded",
            "history": [],
            "steps": [{"key": "s", "status": "pending"}],
        },
    )
    h = health_status(store, NOW, "node-a")
    assert h["executor"]["lag_s"] == 60.0
    assert h["executor"]["due_steps"] == 2
    assert h["status"] == "degraded"  # lag above threshold


def test_json_serialisable():
    import json

    store = MemoryStore()
    _beat(store, "n", 1)
    json.dumps(health_status(store, NOW, "n"))
