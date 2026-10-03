"""POST /replay runs recorded events through matching over the API, with no side effects."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.events.ingest import EVENTS_COLLECTION, event_document  # noqa: E402
from tests.server.conftest import ALICE, dev_app, rule_body  # noqa: E402


def test_replay_reports_recorded_events_without_writing(store):
    client = TestClient(dev_app(store))
    assert client.post("/rules", json=rule_body(), headers=ALICE).status_code == 201
    for i in (1, 2):
        envelope = {"id": f"e{i}", "kind": "event", "type": "t", "data": {}}
        store.insert(EVENTS_COLLECTION, event_document(envelope, host="h"))
    runs_before = store.find("runs")
    r = client.post("/replay", json={"rule_id": "r1", "limit": 10}, headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["events"] == 2 and "would_fire" in body and body["actions_executed"] == 0
    assert store.find("runs") == runs_before


def test_replay_unknown_rule_is_422(store):
    client = TestClient(dev_app(store))
    r = client.post("/replay", json={"rule_id": "nope"}, headers=ALICE)
    assert r.status_code == 422 and r.json()["error"]["code"] == "replay_invalid"
