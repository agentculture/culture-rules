"""Workflow updates carry the revision they were read at (spec c34/h34, deviation d7).

The stored workflow carries a server-owned ``revision`` (store metadata, outside the model);
``PUT /workflows/{id}`` needs ``expected_revision`` equal to it, else 409
``revision_conflict`` naming the current revision, and nothing is written.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from culture_rules.actors.trusted import workflow_digest, workflow_digest_v2
from culture_rules.engine.audit import AUDIT_COLLECTION
from culture_rules.store.memory import MemoryStore
from tests.server.conftest import ALICE, dev_app, rule_body, workflow_body


def _put(client, body, expected, id="wf"):
    payload = dict(body)
    if expected is not ...:
        payload["expected_revision"] = expected
    return client.put(f"/workflows/{id}", json=payload, headers=ALICE)


def _renamed(name: str, id: str = "wf") -> dict:
    return {**workflow_body(id), "name": name}


def test_create_starts_at_revision_1_and_reads_return_it(client):
    created = client.post("/workflows", json=workflow_body(), headers=ALICE)
    assert created.status_code == 201
    assert created.json()["revision"] == 1
    assert client.get("/workflows/wf").json()["revision"] == 1
    assert client.get("/workflows").json()["items"][0]["revision"] == 1


def test_update_with_the_current_revision_writes_and_bumps(client):
    client.post("/workflows", json=workflow_body(), headers=ALICE)
    r = _put(client, _renamed("one"), 1)
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 2
    assert r.json()["name"] == "one"
    assert "expected_revision" not in r.json()
    stored = client.get("/workflows/wf").json()
    assert (stored["name"], stored["revision"]) == ("one", 2)


def test_stale_revision_is_409_naming_the_current_one_and_writes_nothing(client, store):
    client.post("/workflows", json=workflow_body(), headers=ALICE)
    assert _put(client, _renamed("one"), 1).status_code == 200
    before = store.get("workflows", "wf")
    audits = len(store.find(AUDIT_COLLECTION))
    r = _put(client, _renamed("stale"), 1)
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "revision_conflict"
    assert "revision 2" in err["message"]
    assert err["errors"] == [
        {
            "path": "expected_revision",
            "code": "revision_conflict",
            "message": err["errors"][0]["message"],
            "current_revision": 2,
        }
    ]
    assert store.get("workflows", "wf") == before
    assert len(store.find(AUDIT_COLLECTION)) == audits


def test_missing_expected_revision_is_409_and_writes_nothing(client, store):
    client.post("/workflows", json=workflow_body(), headers=ALICE)
    before = store.get("workflows", "wf")
    r = _put(client, _renamed("blind"), ...)
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "revision_conflict"
    assert err["errors"][0]["current_revision"] == 1
    assert store.get("workflows", "wf") == before


def test_expected_revision_must_be_a_non_negative_integer(client):
    client.post("/workflows", json=workflow_body(), headers=ALICE)
    for bad in ("1", True, -1, 1.5):
        r = _put(client, _renamed("x"), bad)
        assert r.status_code == 422, bad
        assert r.json()["error"]["errors"][0]["path"] == "expected_revision"


def test_two_clients_read_the_same_revision_first_wins_second_gets_409(store):
    a = TestClient(dev_app(store))
    b = TestClient(dev_app(store))
    a.post("/workflows", json=workflow_body(), headers=ALICE)
    _put(a, _renamed("r2"), 1)
    _put(a, _renamed("r3"), 2)
    seen_a = a.get("/workflows/wf").json()["revision"]
    seen_b = b.get("/workflows/wf").json()["revision"]
    assert seen_a == seen_b == 3
    assert _put(a, _renamed("from a"), seen_a).status_code == 200
    lost = _put(b, _renamed("from b"), seen_b)
    assert lost.status_code == 409
    assert lost.json()["error"]["errors"][0]["current_revision"] == 4
    assert store.get("workflows", "wf")["name"] == "from a"


def test_a_document_without_a_revision_reads_as_0(client, store):
    store.put("workflows", {"id": "wf", **{k: v for k, v in workflow_body().items()}})
    assert "revision" not in store.get("workflows", "wf")
    assert client.get("/workflows/wf").json().get("revision", 0) == 0
    assert _put(client, _renamed("x"), 1).status_code == 409
    r = _put(client, _renamed("x"), 0)
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 1


def test_a_client_cannot_write_the_revision_itself(client, store):
    client.post("/workflows", json=workflow_body(), headers=ALICE)
    r = _put(client, {**_renamed("x"), "revision": 99}, 1)
    assert r.status_code == 422
    assert store.get("workflows", "wf")["revision"] == 1
    assert client.post("/workflows", json={**workflow_body("w2"), "revision": 7}).status_code == 422


def test_every_other_workflow_write_bumps_the_revision(client):
    client.post("/workflows", json=workflow_body(), headers=ALICE)
    revs = [client.get("/workflows/wf").json()["revision"]]
    for path in ("disable", "enable"):
        assert client.post(f"/workflows/wf/{path}", headers=ALICE).status_code == 200
        revs.append(client.get("/workflows/wf").json()["revision"])
    assert client.delete("/workflows/wf", headers=ALICE).status_code == 200
    revs.append(client.get("/workflows/wf").json()["revision"])
    assert client.post("/workflows/wf/restore", headers=ALICE).status_code == 200
    revs.append(client.get("/workflows/wf").json()["revision"])
    assert revs == [1, 2, 3, 4, 5]


def test_rules_actors_and_machines_are_unchanged(client, store):
    assert client.post("/rules", json=rule_body(), headers=ALICE).status_code == 201
    r = client.put("/rules/r1", json=rule_body(name="renamed"), headers=ALICE)
    assert r.status_code == 200
    assert "revision" not in store.get("rules", "r1")
    assert client.post("/rules/r1/disable", headers=ALICE).status_code == 200
    assert "revision" not in store.get("rules", "r1")


def test_import_bumps_on_change_leaves_unchanged_alone_and_export_omits_it(client, store):
    client.post("/workflows", json=workflow_body(), headers=ALICE)
    _put(client, _renamed("edited"), 1)
    exported = client.get("/export?format=json").json()["files"]
    assert "revision" not in json.loads(exported["workflows/wf.json"])
    same = client.post("/import", json={"files": exported, "apply": True}, headers=ALICE).json()
    assert [c["action"] for c in same["changes"] if c["kind"] == "workflows"] == ["unchanged"]
    assert store.get("workflows", "wf")["revision"] == 2
    changed = {"workflows/wf.json": json.dumps({**_renamed("imported"), "version": 1})}
    plan = client.post("/import", json={"files": changed, "apply": True}, headers=ALICE).json()
    assert [c["action"] for c in plan["changes"]] == ["change"]
    stored = store.get("workflows", "wf")
    assert (stored["name"], stored["revision"]) == ("imported", 3)
    added = {"workflows/w2.json": json.dumps(workflow_body("w2"))}
    client.post("/import", json={"files": added, "apply": True}, headers=ALICE)
    assert store.get("workflows", "w2")["revision"] == 1


def test_import_never_takes_the_revision_from_the_bundle(client, store):
    client.post("/workflows", json=workflow_body(), headers=ALICE)
    files = {"workflows/wf.json": json.dumps({**_renamed("x"), "revision": 50})}
    r = client.post("/import", json={"files": files, "apply": True}, headers=ALICE)
    # the bundle reader is strict: an unknown field is a validation error, nothing written
    assert r.status_code == 422
    assert store.get("workflows", "wf")["revision"] == 1


def test_trust_digests_ignore_the_revision():
    store = MemoryStore()
    client = TestClient(dev_app(store))
    client.post("/workflows", json=workflow_body(), headers=ALICE)
    stored = store.get("workflows", "wf")
    assert stored["revision"] == 1
    without = {k: v for k, v in stored.items() if k != "revision"}
    assert workflow_digest(stored) == workflow_digest(without) is not None
    assert workflow_digest_v2(stored) == workflow_digest_v2(without) is not None
