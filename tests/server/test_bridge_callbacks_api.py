"""``POST /bridge-invocations/{id}/events`` (pr-fixer deviation d1): bridge callbacks land on
the API, authenticated only by the per-attempt callback token, and are recorded in the store
for the node to deliver. The exemption from principal resolution covers exactly this method
and path shape and nothing else.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.actors.agent import (  # noqa: E402
    BRIDGE_CALLBACK_PATH,
    BRIDGE_INVOCATIONS,
    BridgeAgentActor,
    bridge_invocation_id,
)
from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.engine.actorport import InvocationContext  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.actors.test_bridge_agent import PR, FakeBridge, completed_event  # noqa: E402
from tests.engine.run_helpers import T0, Clock  # noqa: E402


@pytest.fixture
def world():
    """A LAN-listener app and one accepted bridge invocation: (store, client, id, token)."""
    store, clock = MemoryStore(), Clock()
    bridge = FakeBridge()
    actor = BridgeAgentActor(
        store,
        bridge_url="http://127.0.0.1:8765",
        callback_url="http://127.0.0.1:8791",
        transport=bridge,
        clock=clock,
    )
    ctx = InvocationContext("r", "fix", "actor_task", "spark", 1, None, {})
    assert actor.invoke(PR, "k", T0 + timedelta(hours=1), context=ctx).outcome == "accepted"
    doc_id = bridge_invocation_id("k", 1)
    token = bridge.requests[0]["body"]["callback"]["token"]
    assert bridge.requests[0]["body"]["callback"]["url"] == (
        "http://127.0.0.1:8791" + BRIDGE_CALLBACK_PATH.format(id=doc_id)
    )
    client = TestClient(create_app(store, auth=AuthSettings(listener=LAN)))
    return store, client, doc_id, token


def post(client, doc_id, event, token=None, **kw):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post(
        BRIDGE_CALLBACK_PATH.format(id=doc_id),
        content=json.dumps(event).encode(),
        headers={"Content-Type": "application/json", **headers},
        **kw,
    )


def test_a_completed_callback_is_recorded_for_the_node(world):
    store, client, doc_id, token = world
    r = post(client, doc_id, completed_event(), token)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "recorded"}
    doc = store.get(BRIDGE_INVOCATIONS, doc_id)
    assert doc["status"] == "completed"
    assert doc["pending_delivery"] is True
    again = post(client, doc_id, completed_event(seq=4), token)
    assert (again.status_code, again.json()) == (200, {"status": "duplicate"})


def test_heartbeat_is_recorded(world):
    store, client, doc_id, token = world
    r = post(client, doc_id, {"event_id": "e", "sequence": 2, "kind": "heartbeat"}, token)
    assert r.status_code == 200
    assert store.get(BRIDGE_INVOCATIONS, doc_id)["last_sequence"] == 2


def test_bad_or_missing_token_is_401_and_records_nothing(world):
    store, client, doc_id, _ = world
    for token in (None, "wrong", ""):
        r = post(client, doc_id, completed_event(), token)
        assert r.status_code == 401, (token, r.text)
        assert r.json() == {"status": "unauthorized"}
    assert store.get(BRIDGE_INVOCATIONS, doc_id)["status"] == "accepted"


def test_a_service_token_is_not_a_callback_token(world):
    store, client, doc_id, _ = world
    issued = ServiceTokens(store).issue("root", name="admin", roles=["admin"]).token
    r = post(client, doc_id, completed_event(), issued)
    assert r.status_code == 401
    assert store.get(BRIDGE_INVOCATIONS, doc_id)["status"] == "accepted"


def test_unknown_and_expired_records(world):
    store, client, doc_id, token = world
    other = bridge_invocation_id("nope", 1)
    assert post(client, other, completed_event(), token).status_code == 404
    store.update_if(BRIDGE_INVOCATIONS, doc_id, {}, {"status": "expired"})
    r = post(client, doc_id, completed_event(), token)
    assert (r.status_code, r.json()) == (410, {"status": "expired"})


def test_invalid_body_is_400(world):
    _, client, doc_id, token = world
    r = client.post(
        BRIDGE_CALLBACK_PATH.format(id=doc_id),
        content=b"not json",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


def test_an_oversized_body_is_413(world):
    _, client, doc_id, token = world
    r = client.post(
        BRIDGE_CALLBACK_PATH.format(id=doc_id),
        content=b"x" * (2 << 20),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 413


def _is_auth_401(r) -> bool:
    if r.status_code != 401:
        return False
    if r.request.method == "HEAD":
        return True
    err = r.json().get("error")
    return isinstance(err, dict) and err.get("code") == "no_credentials"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/bridge-invocations/{id}/events"),
        ("PUT", "/bridge-invocations/{id}/events"),
        ("DELETE", "/bridge-invocations/{id}/events"),
        ("HEAD", "/bridge-invocations/{id}/events"),
        ("OPTIONS", "/bridge-invocations/{id}/events"),
        ("POST", "/bridge-invocations/{id}/events/"),
        ("POST", "/bridge-invocations/{id}/events/x"),
        ("POST", "/bridge-invocations/{id}"),
        ("POST", "/bridge-invocations/x/events"),
        ("POST", "/bridge-invocations/bri_short/events"),
        ("POST", "/bridge-invocations/BRI_{hex}/events"),
        ("POST", "/Bridge-invocations/{id}/events"),
        ("POST", "/bridge-invocations//{id}/events"),
        ("POST", "/bridge-invocations/{id}/eventsx"),
        ("POST", "/api/bridge-invocations/{id}/events"),
        ("POST", "/v1/bridge-invocations/{id}/events"),
        ("POST", "/bridge-invocations/{id}%2Fevents"),
        ("POST", "/runs"),
    ],
)
def test_near_misses_stay_behind_the_auth_middleware(world, method, path):
    _, client, doc_id, token = world
    concrete = path.format(id=doc_id, hex=doc_id[4:])
    r = client.request(method, concrete, headers={"Authorization": f"Bearer {token}"})
    # the callback token is not a credential anywhere else: the middleware refuses it
    assert r.status_code == 401, (method, concrete, r.status_code, r.text)
    if method != "HEAD":
        assert r.json().get("error", {}).get("code") in ("bad_token", "no_credentials"), r.text


def test_near_misses_without_a_credential_get_the_middleware_401(world):
    _, client, doc_id, _ = world
    for method in ("GET", "PUT", "DELETE"):
        assert _is_auth_401(client.request(method, BRIDGE_CALLBACK_PATH.format(id=doc_id)))
    assert _is_auth_401(client.post(f"/bridge-invocations/{doc_id}/events/"))
    assert _is_auth_401(client.post(f"/api/bridge-invocations/{doc_id}/events"))


def _raw_post(app, path: str, raw: bytes) -> int:
    out: dict = {}

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": raw,
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    return out["status"]


def test_an_encoded_raw_path_is_not_exempt(world):
    store, _, doc_id, _ = world
    app = create_app(store, auth=AuthSettings(listener=LAN))
    path = BRIDGE_CALLBACK_PATH.format(id=doc_id)
    encoded = path.replace("/events", "%2Fevents").encode()
    assert _raw_post(app, path, encoded) == 401  # middleware: no credential
    assert _raw_post(app, path, path.encode()) == 401  # handler: no callback token
    assert store.get(BRIDGE_INVOCATIONS, doc_id)["status"] == "accepted"
