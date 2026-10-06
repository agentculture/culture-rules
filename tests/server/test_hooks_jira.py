"""Jira webhook receiver: auth, key extraction, refetch, typed events (bare FastAPI app)."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.apps.jira import JiraError  # noqa: E402
from culture_rules.events.ingest import EVENTS_COLLECTION  # noqa: E402
from culture_rules.server.hooks.jira import handle, router  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

SECRET = "hmac-" + "secret-value-123"
TOKEN = "url-" + "token-value-456"
VALUES = {"WH_SECRET": SECRET, "WH_TOKEN": TOKEN, "API": "api-" + "token-789"}
TYPES = ["jira.issue.created", "jira.issue.updated", "jira.comment.created"]


def resolver(ref):
    name = ref.split(":", 1)[1]
    return VALUES[name]


class FakeClient:
    def __init__(self, fail=None):
        self.fetched = []
        self.fail = fail

    def get_issue(self, key):
        self.fetched.append(key)
        if self.fail:
            raise self.fail
        return {
            "key": key,
            "fields": {
                "summary": "Broken thing",
                "status": {"name": "In Progress"},
                "assignee": {"accountId": "acc-9", "displayName": "Dee"},
                "project": {"key": key.split("-")[0]},
                "updated": "2026-10-04T10:00:00.000+0000",
            },
        }


def actor(id="jira-app", **conn):
    connection = {
        "site": "acme.atlassian.net",
        "email": "bot@example.test",
        "token": "grant:API",
        "webhook_secret": "grant:WH_SECRET",
        "webhook_token": "grant:WH_TOKEN",
    }
    connection.update(conn)
    return {
        "id": id,
        "kind": "app",
        "enabled": True,
        "params": {
            "surface": "jira",
            "events": TYPES,
            "self_identity": "bot-acct",
            "connection": connection,
        },
    }


def payload(event="jira:issue_updated", key="OPS-7", **extra):
    doc = {
        "webhookEvent": event,
        "timestamp": 1790000000000,
        "user": {"accountId": "acc-1"},
        "issue": {"key": key, "id": "10001"},
    }
    doc.update(extra)
    return json.dumps(doc).encode()


def sign(body, secret=SECRET):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def store():
    s = MemoryStore()
    s.insert("actors", actor())
    return s


@pytest.fixture
def fake():
    return FakeClient()


@pytest.fixture
def http(store, fake):
    app = FastAPI()
    app.include_router(router(store, secrets=resolver, client_factory=lambda conn, res: fake))
    return TestClient(app)


def events(store):
    """The stored envelopes (type, source, data)."""
    return [d["envelope"] for d in store.find(EVENTS_COLLECTION)]


def test_signed_issue_updated_refetches_and_writes_one_event(http, store, fake):
    body = payload()
    r = http.post("/hooks/jira", content=body, headers={"X-Hub-Signature": sign(body)})
    assert r.status_code == 202
    assert fake.fetched == ["OPS-7"]
    (ev,) = events(store)
    assert ev["type"] == "jira.issue.updated"
    data = ev["data"]
    assert data["key"] == "OPS-7"
    assert data["summary"] == "Broken thing"
    assert data["status"] == "In Progress"
    assert data["project"] == "OPS"
    assert data["assignee"] == "acc-9"
    assert data["url"] == "https://acme.atlassian.net/browse/OPS-7"


def test_url_token_path(http, store):
    r = http.post("/hooks/jira", params={"token": TOKEN}, content=payload())
    assert r.status_code == 202
    assert len(events(store)) == 1


@pytest.mark.parametrize("kind", ["bad_sig", "bad_token", "no_auth", "sig_without_secret"])
def test_bad_auth_is_401_and_writes_nothing(http, store, fake, kind):
    body = payload()
    if kind == "bad_sig":
        r = http.post("/hooks/jira", content=body, headers={"X-Hub-Signature": sign(body, "x")})
    elif kind == "bad_token":
        r = http.post("/hooks/jira", params={"token": "nope"}, content=body)
    elif kind == "no_auth":
        r = http.post("/hooks/jira", content=body)
    else:
        r = http.post("/hooks/jira", content=body, headers={"X-Hub-Signature": "garbage"})
    assert r.status_code == 401
    assert events(store) == []
    assert fake.fetched == []


def test_signature_is_checked_before_parsing(http):
    r = http.post("/hooks/jira", content=b"{not json", headers={"X-Hub-Signature": "sha256=00"})
    assert r.status_code == 401


def test_signed_but_unparseable_is_400(http):
    body = b"{not json"
    r = http.post("/hooks/jira", content=body, headers={"X-Hub-Signature": sign(body)})
    assert r.status_code == 400


def test_no_issue_key_is_400(http, store):
    body = json.dumps({"webhookEvent": "jira:issue_updated", "issue": {"key": "lower-1"}}).encode()
    r = http.post("/hooks/jira", content=body, headers={"X-Hub-Signature": sign(body)})
    assert r.status_code == 400
    assert events(store) == []


def test_unmapped_event_ignored_no_write_no_fetch(http, store, fake):
    body = payload(event="jira:issue_deleted")
    r = http.post("/hooks/jira", content=body, headers={"X-Hub-Signature": sign(body)})
    assert r.status_code == 200
    assert r.json() == {"ignored": True}
    assert events(store) == []
    assert fake.fetched == []


def test_comment_created_event(http, store):
    body = payload(
        event="comment_created",
        comment={"id": "55", "body": "x" * 5000, "author": {"accountId": "bot-acct"}},
    )
    r = http.post("/hooks/jira", content=body, headers={"X-Hub-Signature": sign(body)})
    assert r.status_code == 202
    (ev,) = events(store)
    assert ev["type"] == "jira.comment.created"
    assert ev["data"]["comment_id"] == "55"
    assert len(ev["data"]["comment_body"]) < 1000
    assert ev["data"]["self_authored"] is False


def test_redelivery_writes_one_event(http, store):
    body = payload()
    hdr = {"X-Hub-Signature": sign(body), "X-Atlassian-Webhook-Identifier": "wh-1"}
    assert http.post("/hooks/jira", content=body, headers=hdr).status_code == 202
    assert http.post("/hooks/jira", content=body, headers=hdr).status_code == 200
    assert len(events(store)) == 1


def test_redelivery_without_identifier_dedupes_on_content(http, store):
    body = payload()
    hdr = {"X-Hub-Signature": sign(body)}
    assert http.post("/hooks/jira", content=body, headers=hdr).status_code == 202
    assert http.post("/hooks/jira", content=body, headers=hdr).status_code == 200
    assert len(events(store)) == 1


def test_refetch_failure_is_502_and_writes_nothing(store):
    app = FastAPI()
    failing = FakeClient(fail=JiraError("http_503", retryable=True))
    app.include_router(router(store, secrets=resolver, client_factory=lambda c, r: failing))
    body = payload()
    r = TestClient(app).post("/hooks/jira", content=body, headers={"X-Hub-Signature": sign(body)})
    assert r.status_code == 502
    assert r.json()["retryable"] is True
    assert events(store) == []


def test_multiple_actors_select_by_verifying_secret(fake):
    store = MemoryStore()
    store.insert("actors", actor("a", webhook_secret="grant:API"))
    store.insert("actors", actor("b"))
    body = payload()
    status, _ = handle(
        store,
        body=body,
        headers={"x-hub-signature": sign(body)},
        query={},
        secrets=resolver,
        client_factory=lambda c, r: fake,
    )
    assert status == 202
    (ev,) = events(store)
    assert ev["data"]["actor"] == "b"


def test_project_allow_list(store, fake):
    store.put("actors", actor(projects=["ABC"]))
    body = payload()
    status, out = handle(
        store,
        body=body,
        headers={"X-Hub-Signature": sign(body)},
        query={},
        secrets=resolver,
        client_factory=lambda c, r: fake,
    )
    assert status == 200
    assert out == {"ignored": True}
    assert fake.fetched == []


def test_disabled_actor_writes_nothing(store, fake):
    a = actor()
    a["enabled"] = False
    store.put("actors", a)
    body = payload()
    status, _ = handle(
        store,
        body=body,
        headers={"X-Hub-Signature": sign(body)},
        query={},
        secrets=resolver,
        client_factory=lambda c, r: fake,
    )
    assert status == 202
    assert events(store) == []


def test_oversize_body_is_413(http):
    r = http.post("/hooks/jira", content=b"x" * (2 * 1024 * 1024 + 1))
    assert r.status_code == 413


def test_no_secret_in_logs(http, caplog):
    caplog.set_level("DEBUG")
    body = payload()
    http.post("/hooks/jira", content=body, headers={"X-Hub-Signature": sign(body)})
    http.post("/hooks/jira", params={"token": TOKEN}, content=body)
    # the test client's own httpx request log is client-side; check the server-side loggers
    server = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("culture_rules"))
    for secret in (SECRET, TOKEN, VALUES["API"], "Broken thing"):
        assert secret not in server
