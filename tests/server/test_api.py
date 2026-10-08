"""The HTTP API surface: routes, error envelope, lifecycle, runs, containment, import/export."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from culture_rules.engine.audit import AUDIT_COLLECTION
from culture_rules.store.memory import MemoryStore
from tests.server.conftest import ALICE, dev_app, rule_body, workflow_body


def test_health_reports_node_status(client):
    body = client.get("/health").json()
    assert body["status"] in ("ok", "degraded", "down")
    assert body["store"]["reachable"] is True
    assert "heartbeat" in body
    assert "executor" in body


def test_rule_crud_roundtrip(client):
    r = client.post("/rules", json=rule_body(), headers=ALICE)
    assert r.status_code == 201
    assert r.json()["id"] == "r1"
    assert client.get("/rules/r1").json()["name"] == "r1"
    assert [i["id"] for i in client.get("/rules").json()["items"]] == ["r1"]
    changed = rule_body(name="renamed")
    r = client.put("/rules/r1", json=changed, headers=ALICE)
    assert r.status_code == 200
    assert r.json()["name"] == "renamed"
    assert client.get("/rules/missing").status_code == 404


def test_error_envelope_for_invalid_and_conflict(client):
    bad = rule_body()
    bad["trigger"] = {"kind": "", "params": {}}
    r = client.post("/rules", json=bad)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid"
    assert err["errors"]
    client.post("/rules", json=rule_body())
    assert client.post("/rules", json=rule_body()).status_code == 409


def test_rule_save_refuses_cycles_with_422(client):
    assert client.post("/rules", json=rule_body("a")).status_code == 201
    assert client.post("/rules", json=rule_body("b", must_after=["a"])).status_code == 201
    r = client.put("/rules/a", json=rule_body("a", must_after=["b"]))
    assert r.status_code == 422
    assert any(e["code"] == "predecessor_cycle" for e in r.json()["error"]["errors"])
    r = client.put("/rules/a", json=rule_body("a", must_after=["a"]))
    assert r.status_code == 422


def test_rule_save_refuses_a_ref_that_cannot_resolve_with_422(client):
    body = rule_body("a")
    body["action"]["params"] = {"x": {"$ref": "secrets.token"}}
    r = client.post("/rules", json=body)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid"  # the same envelope as condition_invalid
    assert [(e["path"], e["code"]) for e in err["errors"]] == [
        ("action.params.x", "invalid_reference")
    ]
    body["action"]["params"] = {"x": {"$ref": "trigger.data.x"}, "y": {"$literal": "trigger.id"}}
    assert client.post("/rules", json=body).status_code == 201


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
    assert r.status_code == 200
    assert r.json()["deleted_by"] == "alice"
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
    assert run["status"] == "running"
    assert run["started_by"] == "alice"
    assert [x["id"] for x in client.get("/runs").json()["items"]] == [run["id"]]
    assert client.get(f"/runs/{run['id']}").json()["rule"]["id"] == "r1"
    c = client.post(f"/runs/{run['id']}/cancel", json={"reason": "no"}, headers=ALICE)
    assert c.status_code == 200
    assert c.json()["status"] == "cancelled"
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
    assert "rules/r1.json" in exported["files"]
    assert "workflows/wf.json" in exported["files"]
    other = TestClient(dev_app(MemoryStore()))
    dry = other.post("/import", json={"files": exported["files"]}).json()
    assert dry["applied"] is False
    assert {c["action"] for c in dry["changes"]} == {"add"}
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


def test_unknown_ask_is_404(client):
    r = client.post("/asks/a1/answer", json={"answer": "yes"}, headers=ALICE)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ask_not_found"


def test_asks_answer_hook_is_injectable(store):
    seen = []

    def hook(store_, ask_id, answer, identity):
        seen.append((ask_id, answer, identity))
        return {"ok": True}

    c = TestClient(dev_app(store, answer_ask=hook))
    r = c.post("/asks/a1/answer", json={"answer": 3}, headers=ALICE)
    assert r.status_code == 200
    assert seen == [("a1", 3, "alice")]


def test_delete_and_purge_of_a_referenced_rule_is_409_rule_referenced(client):
    assert client.post("/rules", json=rule_body("a"), headers=ALICE).status_code == 201
    assert (
        client.post("/rules", json=rule_body("b", must_after=["a"]), headers=ALICE).status_code
        == 201
    )
    r = client.delete("/rules/a", headers=ALICE)
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "rule_referenced"
    assert "b" in r.text
    assert client.get("/rules/a").json().get("deleted_at") is None
    # once the referrer is gone the delete goes through
    assert client.delete("/rules/b", headers=ALICE).status_code == 200
    assert client.delete("/rules/a", headers=ALICE).status_code == 200


def test_save_of_a_rule_with_unknown_predecessor_is_422(client):
    r = client.post("/rules", json=rule_body("b", must_after=["nope"]), headers=ALICE)
    assert r.status_code == 422
    assert any(e["code"] == "unknown_rule" for e in r.json()["error"]["errors"])


def _store_legacy_rule(store, id: str, **changes) -> None:
    """A rule stored before the save-time catalog checks existed."""
    store.put("rules", {"id": id, **rule_body(id, **changes)})


def test_stored_typeless_event_rule_is_still_a_neighbour(client, store):
    _store_legacy_rule(store, "old", trigger={"kind": "event", "params": {}})
    r = client.post("/rules", json=rule_body("new", may_after=["old"]), headers=ALICE)
    assert r.status_code == 201
    r = client.delete("/rules/old", headers=ALICE)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "rule_referenced"


def test_stored_rule_with_pre_catalog_action_runs_via_api(client, store):
    _store_legacy_rule(store, "old", action={"kind": "comment", "params": {"body": "hi"}})
    r = client.post("/runs", json={"rule_id": "old"}, headers=ALICE)
    assert r.status_code == 201


def test_saving_a_new_rule_with_unknown_action_kind_is_still_422(client):
    r = client.post("/rules", json=rule_body("new", action={"kind": "teleport"}), headers=ALICE)
    assert r.status_code == 422
    assert any(e["code"] == "action_kind_unknown" for e in r.json()["error"]["errors"])


def test_restore_refuses_an_enabled_variable_rule_while_an_old_node_is_online(store, client):
    """d7 holds on restore: an old node that came online since the delete must not be handed
    a live, enabled variable rule (it would read ``not(a in vars.x)`` as true)."""
    from datetime import UTC, datetime

    from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION

    store.put_variable("x", ["qodo"], updated_by="alice")
    cond = {"op": "not", "arg": {"op": "in", "value": {"field": "a"}, "items": {"var": "x"}}}
    assert (
        client.post("/rules", json=rule_body("v", condition=cond), headers=ALICE).status_code == 201
    )
    assert client.delete("/rules/v", headers=ALICE).status_code == 200
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.put(HEARTBEAT_COLLECTION, {"id": "orin", "machine": "orin", "ts": now})
    r = client.post("/rules/v/restore", headers=ALICE)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["errors"][0]["code"] == "variables_unsupported_nodes"
    assert store.get("rules", "v")["deleted_at"]
