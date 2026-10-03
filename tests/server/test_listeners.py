"""Criterion 2 over HTTP: two listeners built by serve.py; loopback honours Access headers,
the LAN listener ignores them and requires a service token. Config is all-or-nothing."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.access import AccessConfigError  # noqa: E402
from culture_rules.auth.resolve import ACCESS_HEADER  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.server import serve as serve_mod  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.auth.jwks import AUD, TEAM, jwks, wall_clock_token  # noqa: E402

ENV = {
    "CULTURE_RULES_ACCESS_LISTEN": "127.0.0.1:8766",
    "CULTURE_RULES_ACCESS_TEAM_DOMAIN": TEAM,
    "CULTURE_RULES_ACCESS_AUD": AUD,
    "CULTURE_RULES_EDITORS": "alice@example.com",
}


def by_name(listeners):
    return {lst.name: lst for lst in listeners}


def test_without_access_config_there_is_one_lan_listener():
    (lan,) = serve_mod.build_listeners(MemoryStore(), env={})
    assert (lan.name, lan.host, lan.port) == ("lan", "127.0.0.1", 8765)


def test_with_access_config_there_are_two_listeners():
    got = by_name(serve_mod.build_listeners(MemoryStore(), env=ENV, fetch_jwks=jwks))
    assert set(got) == {"lan", "loopback"}
    assert (got["loopback"].host, got["loopback"].port) == ("127.0.0.1", 8766)


@pytest.mark.parametrize("drop", sorted(k for k in ENV if k.startswith("CULTURE_RULES_ACCESS")))
def test_partial_access_config_is_refused(drop):
    env = {k: v for k, v in ENV.items() if k != drop}
    with pytest.raises(AccessConfigError):
        serve_mod.build_listeners(MemoryStore(), env=env)


def test_loopback_honours_access_jwt_lan_ignores_it_and_needs_a_token():
    store = MemoryStore()
    got = by_name(serve_mod.build_listeners(store, env=ENV, fetch_jwks=jwks))
    loop, lan = TestClient(got["loopback"].app), TestClient(got["lan"].app)
    sso = {ACCESS_HEADER: wall_clock_token()}

    me = loop.get("/whoami", headers=sso)
    assert me.status_code == 200
    assert me.json() == {"identity": "alice@example.com", "kind": "sso", "roles": ["editor"]}
    assert lan.get("/whoami", headers=sso).status_code == 401

    tok = ServiceTokens(store).issue("root", name="ci", roles=["viewer"]).token
    bearer = {"Authorization": f"Bearer {tok}"}
    assert lan.get("/whoami", headers=bearer).json()["identity"] == "ci"
    assert loop.get("/whoami", headers=bearer).json()["identity"] == "ci"


def test_forged_access_jwt_is_401_on_loopback():
    got = by_name(serve_mod.build_listeners(MemoryStore(), env=ENV, fetch_jwks=jwks))
    loop = TestClient(got["loopback"].app)
    head, body, sig = wall_clock_token().split(".")
    r = loop.get("/whoami", headers={ACCESS_HEADER: f"{head}.{body}.{sig[:-4]}AAAA"})
    assert r.status_code == 401 and r.json()["error"]["code"] in ("bad_signature", "malformed")


def test_insecure_dev_identity_is_opt_in_by_env():
    (lan,) = serve_mod.build_listeners(MemoryStore(), env={})
    c = TestClient(lan.app)
    assert c.get("/whoami", headers={"X-Culture-Identity": "dev"}).status_code == 401
    env = {"CULTURE_RULES_INSECURE_DEV_IDENTITY": "1"}
    (lan,) = serve_mod.build_listeners(MemoryStore(), env=env)
    me = TestClient(lan.app).get("/whoami", headers={"X-Culture-Identity": "dev"})
    assert me.status_code == 200 and me.json()["identity"] == "dev"


def test_serve_runs_one_uvicorn_server_per_listener(monkeypatch):
    seen = []
    monkeypatch.setattr(serve_mod, "_run_servers", lambda configs: seen.extend(configs))
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    serve_mod.serve(MemoryStore(), fetch_jwks=jwks)
    assert sorted((c.host, c.port) for c in seen) == [("127.0.0.1", 8765), ("127.0.0.1", 8766)]


def _health(listener, store):
    tok = ServiceTokens(store).issue("root", name="probe", roles=["viewer"]).token
    r = TestClient(listener.app).get("/health", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    return r.json()


def _health_host(listener, store):
    return _health(listener, store)["host"]


def test_health_reports_the_node_named_by_culture_rules_node_name():
    env = {**ENV, "CULTURE_RULES_NODE_NAME": "spark"}
    store = MemoryStore()
    got = by_name(serve_mod.build_listeners(store, env=env, fetch_jwks=jwks))
    assert _health_host(got["lan"], store) == "spark"
    assert _health_host(got["loopback"], store) == "spark"


def test_an_explicit_node_name_beats_the_environment():
    env = {"CULTURE_RULES_NODE_NAME": "from-env"}
    store = MemoryStore()
    (lan,) = serve_mod.build_listeners(store, env=env, node_name="spark")
    assert _health_host(lan, store) == "spark"


def test_without_a_node_name_health_uses_the_short_hostname(monkeypatch):
    monkeypatch.setattr("socket.gethostname", lambda: "spark-f8a9.lan")
    monkeypatch.delenv("CULTURE_RULES_NODE_NAME", raising=False)
    store = MemoryStore()
    (lan,) = serve_mod.build_listeners(store, env={})
    assert _health_host(lan, store) == "spark-f8a9"


def test_health_is_ok_when_the_named_node_beats():
    from datetime import UTC, datetime

    from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION

    store = MemoryStore()
    store.put(HEARTBEAT_COLLECTION, {"id": "spark", "ts": datetime.now(UTC).isoformat()})
    (lan,) = serve_mod.build_listeners(store, env={"CULTURE_RULES_NODE_NAME": "spark"})
    body = _health(lan, store)
    assert body["host"] == "spark" and body["heartbeat"]["online"] is True


def test_the_serve_verb_passes_node_name_through(monkeypatch):
    from culture_rules.cli import main

    seen = {}
    monkeypatch.setattr(serve_mod, "serve", lambda **kw: seen.update(kw))
    assert main(["serve", "--node-name", "spark", "--port", "9999"]) == 0
    assert seen["node_name"] == "spark" and seen["port"] == 9999
    assert main(["serve"]) == 0
    assert seen["node_name"] is None  # serve() then reads CULTURE_RULES_NODE_NAME
