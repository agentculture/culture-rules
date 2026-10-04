"""GitHub webhook receiver: HMAC check, dedupe, typed events, no inline rule evaluation."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets as pysecrets

from fastapi import FastAPI
from fastapi.testclient import TestClient

from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.server.hooks import github as gh
from culture_rules.store.memory import MemoryStore

REF = "grant:GH_HOOK"
KEY_A = pysecrets.token_hex(16)
KEY_B = pysecrets.token_hex(16)
ALL_TYPES = [
    "github.pr.opened",
    "github.pr.closed",
    "github.pr.reopened",
    "github.comment.created",
    "github.issue.opened",
    "github.review.submitted",
]


def app_actor(id="gh-app", app_id="111", ref=REF, enabled=True):
    return {
        "id": id,
        "kind": "app",
        "enabled": enabled,
        "params": {
            "surface": "github",
            "events": ALL_TYPES,
            "self_identity": "culture[bot]",
            "connection": {"app_id": app_id, "webhook_secret": ref},
        },
    }


def resolver(table):
    def _resolve(ref):
        return table[ref]

    return _resolve


def sign(body: bytes, secret: str = KEY_A) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def pr_body(action="opened", **over):
    pr = {"number": 7, "title": "T", "html_url": "https://x/pr/7", "merged": False}
    pr.update(over)
    return json.dumps(
        {
            "action": action,
            "pull_request": pr,
            "repository": {"full_name": "o/r"},
            "sender": {"login": "alice"},
        }
    ).encode()


def hdrs(body, event="pull_request", delivery="d-1", target="111", secret=KEY_A):
    h = {
        "x-github-event": event,
        "x-github-delivery": delivery,
        "x-hub-signature-256": sign(body, secret),
    }
    if target is not None:
        h["x-github-hook-installation-target-id"] = target
    return h


def make(*actors):
    s = MemoryStore()
    for a in actors or (app_actor(),):
        s.insert("actors", a)
    return s


def post(store, body, headers, secrets=None):
    return gh.handle(
        store,
        body=body,
        headers=headers,
        query={},
        secrets=secrets or resolver({REF: KEY_A}),
    )


def events(store):
    return store.find(EVENTS_COLLECTION)


def test_signed_pr_opened_writes_one_event():
    s = make()
    b = pr_body()
    status, out = post(s, b, hdrs(b))
    assert 200 <= status < 300
    evs = events(s)
    assert len(evs) == 1
    assert "github.pr.opened" in json.dumps(evs[0])
    data = json.dumps(evs[0])
    assert "o/r" in data
    assert "alice" in data
    assert "https://x/pr/7" in data


def test_unsigned_and_wrong_signature_401_nothing_written():
    s = make()
    b = pr_body()
    h = hdrs(b)
    del h["x-hub-signature-256"]
    assert post(s, b, h)[0] == 401
    assert post(s, b, hdrs(b, secret=KEY_B))[0] == 401
    h = hdrs(b)
    h["x-hub-signature-256"] = "sha1=" + "0" * 40
    assert post(s, b, h)[0] == 401
    assert events(s) == []


def test_unknown_app_target_and_resolver_failure_401():
    s = make()
    b = pr_body()
    assert post(s, b, hdrs(b, target="999"))[0] == 401

    def boom(ref):
        raise RuntimeError("nope")

    assert post(s, b, hdrs(b), secrets=boom)[0] == 401
    assert post(MemoryStore(), b, hdrs(b))[0] == 401
    assert events(s) == []


def test_missing_delivery_id_rejected_nothing_written():
    s = make()
    b = pr_body()
    h = hdrs(b)
    del h["x-github-delivery"]
    assert post(s, b, h)[0] in (400, 401)
    assert events(s) == []


def test_redelivery_yields_one_event():
    s = make()
    b = pr_body()
    assert post(s, b, hdrs(b))[0] == 202
    assert post(s, b, hdrs(b))[0] == 200
    assert len(events(s)) == 1


def test_ping_and_ignored_types():
    s = make()
    b = b"{}"
    assert post(s, b, hdrs(b, event="ping")) == (200, {"pong": True})
    assert post(s, b, hdrs(b, event="push")) == (200, {"ignored": True})
    b = pr_body("labeled")
    assert post(s, b, hdrs(b)) == (200, {"ignored": True})
    assert events(s) == []


def test_ping_still_requires_signature():
    s = make()
    b = b"{}"
    h = hdrs(b, event="ping", secret=KEY_B)
    assert post(s, b, h)[0] == 401


def test_event_mapping_and_data():
    s = make()
    cases = [
        ("pull_request", pr_body("closed", merged=True), "github.pr.closed"),
        ("pull_request", pr_body("reopened"), "github.pr.reopened"),
        (
            "issue_comment",
            json.dumps(
                {
                    "action": "created",
                    "issue": {"number": 3, "title": "I", "html_url": "https://x/i/3"},
                    "comment": {"body": "c" * 5000},
                    "repository": {"full_name": "o/r"},
                    "sender": {"login": "bob"},
                }
            ).encode(),
            "github.comment.created",
        ),
        (
            "issues",
            json.dumps(
                {
                    "action": "opened",
                    "issue": {"number": 4, "title": "I", "html_url": "https://x/i/4"},
                    "repository": {"full_name": "o/r"},
                    "sender": {"login": "bob"},
                }
            ).encode(),
            "github.issue.opened",
        ),
        (
            "pull_request_review",
            json.dumps(
                {
                    "action": "submitted",
                    "review": {"state": "approved"},
                    "pull_request": {"number": 7, "title": "T", "html_url": "https://x/pr/7"},
                    "repository": {"full_name": "o/r"},
                    "sender": {"login": "bob"},
                }
            ).encode(),
            "github.review.submitted",
        ),
    ]
    for i, (ev, b, typ) in enumerate(cases):
        assert post(s, b, hdrs(b, event=ev, delivery=f"d{i}"))[0] == 202
        stored = [e for e in events(s) if typ in json.dumps(e)]
        assert len(stored) == 1, typ
    blob = json.dumps(events(s))
    assert '"merged": true' in blob
    assert "c" * 5000 not in blob
    assert "approved" in blob


def test_self_authored_tagged():
    s = make()
    b = pr_body().replace(b'"alice"', b'"culture[bot]"')
    post(s, b, hdrs(b))
    assert "self_authored" in json.dumps(events(s)[0])


def test_multiple_app_actors_selected_by_target_id():
    other_ref = "grant:OTHER"
    s = make(app_actor("a1", "111"), app_actor("a2", "222", other_ref))
    table = {REF: KEY_A, other_ref: KEY_B}
    b = pr_body()
    assert post(s, b, hdrs(b, target="222", secret=KEY_B), resolver(table))[0] == 202
    evs = events(s)
    assert len(evs) == 1
    assert "app://a2" in json.dumps(evs[0])
    # a2's secret against a1's target id fails
    assert post(s, b, hdrs(b, delivery="x", target="111", secret=KEY_B), resolver(table))[0] == 401


def test_disabled_actor_does_not_leak_state():
    s = make(app_actor(enabled=False))
    b = pr_body()
    assert post(s, b, hdrs(b))[0] == 202
    assert events(s) == []


def test_no_rule_evaluation_in_request_path(monkeypatch):
    import culture_rules.engine.matching as matching
    import culture_rules.engine.runs as runs

    calls = []

    def spy(*a, **k):
        calls.append(1)
        raise AssertionError("rule evaluation in webhook path")

    monkeypatch.setattr(matching, "trigger_matches", spy)
    monkeypatch.setattr(matching, "match", spy)
    for name in ("start_run", "step", "advance"):
        if hasattr(runs, name):
            monkeypatch.setattr(runs, name, spy)
    s = make()
    b = pr_body()
    assert post(s, b, hdrs(b))[0] == 202
    assert calls == []
    src = open(gh.__file__, encoding="utf-8").read()
    assert "culture_rules.engine" not in src.split('"""', 2)[2]


def test_logs_carry_no_body_or_secret(caplog):
    caplog.set_level(logging.DEBUG)
    s = make()
    b = pr_body(title="KEY_A-TITLE-XYZ")
    post(s, b, hdrs(b))
    post(s, b, hdrs(b, secret=KEY_B, delivery="d-2"))
    text = caplog.text
    assert "KEY_A-TITLE-XYZ" not in text
    assert KEY_A not in text
    assert KEY_B not in text
    assert "sha256=" not in text


def client(store, secrets=None):
    app = FastAPI()
    app.include_router(gh.router(store, secrets=secrets or resolver({REF: KEY_A})))
    return TestClient(app)


def test_router_signed_and_unsigned():
    s = make()
    c = client(s)
    b = pr_body()
    r = c.post("/hooks/github", content=b, headers=hdrs(b))
    assert r.status_code == 202
    assert len(events(s)) == 1
    h = hdrs(b, delivery="d-9")
    h["x-hub-signature-256"] = "sha256=" + "0" * 64
    assert c.post("/hooks/github", content=b, headers=h).status_code == 401
    assert len(events(s)) == 1


def test_router_body_limit_413():
    s = make()
    c = client(s)
    b = b"x" * (gh.MAX_BODY_BYTES + 1)
    assert c.post("/hooks/github", content=b, headers=hdrs(b)).status_code == 413
    assert events(s) == []
