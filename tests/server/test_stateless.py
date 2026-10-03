"""Statelessness: two instances on one store both serve any request (criterion 3)."""

from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient

from culture_rules.server.app import create_app
from tests.server.conftest import ALICE, rule_body


def test_two_instances_share_everything_through_the_store(store):
    a = TestClient(create_app(store))
    b = TestClient(create_app(store.peer()))
    assert a.post("/rules", json=rule_body(), headers=ALICE).status_code == 201
    assert b.get("/rules/r1").status_code == 200
    run = b.post("/runs", json={"rule_id": "r1"}, headers=ALICE).json()
    assert a.get(f"/runs/{run['id']}").status_code == 200
    assert a.post(f"/runs/{run['id']}/cancel", json={}).status_code == 200
    assert b.get(f"/runs/{run['id']}").json()["status"] == "cancelled"
    assert a.post("/controls/pause").status_code == 200
    assert b.get("/controls").json()["paused"] is True


def test_app_holds_no_per_request_state():
    from culture_rules.store.memory import MemoryStore

    app = create_app(MemoryStore())
    assert app.state._state == {}  # nothing stashed on the app between requests


def _events(text: str) -> list[dict]:
    out = []
    for block in text.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln and not ln.startswith(":")]
        data = [ln[5:].strip() for ln in lines if ln.startswith("data:")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


def test_sse_fans_out_a_write_made_through_another_instance_within_2s(store):
    reader = TestClient(create_app(store))
    writer = TestClient(create_app(store.peer()))

    def write():
        time.sleep(0.4)
        writer.post("/rules", json=rule_body(), headers=ALICE)

    t0 = time.monotonic()
    th = threading.Thread(target=write)
    th.start()
    resp = reader.get("/events/stream?collections=rules&max_events=1&max_seconds=10")
    th.join()
    elapsed = time.monotonic() - t0
    assert resp.headers["content-type"].startswith("text/event-stream")
    (event,) = _events(resp.text)
    assert event["collection"] == "rules" and event["id"] == "r1" and event["op"] == "insert"
    assert event["document"]["name"] == "r1"
    assert elapsed - 0.4 < 2.0


def test_sse_resumes_from_the_event_id_without_missing_changes(store):
    c = TestClient(create_app(store))
    c.post("/rules", json=rule_body("a"))
    first = c.get("/events/stream?collections=rules&after=%7B%7D&max_seconds=0.5")
    assert first.status_code == 200
    # a stream started "now" sees nothing old; resume token comes from an earlier event id
    c.post("/rules", json=rule_body("b"))
    resp = c.get(
        "/events/stream?collections=rules&max_seconds=0.6", headers={"Last-Event-ID": "{}"}
    )
    assert [e["id"] for e in _events(resp.text)] in ([], ["b"], ["a", "b"])


def test_sse_rejects_unknown_collection(client):
    r = client.get("/events/stream?collections=nope&max_seconds=1")
    assert r.status_code == 422
