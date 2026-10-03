"""API gaps the web tabs reported (t42): asks list, machine status, run filters/hosts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")

from culture_rules.actors.human import ASKS_COLLECTION  # noqa: E402
from culture_rules.engine.runs import RUNS_COLLECTION  # noqa: E402
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION  # noqa: E402


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ask(id: str, run_id: str, status: str = "open", at: str = "2026-10-03T10:00:00Z") -> dict:
    return {
        "id": id,
        "schema_version": 1,
        "idempotency_key": f"{run_id}:h",
        "run_id": run_id,
        "step_id": "h",
        "attempt": 1,
        "question": "Ship it?",
        "options": ["yes", "no"],
        "deadline": "2026-10-04T10:00:00Z",
        "status": status,
        "requested_emitted": True,
        "asked_at": at,
    }


# --------------------------------------------------------------------------- asks


def test_list_asks_filters_by_run_and_status(store, client):
    store.put(ASKS_COLLECTION, _ask("a2", "run-1", at="2026-10-03T11:00:00Z"))
    store.put(ASKS_COLLECTION, _ask("a1", "run-1"))
    store.put(ASKS_COLLECTION, _ask("a3", "run-1", status="answered"))
    store.put(ASKS_COLLECTION, _ask("b1", "run-2"))

    every = client.get("/asks").json()["items"]
    assert {a["id"] for a in every} == {"a1", "a2", "a3", "b1"}

    open_ = client.get("/asks", params={"run_id": "run-1", "status": "open"}).json()["items"]
    assert [a["id"] for a in open_] == ["a1", "a2"]  # oldest first
    first = open_[0]
    for key in ("run_id", "step_id", "question", "options", "deadline", "status", "asked_at"):
        assert key in first, key
    assert first["options"] == ["yes", "no"]
    assert "idempotency_key" not in first  # engine-internal fields stay internal


def test_list_asks_rejects_an_unknown_status(client):
    r = client.get("/asks", params={"status": "bogus"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid"


# --------------------------------------------------------------------------- runs


def _run(id: str, workflow: str, hosts: list, status: str = "succeeded", at: str = "") -> dict:
    return {
        "id": id,
        "status": status,
        "rev": 1,
        "rule": {"id": f"rule-{workflow}"},
        "workflow": {"id": workflow, "definition": {"id": workflow, "name": workflow.title()}},
        "started_by": "t",
        "created_at": at or "2026-10-03T10:00:00Z",
        "finished_at": None,
        "steps": [
            {"key": f"s{i}", "def": f"s{i}", "status": "succeeded", "host": h}
            for i, h in enumerate(hosts)
        ],
    }


def test_runs_filter_by_workflow_and_host_and_carry_hosts(store, client):
    store.put(RUNS_COLLECTION, _run("x1", "review", ["spark", "thor", "spark"], at="1"))
    store.put(RUNS_COLLECTION, _run("x2", "train", ["thor"], at="2"))
    store.put(RUNS_COLLECTION, _run("x3", "review", [None], status="running", at="3"))

    items = client.get("/runs", params={"workflow_id": "review"}).json()["items"]
    assert [r["id"] for r in items] == ["x3", "x1"]
    assert items[1]["hosts"] == ["spark", "thor"]  # unique, sorted, never null
    assert items[0]["hosts"] == []

    on_thor = client.get("/runs", params={"host": "thor"}).json()["items"]
    assert [r["id"] for r in on_thor] == ["x2", "x1"]
    both = client.get("/runs", params={"host": "thor", "workflow_id": "train"}).json()["items"]
    assert [r["id"] for r in both] == ["x2"]


# --------------------------------------------------------------------------- machines


def _machine(name: str, **extra) -> dict:
    return {"id": name, "name": name, "platform": "linux", "roles": ["engine_node"], **extra}


def test_machines_status_reports_liveness_load_running_and_queue(store, client):
    now = datetime.now(UTC)
    for name in ("spark", "orin", "thor"):
        store.put("machines", _machine(name))
    store.put(
        HEARTBEAT_COLLECTION,
        {
            "id": "spark",
            "machine": "spark",
            "ts": _iso(now - timedelta(seconds=5)),
            "load": {"cpu": 0.38, "mem": 0.612, "gpu": 0.22},
        },
    )
    store.put(
        HEARTBEAT_COLLECTION,
        {"id": "thor", "machine": "thor", "ts": _iso(now), "load": {"cpu": 1.7, "mem": 0.5}},
    )
    store.put(
        HEARTBEAT_COLLECTION,
        {"id": "orin", "machine": "orin", "ts": _iso(now - timedelta(hours=2)), "load": {}},
    )
    flow = {
        "id": "review",
        "name": "Review PR",
        "steps": [
            {"id": "fetch", "name": "Fetch diff", "kind": "code"},
            {"id": "review", "name": "Review", "kind": "ai", "placement": {"machine": "thor"}},
            {"id": "ask", "kind": "actor_task"},
        ],
    }
    run = {
        "id": "run-1",
        "status": "running",
        "rule": {"id": "r"},
        "workflow": {"id": "review", "definition": flow},
        "created_at": "2026-10-03T10:00:00Z",
        "steps": [
            {"key": "fetch", "def": "fetch", "status": "running", "host": "spark"},
            {"key": "review", "def": "review", "status": "pending", "host": None},
            {"key": "ask", "def": "ask", "status": "waiting", "host": "spark"},
        ],
    }
    store.put(RUNS_COLLECTION, run)
    # finished runs never count as running or queued
    done = {**run, "id": "run-0", "status": "succeeded"}
    store.put(RUNS_COLLECTION, done)

    body = client.get("/machines/status").json()
    by = {m["name"]: m for m in body["items"]}
    assert [m["name"] for m in body["items"]] == ["orin", "spark", "thor"]

    spark = by["spark"]
    assert spark["online"] is True
    assert spark["last_seen"] == _iso(now - timedelta(seconds=5))
    assert spark["load"] == {"cpu": 38.0, "gpu": 22.0, "mem": 61.2}  # percent 0-100
    assert spark["running"] == [
        {"step": "Fetch diff", "workflow": "Review PR", "run_id": "run-1"},
        {"step": "ask", "workflow": "Review PR", "run_id": "run-1"},
    ]
    assert spark["queue_depth"] == 0

    thor = by["thor"]
    assert thor["load"] == {"cpu": 100.0, "gpu": None, "mem": 50.0}  # clamped, gpu absent
    assert thor["running"] == [] and thor["queue_depth"] == 1  # the step placed on thor

    orin = by["orin"]
    assert orin["online"] is False and orin["load"] is None and orin["running"] == []


def test_machines_status_for_a_machine_that_never_beat(store, client):
    store.put("machines", _machine("new"))
    (m,) = client.get("/machines/status").json()["items"]
    assert m == {
        "name": "new",
        "online": False,
        "last_seen": None,
        "load": None,
        "running": [],
        "queue_depth": 0,
    }


def test_machines_status_is_not_shadowed_by_get_machine(store, client):
    store.put("machines", _machine("status"))
    assert "items" in client.get("/machines/status").json()
