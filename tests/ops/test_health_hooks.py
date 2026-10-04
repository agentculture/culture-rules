"""Health reports webhook delivery outcomes and Discord gateway state (t32)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets as pysecrets
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.apps.discord_gateway import GATEWAY_STATE_COLLECTION  # noqa: E402
from culture_rules.engine.named_lease import LEASES_COLLECTION  # noqa: E402
from culture_rules.ops.health import health_status  # noqa: E402
from culture_rules.server.hooks import github as gh  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.server.conftest import dev_app  # noqa: E402

REF = "grant:GH_HOOK"
KEY = pysecrets.token_hex(16)
OTHER = pysecrets.token_hex(16)
MARKER = "payload-marker-" + pysecrets.token_hex(6)
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
ALICE = {"X-Culture-Identity": "alice"}


def _actor(id="gh-app"):
    return {
        "id": id,
        "kind": "app",
        "enabled": True,
        "params": {
            "surface": "github",
            "events": ["github.pr.opened"],
            "connection": {"app_id": "111", "webhook_secret": REF},
        },
    }


def _body(title=MARKER):
    return json.dumps(
        {
            "action": "opened",
            "pull_request": {"number": 7, "title": title, "html_url": "https://x/7"},
            "repository": {"full_name": "o/r"},
            "sender": {"login": "alice"},
        }
    ).encode()


def _sig(body, key=KEY):
    return "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def _headers(body, delivery, key=KEY, event="pull_request"):
    return {
        "x-github-event": event,
        "x-github-delivery": delivery,
        "x-hub-signature-256": _sig(body, key),
        "x-github-hook-installation-target-id": "111",
        "content-type": "application/json",
    }


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def rig(monkeypatch, store):
    monkeypatch.setattr(gh, "resolve", lambda ref: KEY)
    store.insert("actors", _actor())
    return store, TestClient(dev_app(store))


def _hooks(client):
    r = client.get("/health", headers=ALICE)
    assert r.status_code == 200, r.text
    return r.json()["hooks"]


def test_health_counts_deliveries_per_surface_actor_and_outcome(rig):
    store, c = rig
    body = _body()
    assert c.post("/hooks/github", content=body, headers=_headers(body, "d1")).status_code == 202
    assert c.post("/hooks/github", content=body, headers=_headers(body, "d1")).status_code == 200
    ignored = _body()
    ign = _headers(ignored, "d2", event="pull_request")
    ign_body = json.dumps({"action": "labeled", "pull_request": {}}).encode()
    ign.update({"x-hub-signature-256": _sig(ign_body), "x-github-delivery": "d3"})
    c.post("/hooks/github", content=ign_body, headers=ign)  # unmapped action: no outcome
    bad = c.post("/hooks/github", content=body, headers=_headers(body, "d4", key=OTHER))
    assert bad.status_code == 401
    got = _hooks(c)
    assert got["github"]["gh-app"] == {"accepted": 1, "duplicate": 1}
    assert got["github"]["github"] == {"unauthorized": 1}


def test_unconfigured_node_reports_empty_hooks_and_gateways(store):
    out = health_status(store, NOW, "n1")
    assert out["hooks"] == {}
    assert out["gateways"] == []


def test_gateway_state_from_lease_and_state_doc(store):
    expires = (NOW + timedelta(seconds=20)).isoformat()
    store.put(
        LEASES_COLLECTION,
        {"id": "discord-gateway:disc", "holder": "spark", "epoch": 3, "expires_at": expires},
    )
    store.put(
        GATEWAY_STATE_COLLECTION,
        {"id": "disc", "connected": True, "last_event_at": "2026-01-01T11:59:00+00:00"},
    )
    store.put(LEASES_COLLECTION, {"id": "claim:other", "holder": "x"})
    out = health_status(store, NOW, "n1")
    assert out["gateways"] == [
        {
            "actor": "disc",
            "holder": "spark",
            "connected": True,
            "last_event_at": "2026-01-01T11:59:00+00:00",
            "lease_expires_at": expires,
        }
    ]


def test_expired_lease_is_not_held_and_never_connected(store):
    store.put(
        LEASES_COLLECTION,
        {
            "id": "discord-gateway:disc",
            "holder": "spark",
            "epoch": 1,
            "expires_at": (NOW - timedelta(seconds=5)).isoformat(),
        },
    )
    (gw,) = health_status(store, NOW, "n1")["gateways"]
    assert gw["holder"] is None
    assert gw["connected"] is False
    assert gw["last_event_at"] is None


def test_delivery_logs_carry_no_body_signature_or_secret(rig, caplog):
    store, c = rig
    body = _body()
    sig = _sig(body)
    with caplog.at_level(logging.DEBUG):
        c.post("/hooks/github", content=body, headers=_headers(body, "dlv-secret-id"))
        c.post("/hooks/github", content=body, headers=_headers(body, "dlv-secret-id", key=OTHER))
        c.get("/health", headers=ALICE)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert caplog.records
    for needle in (MARKER, sig, KEY, OTHER, "dlv-secret-id", body.decode()):
        assert needle not in text


def test_supervisor_persists_connected_and_last_event_time():
    from tests.apps.discord_fakes import eventually, message
    from tests.apps.test_discord_gateway import Rig, discord_actor

    r = Rig()
    try:
        r.store.put("actors", discord_actor())
        r.sup.tick()
        assert eventually(
            lambda: (r.store.get(GATEWAY_STATE_COLLECTION, "bot") or {}).get("connected")
        )
        r.hub.post(message(1))
        assert eventually(
            lambda: (r.store.get(GATEWAY_STATE_COLLECTION, "bot") or {}).get("last_event_at")
        )
        (gw,) = health_status(r.store, r.clock(), "n1")["gateways"]
        assert gw["actor"] == "bot"
        assert gw["holder"] == "engine@spark"
        assert gw["connected"] is True
    finally:
        r.sup.shutdown()
    assert r.store.get(GATEWAY_STATE_COLLECTION, "bot")["connected"] is False
