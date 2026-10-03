"""The noun groups against a real ``create_app(MemoryStore())`` (needs the ``server`` extra)."""

from __future__ import annotations

import json
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.cli import _api, main  # noqa: E402
from culture_rules.cli.verbs import REGISTRY  # noqa: E402
from culture_rules.client.http import ApiClient  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.server.conftest import rule_body, workflow_body  # noqa: E402

COLLECTIONS = ("rules", "workflows", "actors", "machines", "runs", "audit", "controls")


class Wire:
    """A transport onto an in-process app that records every request."""

    def __init__(self, store: MemoryStore):
        self.tc = TestClient(create_app(store), base_url="http://127.0.0.1:8765")
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers)))
        r = self.tc.request(method, url, headers=headers, content=body)
        return r.status_code, r.content

    def mutating(self):
        return [(m, u) for m, u, _ in self.calls if m != "GET"]


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def wire(store, monkeypatch):
    w = Wire(store)
    monkeypatch.setattr(
        _api, "make_client", lambda api_url=None: ApiClient("http://127.0.0.1:8765", transport=w)
    )
    monkeypatch.setenv("CULTURE_RULES_IDENTITY", "alice")
    return w


def snapshot(store):
    return {c: sorted(json.dumps(d, sort_keys=True) for d in store.find(c)) for c in COLLECTIONS}


def run(capsys, *argv):
    rc = main(list(argv))
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def jrun(capsys, *argv):
    rc, out, err = run(capsys, *argv, "--json")
    assert rc == 0, err
    return json.loads(out)


def write_body(tmp_path, body, name="body.json"):
    p = tmp_path / name
    p.write_text(json.dumps(body))
    return str(p)


# --------------------------------------------------------------------------- dry-run


def test_every_write_is_dry_run_without_apply(wire, store, capsys, tmp_path):
    wire.tc.post("/rules", json=rule_body("r1"), headers={"X-Culture-Identity": "alice"})
    wire.tc.post("/rules", json=rule_body("r2"), headers={"X-Culture-Identity": "alice"})
    wire.tc.delete("/rules/r2", headers={"X-Culture-Identity": "alice"})
    wire.tc.post("/workflows", json=workflow_body("wf"), headers={"X-Culture-Identity": "alice"})
    run_id = wire.tc.post(
        "/runs", json={"rule_id": "r1"}, headers={"X-Culture-Identity": "alice"}
    ).json()["id"]
    wire.calls.clear()
    before = snapshot(store)
    body = write_body(tmp_path, rule_body("r9"))
    updated = write_body(tmp_path, rule_body("r1", enabled=False), "u.json")
    bundle = tmp_path / "bundle"
    (bundle / "rules").mkdir(parents=True)
    (bundle / "rules" / "r7.json").write_text(json.dumps(rule_body("r7")))
    cases = [
        ["rules", "create", "--body", f"@{body}"],
        ["rules", "update", "r1", "--body", f"@{updated}"],
        ["rules", "enable", "r1"],
        ["rules", "disable", "r1"],
        ["rules", "delete", "r1"],
        ["rules", "restore", "r2"],
        ["rules", "run", "r1"],
        ["rules", "import", str(bundle)],
        ["workflows", "delete", "wf"],
        ["machines", "drain", "thor"],
        ["machines", "undrain", "thor"],
        ["runs", "cancel", run_id],
        ["runs", "pause"],
        ["runs", "resume"],
    ]
    for argv in cases:
        rc, out, err = run(capsys, *argv, "--json")
        assert rc == 0, (argv, err)
        payload = json.loads(out)
        assert payload["applied"] is False and payload["dry_run"] is True, argv
        assert snapshot(store) == before, argv
    # only GETs and the server's own non-mutating import plan ever went out
    assert [c for c in wire.mutating() if c != ("POST", "/import")] == []


def test_dry_run_is_the_default_for_every_mutating_verb_in_the_registry():
    for v in REGISTRY.verbs():
        if v.mutating:
            assert v.params_schema()["properties"]["apply"]["default"] is False


# --------------------------------------------------------------------------- applying


def test_rules_lifecycle_with_apply(wire, store, capsys, tmp_path):
    body = write_body(tmp_path, rule_body("r1"))
    out = jrun(capsys, "rules", "create", "--body", f"@{body}", "--apply")
    assert out["applied"] is True and out["result"]["id"] == "r1"
    assert [i["id"] for i in jrun(capsys, "rules", "list")["items"]] == ["r1"]
    assert jrun(capsys, "rules", "show", "r1")["id"] == "r1"

    upd = write_body(tmp_path, rule_body("r1", enabled=False), "u.json")
    jrun(capsys, "rules", "update", "r1", "--body", f"@{upd}", "--apply")
    assert store.get("rules", "r1")["enabled"] is False
    jrun(capsys, "rules", "enable", "r1", "--apply")
    assert store.get("rules", "r1")["enabled"] is True
    jrun(capsys, "rules", "disable", "r1", "--apply")
    assert store.get("rules", "r1")["enabled"] is False

    jrun(capsys, "rules", "delete", "r1", "--apply")
    assert jrun(capsys, "rules", "list")["items"] == []
    assert len(jrun(capsys, "rules", "list", "--include-deleted")["items"]) == 1
    jrun(capsys, "rules", "restore", "r1", "--apply")
    assert len(jrun(capsys, "rules", "list")["items"]) == 1


def test_run_a_rule_and_inspect_and_cancel_the_run(wire, capsys, tmp_path):
    jrun(capsys, "rules", "create", "--body", f"@{write_body(tmp_path, rule_body())}", "--apply")
    started = jrun(capsys, "rules", "run", "r1", "--trigger", '{"k": 1}', "--apply")
    run_id = started["result"]["id"]
    listed = jrun(capsys, "runs", "list")["items"]
    assert [r["id"] for r in listed] == [run_id]
    assert jrun(capsys, "runs", "show", run_id)["id"] == run_id
    cancelled = jrun(capsys, "runs", "cancel", run_id, "--reason", "no", "--apply")
    assert cancelled["result"]["status"] == "cancelled"


def test_pause_resume_and_drain_with_apply(wire, capsys):
    assert jrun(capsys, "runs", "pause", "--apply")["result"]["paused"] is True
    assert jrun(capsys, "runs", "resume", "--apply")["result"]["paused"] is False
    assert jrun(capsys, "machines", "drain", "thor", "--apply")["result"]["drained"] == ["thor"]
    assert jrun(capsys, "machines", "undrain", "thor", "--apply")["result"]["drained"] == []


def test_export_then_import_into_another_store(wire, capsys, tmp_path, monkeypatch):
    jrun(capsys, "rules", "create", "--body", f"@{write_body(tmp_path, rule_body())}", "--apply")
    exported = jrun(capsys, "rules", "export")
    assert list(exported["files"]) == ["rules/r1.json"]
    bundle = tmp_path / "out"
    (bundle / "rules").mkdir(parents=True)
    for rel, text in exported["files"].items():
        (bundle / rel).write_text(text)
    other = MemoryStore()
    w2 = Wire(other)
    monkeypatch.setattr(
        _api, "make_client", lambda api_url=None: ApiClient("http://x", transport=w2)
    )
    plan = jrun(capsys, "rules", "import", str(bundle))
    assert plan["applied"] is False and plan["result"]["changes"]
    assert other.find("rules") == []
    done = jrun(capsys, "rules", "import", str(bundle), "--apply")
    assert done["applied"] is True and other.get("rules", "r1")


def test_overview_for_every_noun(wire, capsys):
    for noun in ("rules", "workflows", "actors", "machines", "runs"):
        out = jrun(capsys, noun, "overview")
        assert out["subject"] == f"culture-rules {noun}" and out["sections"]
        rc, text, _ = run(capsys, noun, "overview")
        assert rc == 0 and f"# culture-rules {noun}" in text
    rc, text, _ = run(capsys, "rules")  # bare noun prints its overview
    assert rc == 0 and "# culture-rules rules" in text


def test_every_read_verb_supports_json_and_text(wire, capsys):
    wire.tc.post("/rules", json=rule_body(), headers={"X-Culture-Identity": "alice"})
    for argv in (
        ["rules", "list"],
        ["rules", "show", "r1"],
        ["runs", "list"],
        ["runs", "controls"],
    ):
        rc, out, _ = run(capsys, *argv)
        assert rc == 0 and out.strip(), argv
        rc, out, _ = run(capsys, *argv, "--json")
        assert rc == 0 and json.loads(out) is not None, argv


# --------------------------------------------------------------------------- errors


def test_api_error_envelope_becomes_a_user_error(wire, capsys):
    rc, out, err = run(capsys, "rules", "show", "nope", "--json")
    assert rc == 1 and out == ""
    payload = json.loads(err)
    assert payload["code"] == 1 and "nope" in payload["message"] and payload["remediation"]


def test_validation_errors_are_listed(wire, capsys):
    rc, out, err = run(capsys, "rules", "create", "--body", '{"id": "x"}', "--apply")
    assert rc == 1 and "error:" in err and out == ""


def test_unreachable_api_is_an_environment_error(monkeypatch, capsys):
    monkeypatch.delenv("CULTURE_RULES_API_URL", raising=False)
    rc, out, err = run(capsys, "rules", "list", "--api-url", "http://127.0.0.1:9", "--json")
    assert rc == 2 and json.loads(err)["code"] == 2


def test_bad_body_json_is_a_user_error(wire, capsys):
    rc, _, err = run(capsys, "rules", "create", "--body", "{not json")
    assert rc == 1 and "hint:" in err


# --------------------------------------------------------------------------- credentials


def test_bearer_token_from_env_is_sent(store, monkeypatch, capsys):
    w = Wire(store)
    monkeypatch.setenv("CULTURE_RULES_TOKEN", "tok-123")
    monkeypatch.delenv("CULTURE_RULES_API_URL", raising=False)
    client = _api.make_client()
    client._transport = w  # the real factory, a swapped transport
    assert client.base_url == "http://127.0.0.1:8765"
    client.request("GET", "/rules")
    assert w.calls[0][2]["Authorization"] == "Bearer tok-123"


def test_grant_reference_token_is_resolved_via_secrets(monkeypatch):
    from culture_rules.actors import secrets

    monkeypatch.setattr(secrets, "resolve", lambda ref, *a, **k: "resolved-" + ref)
    monkeypatch.setenv("CULTURE_RULES_TOKEN", "grant:API_TOKEN")
    client = _api.make_client()
    assert client.token == "resolved-grant:API_TOKEN"


def test_api_url_from_env(monkeypatch):
    monkeypatch.setenv("CULTURE_RULES_API_URL", "http://127.0.0.1:9999/")
    assert _api.make_client().base_url == "http://127.0.0.1:9999"
    assert _api.make_client("http://127.0.0.1:1234").base_url == "http://127.0.0.1:1234"


# --------------------------------------------------------------------------- real HTTP


def test_cli_over_a_real_http_listener(store, monkeypatch, capsys):
    uvicorn = pytest.importorskip("uvicorn")
    cfg = uvicorn.Config(create_app(store), host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    try:
        port = server.servers[0].sockets[0].getsockname()[1]
        monkeypatch.setenv("CULTURE_RULES_API_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("CULTURE_RULES_IDENTITY", "alice")
        body = json.dumps(rule_body())
        out = jrun(capsys, "rules", "create", "--body", body, "--apply")
        assert out["applied"] is True
        assert [i["id"] for i in jrun(capsys, "rules", "list")["items"]] == ["r1"]
    finally:
        server.should_exit = True
        thread.join(5)


# --------------------------------------------------------------------------- serve


def test_serve_verb_calls_the_server_entry_point(monkeypatch, capsys):
    calls = {}
    import culture_rules.server.serve as serve_mod

    monkeypatch.setattr(serve_mod, "serve", lambda **kw: calls.update(kw))
    rc, _, _ = run(capsys, "serve", "--host", "127.0.0.1", "--port", "9123")
    assert rc == 0 and calls["host"] == "127.0.0.1" and calls["port"] == 9123
