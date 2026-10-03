"""Criterion 3: viewer cannot mutate, editor cannot purge or save inline scripts, admin can.

Every request resolves to a principal before any handler runs (401 otherwise), role failures
are 403, and the audit identity is the principal's identity.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.engine.audit import AUDIT_COLLECTION  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.server.conftest import rule_body, workflow_body  # noqa: E402


@pytest.fixture
def world():
    store = MemoryStore()
    calls: list = []

    def hook(store_, ask_id, answer, identity):
        calls.append(identity)
        return {"ok": True}

    client = TestClient(create_app(store, auth=AuthSettings(listener=LAN), answer_ask=hook))
    tokens = ServiceTokens(store)
    hdr = {
        role: {"Authorization": f"Bearer {tokens.issue('root', name=role, roles=[role]).token}"}
        for role in ("viewer", "editor", "admin")
    }
    return store, client, hdr, calls


def inline_workflow(id: str = "wf-inline") -> dict:
    body = workflow_body(id)
    body["steps"][0]["config"] = {"script": "echo hi", "interpreter": "sh"}
    return body


def test_no_credentials_is_401_before_any_handler_runs(world):
    store, client, _, calls = world
    before = len(store.find(AUDIT_COLLECTION))
    for method, path, body in [
        ("get", "/rules", None),
        ("get", "/health", None),
        ("post", "/rules", rule_body()),
        ("post", "/asks/a1/answer", {"answer": 1}),
        ("post", "/rules", {"not": "even valid"}),
        ("get", "/no/such/route", None),
    ]:
        r = client.request(method.upper(), path, json=body)
        assert r.status_code == 401, (method, path, r.text)
        err = r.json()["error"]
        assert err["code"] == "no_credentials"
    assert calls == [] and len(store.find(AUDIT_COLLECTION)) == before
    assert store.find("rules") == []


def test_bad_bearer_is_401(world):
    _, client, _, _ = world
    r = client.get("/rules", headers={"Authorization": "Bearer crt_x.y"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "bad_token"


def test_whoami_reports_the_principal(world):
    _, client, hdr, _ = world
    body = client.get("/whoami", headers=hdr["editor"]).json()
    assert body == {"identity": "editor", "kind": "service", "roles": ["editor"]}


def test_viewer_reads_but_cannot_mutate(world):
    store, client, hdr, calls = world
    v = hdr["viewer"]
    client.post("/rules", json=rule_body(), headers=hdr["editor"])
    reads = ("/rules", "/rules/r1", "/workflows", "/runs", "/controls", "/export", "/health")
    for path in (*reads, "/asks", "/machines/status", "/repos"):
        assert client.get(path, headers=v).status_code == 200, path
    for method, path, body in [
        ("POST", "/rules", rule_body("r2")),
        ("PUT", "/rules/r1", rule_body()),
        ("POST", "/rules/r1/disable", None),
        ("DELETE", "/rules/r1", None),
        ("POST", "/rules/r1/restore", None),
        ("POST", "/import", {"files": {}}),
        ("POST", "/export", {"repo": "any"}),
        ("POST", "/runs", {"rule_id": "r1"}),
        ("POST", "/asks/a1/answer", {"answer": 1}),
        ("POST", "/rules/r1/purge", {"apply": True}),
        ("POST", "/controls/pause", None),
        ("POST", "/service-tokens", {"name": "x", "roles": ["admin"]}),
    ]:
        r = client.request(method, path, json=body, headers=v)
        assert r.status_code == 403, (method, path, r.text)
        assert r.json()["error"]["code"] == "forbidden_role"
    assert calls == []
    entries = [e for e in store.find(AUDIT_COLLECTION) if not e["verb"].startswith("service_")]
    assert {e["identity"] for e in entries} == {"editor"}


def test_editor_mutates_but_cannot_purge_drain_or_issue_tokens(world):
    store, client, hdr, calls = world
    e = hdr["editor"]
    assert client.post("/rules", json=rule_body(), headers=e).status_code == 201
    assert client.put("/rules/r1", json=rule_body(name="x"), headers=e).status_code == 200
    assert client.post("/rules/r1/disable", headers=e).status_code == 200
    assert client.post("/workflows", json=workflow_body(), headers=e).status_code == 201
    run = client.post("/runs", json={"rule_id": "r1"}, headers=e)
    assert run.status_code == 201 and run.json()["started_by"] == "editor"
    assert client.post("/asks/a1/answer", json={"answer": 1}, headers=e).status_code == 200
    assert calls == ["editor"]
    assert client.delete("/rules/r1", headers=e).status_code == 200
    for method, path, body in [
        ("POST", "/rules/r1/purge", {"apply": True}),
        ("POST", "/machines/thor/drain", None),
        ("POST", "/controls/pause", None),
        ("POST", "/service-tokens", {"name": "x", "roles": ["admin"]}),
        ("GET", "/service-tokens", None),
    ]:
        r = client.request(method, path, json=body, headers=e)
        assert r.status_code == 403, (method, path, r.text)
    assert store.get("rules", "r1") is not None


def test_editor_cannot_save_inline_scripts_admin_can(world):
    store, client, hdr, _ = world
    r = client.post("/workflows", json=inline_workflow(), headers=hdr["editor"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "inline_script_admin_only"
    assert store.get("workflows", "wf-inline") is None
    client.post("/workflows", json=workflow_body("wf2"), headers=hdr["editor"])
    r = client.put("/workflows/wf2", json=inline_workflow("wf2"), headers=hdr["editor"])
    assert r.status_code == 403
    assert (
        client.post("/workflows", json=inline_workflow(), headers=hdr["admin"]).status_code == 201
    )
    import json

    files = {"workflows/wf3.json": json.dumps(inline_workflow("wf3"))}
    r = client.post("/import", json={"files": files, "apply": True}, headers=hdr["editor"])
    assert r.status_code == 403 and store.get("workflows", "wf3") is None


def test_nested_inline_script_in_a_loop_body_is_also_admin_only(world):
    _, client, hdr, _ = world
    body = workflow_body("wf-nested")
    inner = dict(body["steps"][0], id="inner", config={"script": "echo x"})
    body["steps"][0] = dict(body["steps"][0], kind="loop", body=[inner], max_iterations=2)
    r = client.post("/workflows", json=body, headers=hdr["editor"])
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "inline_script_admin_only"


def test_admin_can_purge_drain_pause_and_manage_tokens(world):
    store, client, hdr, _ = world
    a = hdr["admin"]
    client.post("/rules", json=rule_body(), headers=a)
    client.delete("/rules/r1", headers=a)
    dry = client.post("/rules/r1/purge", json={}, headers=a)
    assert dry.status_code == 200 and dry.json()["applied"] is False
    done = client.post("/rules/r1/purge", json={"apply": True}, headers=a)
    assert done.status_code == 200 and done.json()["applied"] is True
    assert store.get("rules", "r1") is None
    assert client.post("/machines/thor/drain", headers=a).status_code == 200
    assert client.post("/controls/pause", headers=a).status_code == 200

    issued = client.post(
        "/service-tokens", json={"name": "bot", "roles": ["viewer"], "kind": "agent"}, headers=a
    )
    assert issued.status_code == 201
    tok = issued.json()
    assert tok["token"].startswith("crt_") and tok["identity"] == "bot" and tok["kind"] == "agent"
    bot = {"Authorization": f"Bearer {tok['token']}"}
    assert client.get("/whoami", headers=bot).json()["kind"] == "agent"
    listed = client.get("/service-tokens", headers=a).json()["items"]
    assert "bot" in {t["identity"] for t in listed} and all("hash" not in t for t in listed)
    assert client.delete(f"/service-tokens/{tok['id']}", headers=a).status_code == 200
    assert client.get("/whoami", headers=bot).status_code == 401
    assert client.delete(f"/service-tokens/{tok['id']}", headers=a).status_code == 409
    bad = client.post("/service-tokens", json={"name": "x", "roles": ["god"]}, headers=a)
    assert bad.status_code == 422


def test_purge_of_a_live_item_is_409(world):
    _, client, hdr, _ = world
    client.post("/rules", json=rule_body(), headers=hdr["admin"])
    assert client.post(
        "/rules/r1/purge", json={"apply": True}, headers=hdr["admin"]
    ).status_code == (409)


def test_actor_saves_refuse_literal_secrets(world):
    store, client, hdr, _ = world
    actor = {"id": "bot", "name": "bot", "kind": "agent", "params": {"api_token": "hunter22"}}
    r = client.post("/actors", json=actor, headers=hdr["editor"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "secret_literal"
    assert "hunter22" not in r.text and store.get("actors", "bot") is None
    actor["params"]["api_token"] = "grant:BOT_TOKEN"
    assert client.post("/actors", json=actor, headers=hdr["editor"]).status_code == 201
    actor["params"]["api_token"] = "hunter22"
    assert client.put("/actors/bot", json=actor, headers=hdr["editor"]).status_code == 422


def test_admins_parameter_elevates_a_principal(world):
    store = MemoryStore()
    client = TestClient(create_app(store, admins=("ops",), auth=AuthSettings(listener=LAN)))
    tok = ServiceTokens(store).issue("root", name="ops", roles=["viewer"]).token
    r = client.post("/controls/pause", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200


def test_default_app_is_secure_dev_header_is_ignored():
    client = TestClient(create_app(MemoryStore()))
    r = client.post("/rules", json=rule_body(), headers={"X-Culture-Identity": "alice"})
    assert r.status_code == 401
