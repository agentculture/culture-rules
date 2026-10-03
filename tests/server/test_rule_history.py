"""h78 / c97: GET /rules/{id}/history - a rule's recent decisions plus runs, newest first."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.engine.decisions import RULE_DECISIONS, decision_key  # noqa: E402
from culture_rules.engine.runs import RUNS_COLLECTION  # noqa: E402
from culture_rules.server import events  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.server.conftest import rule_body  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _decision(rule_id: str, event_id: str, at: str, reason="superseded_by", by=("a",)) -> dict:
    return {
        "id": decision_key(rule_id, event_id),
        "rule_id": rule_id,
        "event_id": event_id,
        "reason": reason,
        "by": list(by),
        "message": f"superseded by {', '.join(by)}",
        "at": at,
        "host": "spark",
    }


def _run(id: str, rule_id: str, at: str, status="succeeded") -> dict:
    return {"id": id, "rule": {"id": rule_id}, "status": status, "created_at": at, "steps": []}


def test_history_merges_decisions_and_runs_newest_first(store, client):
    assert client.post("/rules", json=rule_body("b")).status_code == 201
    store.put(RUNS_COLLECTION, _run("run-1", "b", "2026-10-03T10:00:00+00:00"))
    store.put(RUNS_COLLECTION, _run("run-3", "b", "2026-10-03T12:00:00+00:00", "failed"))
    store.put(RUNS_COLLECTION, _run("run-x", "other", "2026-10-03T13:00:00+00:00"))
    store.put(RULE_DECISIONS, _decision("b", "evt_2", "2026-10-03T11:00:00+00:00"))
    store.put(RULE_DECISIONS, _decision("other", "evt_9", "2026-10-03T11:30:00+00:00"))

    resp = client.get("/rules/b/history")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    ids = [(i["kind"], i["event_id"] if i["kind"] == "decision" else i["id"]) for i in items]
    assert ids == [
        ("run", "run-3"),
        ("decision", "evt_2"),
        ("run", "run-1"),
    ]
    skip = items[1]
    assert skip["reason"] == "superseded_by"
    assert skip["by"] == ["a"]
    assert skip["message"] == "superseded by a"
    assert skip["host"] == "spark"
    assert items[0]["status"] == "failed"
    assert items[0]["at"] == "2026-10-03T12:00:00+00:00"


def test_history_respects_limit(store, client):
    client.post("/rules", json=rule_body("b"))
    for n in range(5):
        store.put(RULE_DECISIONS, _decision("b", f"evt_{n}", f"2026-10-03T1{n}:00:00+00:00"))
    items = client.get("/rules/b/history", params={"limit": 2}).json()["items"]
    assert [i["event_id"] for i in items] == ["evt_4", "evt_3"]


def test_history_of_an_unknown_rule_is_404(client):
    assert client.get("/rules/nope/history").status_code == 404


def test_viewer_can_read_history():
    store = MemoryStore()
    client = TestClient(create_app(store, auth=AuthSettings(listener=LAN)))
    tokens = ServiceTokens(store)
    hdr = {}
    for role in ("viewer", "editor"):
        token = tokens.issue("root", name=role, roles=[role]).token
        hdr[role] = {"Authorization": f"Bearer {token}"}
    assert client.post("/rules", json=rule_body("b"), headers=hdr["editor"]).status_code == 201
    assert client.get("/rules/b/history", headers=hdr["viewer"]).status_code == 200


def test_history_is_in_the_committed_contract():
    paths = json.loads((ROOT / "api" / "openapi.json").read_text(encoding="utf-8"))["paths"]
    assert "get" in paths["/rules/{id}/history"]


def test_live_feed_streams_decisions_asks_and_heartbeats():
    for name in (RULE_DECISIONS, "asks", "heartbeats"):
        assert name in events.STREAMABLE, name
