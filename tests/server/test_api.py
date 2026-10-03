"""The HTTP API surface: routes, error envelope, lifecycle, runs, containment, import/export."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from culture_rules.engine.audit import AUDIT_COLLECTION
from culture_rules.server.app import create_app
from culture_rules.store.memory import MemoryStore
from tests.server.conftest import ALICE, rule_body, workflow_body


def test_health_stub(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_rule_crud_roundtrip(client):
    r = client.post("/rules", json=rule_body(), headers=ALICE)
    assert r.status_code == 201 and r.json()["id"] == "r1"
    assert client.get("/rules/r1").json()["name"] == "r1"
    assert [i["id"] for i in client.get("/rules").json()["items"]] == ["r1"]
    changed = rule_body(name="renamed")
    r = client.put("/rules/r1", json=changed, headers=ALICE)
    assert r.status_code == 200 and r.json()["name"] == "renamed"
    assert client.get("/rules/missing").status_code == 404


def test_error_envelope_for_invalid_and_conflict(client):
    bad = rule_body()
    bad["trigger"] = {"kind": "", "params": {}}
    r = client.post("/rules", json=bad)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid" and err["errors"]
    client.post("/rules", json=rule_body())
    assert client.post("/rules", json=rule_body()).status_code == 409


def test_rule_save_refuses_cycles_with_422(client):
    assert client.post("/rules", json=rule_body("a", must_after=["b"])).status_code == 201
    r = client.post("/rules", json=rule_body("b", must_after=["a"]))
    assert r.status_code == 422
    assert any(e["code"] == "predecessor_cycle" for e in r.json()["error"]["errors"])
    r = client.put("/rules/a", json=rule_body("a", must_after=["a"]))
    assert r.status_code == 422


def test_toggle_enabled_and_audit_identity(client, store):
    client.post("/rules", json=rule_body(), headers=ALICE)
    assert client.post("/rules/r1/disable", headers=ALICE).json()["enabled"] is False
    assert client.post("/rules/r1/enable", headers=ALICE).json()["enabled"] is True
    entries = store.find(AUDIT_COLLECTION)
    assert {e["identity"] for e in entries} == {"alice"}
    client.post("/rules/r1/disable")
    assert any(e["identity"] == "anonymous" for e in store.find(AUDIT_COLLECTION))


def test_soft_delete_and_restore(client):
    client.post("/rules", json=rule_body(), headers=ALICE)
    r = client.delete("/rules/r1", headers=ALICE)
    assert r.status_code == 200 and r.json()["deleted_by"] == "alice"
    assert client.get("/rules").json()["items"] == []
    assert len(client.get("/rules?include_deleted=true").json()["items"]) == 1
    assert client.delete("/rules/r1").status_code == 409
    assert client.post("/rules/r1/restore", headers=ALICE).json()["deleted_at"] is None
    assert client.post("/rules/r1/restore").status_code == 409


@pytest.mark.parametrize("kind", ["workflows", "actors", "machines"])
def test_other_kinds_have_the_same_routes(client, kind):
    bodies = {
        "workflows": workflow_body(),
        "actors": {"id": "bot", "name": "bot", "kind": "agent"},
        "machines": {"name": "thor", "roles": ["engine_node"]},
    }
    ident = {"workflows": "wf", "actors": "bot", "machines": "thor"}[kind]
    assert client.post(f"/{kind}", json=bodies[kind]).status_code == 201
    assert client.get(f"/{kind}/{ident}").status_code == 200
    assert client.post(f"/{kind}/{ident}/disable").json()["enabled"] is False
    assert client.delete(f"/{kind}/{ident}").status_code == 200
    assert client.post(f"/{kind}/{ident}/restore").status_code == 200


def test_runs_create_list_get_cancel(client):
    client.post("/rules", json=rule_body())
    r = client.post("/runs", json={"rule_id": "r1", "trigger": {}}, headers=ALICE)
    assert r.status_code == 201
    run = r.json()
    assert run["status"] == "running" and run["started_by"] == "alice"
    assert [x["id"] for x in client.get("/runs").json()["items"]] == [run["id"]]
    assert client.get(f"/runs/{run['id']}").json()["rule"]["id"] == "r1"
    c = client.post(f"/runs/{run['id']}/cancel", json={"reason": "no"}, headers=ALICE)
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert client.post(f"/runs/{run['id']}/cancel", json={}).status_code == 409
    assert client.get("/runs/nope").status_code == 404
    assert client.get("/runs?status=cancelled").json()["items"][0]["id"] == run["id"]


def test_run_for_unknown_rule_is_404(client):
    assert client.post("/runs", json={"rule_id": "zzz"}).status_code == 404


def test_pause_blocks_starts_and_resume_lifts(client):
    client.post("/rules", json=rule_body())
    assert client.get("/controls").json() == {"paused": False, "drained": []}
    assert client.post("/controls/pause", headers=ALICE).json()["paused"] is True
    assert client.post("/runs", json={"rule_id": "r1"}).status_code == 409
    assert client.post("/controls/pause").status_code == 409
    assert client.post("/controls/resume", headers=ALICE).json()["paused"] is False
    assert client.post("/runs", json={"rule_id": "r1"}).status_code == 201


def test_drain_and_undrain_machine(client):
    assert client.post("/machines/thor/drain", headers=ALICE).status_code == 200
    assert client.get("/controls").json()["drained"] == ["thor"]
    assert client.post("/machines/thor/undrain", headers=ALICE).status_code == 200
    assert client.get("/controls").json()["drained"] == []


def test_export_import_roundtrip_between_two_apps(client):
    import json

    client.post("/rules", json=rule_body())
    client.post("/workflows", json=workflow_body())
    exported = client.get("/export?format=json").json()
    assert "rules/r1.json" in exported["files"] and "workflows/wf.json" in exported["files"]
    other = TestClient(create_app(MemoryStore()))
    dry = other.post("/import", json={"files": exported["files"]}).json()
    assert dry["applied"] is False and {c["action"] for c in dry["changes"]} == {"add"}
    assert other.get("/rules").json()["items"] == []
    done = other.post("/import", json={"files": exported["files"], "apply": True}, headers=ALICE)
    assert done.json()["applied"] is True
    assert other.get("/rules/r1").status_code == 200
    json.dumps(done.json())


def test_import_with_rule_set_error_is_422(client):
    import json

    files = {
        "rules/a.json": json.dumps(rule_body("a", must_after=["b"])),
        "rules/b.json": json.dumps(rule_body("b", must_after=["a"])),
    }
    r = client.post("/import", json={"files": files, "apply": True})
    assert r.status_code == 422
    assert client.get("/rules").json()["items"] == []


def test_asks_answer_is_an_unimplemented_stub(client):
    r = client.post("/asks/a1/answer", json={"answer": "yes"}, headers=ALICE)
    assert r.status_code == 501 and r.json()["error"]["code"] == "not_implemented"


def test_asks_answer_hook_is_injectable(store):
    seen = []

    def hook(store_, ask_id, answer, identity):
        seen.append((ask_id, answer, identity))
        return {"ok": True}

    c = TestClient(create_app(store, answer_ask=hook))
    r = c.post("/asks/a1/answer", json={"answer": 3}, headers=ALICE)
    assert r.status_code == 200 and seen == [("a1", 3, "alice")]
