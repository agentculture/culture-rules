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
    "github.pr.synchronize",
    "github.pr.ready",
    "github.comment.created",
    "github.issue.opened",
    "github.review.submitted",
    "github.review_comment.created",
    "github.checks.suite_completed",
    "github.checks.workflow_completed",
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
    pr = {
        "number": 7,
        "title": "T",
        "html_url": "https://x/pr/7",
        "merged": False,
        "draft": False,
        "head": {
            "sha": "abc123",
            "ref": "feature-branch",
            "repo": {"full_name": "o/r"},
        },
        "base": {
            "sha": "def456",
            "ref": "main",
            "repo": {"full_name": "o/r"},
        },
        "user": {"login": "alice"},
    }
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
        (
            "pull_request",
            json.dumps(
                {
                    "action": "synchronize",
                    "pull_request": {
                        "number": 7,
                        "title": "T",
                        "html_url": "https://x/pr/7",
                        "merged": False,
                        "draft": True,
                        "head": {"sha": "abc123", "ref": "feat", "repo": {"full_name": "o/r"}},
                        "base": {"ref": "main", "repo": {"full_name": "o/r"}},
                        "user": {"login": "bob"},
                    },
                    "repository": {"full_name": "o/r"},
                    "sender": {"login": "bob"},
                }
            ).encode(),
            "github.pr.synchronize",
        ),
        (
            "pull_request",
            json.dumps(
                {
                    "action": "ready_for_review",
                    "pull_request": {
                        "number": 8,
                        "title": "U",
                        "html_url": "https://x/pr/8",
                        "merged": False,
                        "draft": False,
                        "head": {"sha": "def789", "ref": "release", "full_name": "o/r"},
                        "base": {"ref": "main", "full_name": "o/r"},
                        "user": {"login": "carol"},
                    },
                    "repository": {"full_name": "o/r"},
                    "sender": {"login": "carol"},
                }
            ).encode(),
            "github.pr.ready",
        ),
        (
            "check_suite",
            json.dumps(
                {
                    "action": "completed",
                    "check_suite": {
                        "app": {"slug": "ci-bot"},
                        "conclusion": "success",
                        "head_sha": "abc123",
                    },
                    "repository": {"full_name": "o/r"},
                    "sender": {"login": "ci-bot"},
                }
            ).encode(),
            "github.checks.suite_completed",
        ),
        (
            "workflow_run",
            json.dumps(
                {
                    "action": "completed",
                    "workflow_run": {
                        "conclusion": "failure",
                        "head_sha": "def456",
                    },
                    "repository": {"full_name": "o/r"},
                    "sender": {"login": "ci-bot"},
                }
            ).encode(),
            "github.checks.workflow_completed",
        ),
        (
            "pull_request_review_comment",
            json.dumps(
                {
                    "action": "created",
                    "pull_request": {"number": 7, "title": "T", "html_url": "https://x/pr/7"},
                    "comment": {
                        "id": 42,
                        "body": "look here",
                        "user": {"login": "reviewer"},
                    },
                    "repository": {"full_name": "o/r"},
                    "sender": {"login": "other-user"},
                }
            ).encode(),
            "github.review_comment.created",
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


def test_pull_request_data_enrichment():
    """Every PR event carries head_sha, head_branch, head_repo, base_repo, draft, pr_author."""
    s = make()
    pr_actions = ["opened", "closed", "reopened", "synchronize", "ready_for_review"]
    for i, action in enumerate(pr_actions):
        b = pr_body(action)
        assert post(s, b, hdrs(b, delivery=f"d{i}"))[0] == 202

    pr_events = [e for e in events(s) if e["envelope"]["type"].startswith("github.pr.")]
    assert len(pr_events) == len(pr_actions)
    for ev in pr_events:
        data = ev["envelope"]["data"]
        assert data["head_sha"] == "abc123"
        assert data["head_branch"] == "feature-branch"
        assert data["head_repo"] == "o/r"
        assert data["base_repo"] == "o/r"
        assert isinstance(data["draft"], bool)
        assert data["pr_author"] == "alice"


def test_pull_request_review_data_enrichment():
    """github.review.submitted and github.review_comment.created carry PR enrichment."""
    s = make()
    # review.submitted
    review_body = json.dumps(
        {
            "action": "submitted",
            "review": {"state": "approved"},
            "pull_request": {
                "number": 7,
                "title": "T",
                "html_url": "https://x/pr/7",
                "merged": False,
                "draft": True,
                "head": {"sha": "abc123", "ref": "feat", "repo": {"full_name": "o/r"}},
                "base": {"ref": "main", "repo": {"full_name": "o/r"}},
                "user": {"login": "alice"},
            },
            "repository": {"full_name": "o/r"},
            "sender": {"login": "bob"},
        }
    ).encode()
    assert (
        post(s, review_body, hdrs(review_body, event="pull_request_review", delivery="d-rv"))[0]
        == 202
    )
    # review_comment.created
    comment_body = json.dumps(
        {
            "action": "created",
            "pull_request": {
                "number": 7,
                "title": "T",
                "html_url": "https://x/pr/7",
                "merged": False,
                "draft": False,
                "head": {"sha": "def456", "ref": "fix", "repo": {"full_name": "o/r"}},
                "base": {"ref": "main", "repo": {"full_name": "o/r"}},
                "user": {"login": "carol"},
            },
            "comment": {"id": 42, "user": {"login": "reviewer-a"}},
            "repository": {"full_name": "o/r"},
            "sender": {"login": "other"},
        }
    ).encode()
    assert (
        post(
            s,
            comment_body,
            hdrs(comment_body, event="pull_request_review_comment", delivery="d-rc"),
        )[0]
        == 202
    )

    evs = events(s)
    rv = [e for e in evs if "github.review.submitted" in json.dumps(e)][0]
    rc = [e for e in evs if "github.review_comment.created" in json.dumps(e)][0]
    for ev in (rv, rc):
        data = ev["envelope"]["data"]
        assert isinstance(data["draft"], bool)
        assert "head_sha" in data
        assert "head_branch" in data
        assert "base_repo" in data
        assert "pr_author" in data
    assert rv["envelope"]["data"]["draft"] is True
    assert rc["envelope"]["data"]["author"] == "reviewer-a"


def test_check_suite_data_enrichment():
    """check_suite event extracts nested fields: head_sha, head_branch, pr_numbers, etc."""
    s = make()
    body = json.dumps(
        {
            "action": "completed",
            "check_suite": {
                "app": {"slug": "ci-bot"},
                "conclusion": "success",
                "status": "completed",
                "head_sha": "abc123def",
                "head_branch": "feature/auth",
                "pull_requests": [
                    {"number": 42, "url": "https://github/o/r/pull/42"},
                    {"number": 55, "url": "https://github/o/r/pull/55"},
                ],
            },
            "repository": {"full_name": "o/r"},
            "sender": {"login": "ci-bot"},
        }
    ).encode()
    assert post(s, body, hdrs(body, event="check_suite"))[0] == 202
    (ev,) = [e for e in events(s) if e["envelope"]["type"] == "github.checks.suite_completed"]
    data = ev["envelope"]["data"]
    # Nested fields
    assert data["head_sha"] == "abc123def"
    assert data["head_branch"] == "feature/auth"
    assert data["pr_numbers"] == [42, 55]
    assert data["repository"] == "o/r"
    # check_suite-specific
    assert data["app_slug"] == "ci-bot"
    assert data["workflow_name"] is None
    assert data["status"] == "completed"
    assert data["conclusion"] == "success"
    # Legacy field (null when no top-level pull_request)
    assert data["number"] is None


def test_check_suite_empty_pull_requests_keeps_sha_and_branch():
    """When pull_requests is empty, head_sha and head_branch must still be present."""
    s = make()
    body = json.dumps(
        {
            "action": "completed",
            "check_suite": {
                "app": {"slug": "my-app"},
                "conclusion": "failure",
                "status": "completed",
                "head_sha": "deadbeef",
                "head_branch": "main",
                "pull_requests": [],
            },
            "repository": {"full_name": "o/r"},
            "sender": {"login": "ci-bot"},
        }
    ).encode()
    assert post(s, body, hdrs(body, event="check_suite"))[0] == 202
    (ev,) = events(s)
    data = ev["envelope"]["data"]
    assert data["head_sha"] == "deadbeef"
    assert data["head_branch"] == "main"
    assert data["pr_numbers"] == []


def test_workflow_run_data_enrichment():
    """workflow_run event extracts nested fields: head_sha, head_branch, pr_numbers, etc."""
    s = make()
    body = json.dumps(
        {
            "action": "completed",
            "workflow_run": {
                "id": 98765,
                "name": "CI Build",
                "conclusion": "failure",
                "status": "completed",
                "head_sha": "feedface",
                "head_branch": "develop",
                "pull_requests": [
                    {"number": 10, "url": "https://github/o/r/pull/10"},
                ],
            },
            "repository": {"full_name": "o/r"},
            "sender": {"login": "github-actions[bot]"},
        }
    ).encode()
    assert post(s, body, hdrs(body, event="workflow_run"))[0] == 202
    (ev,) = [e for e in events(s) if e["envelope"]["type"] == "github.checks.workflow_completed"]
    data = ev["envelope"]["data"]
    # Nested fields
    assert data["head_sha"] == "feedface"
    assert data["head_branch"] == "develop"
    assert data["pr_numbers"] == [10]
    assert data["repository"] == "o/r"
    # workflow_run-specific
    assert data["app_slug"] is None
    assert data["workflow_name"] == "CI Build"
    assert data["status"] == "completed"
    assert data["conclusion"] == "failure"
    # Legacy field (null when no top-level pull_request)
    assert data["number"] is None


def test_workflow_run_empty_pull_requests_keeps_sha_and_branch():
    """When pull_requests is empty, head_sha and head_branch must still be present."""
    s = make()
    body = json.dumps(
        {
            "action": "completed",
            "workflow_run": {
                "id": 11111,
                "name": "Deploy",
                "conclusion": "success",
                "status": "completed",
                "head_sha": "cafebabe",
                "head_branch": "main",
                "pull_requests": [],
            },
            "repository": {"full_name": "x/y"},
            "sender": {"login": "github-actions[bot]"},
        }
    ).encode()
    assert post(s, body, hdrs(body, event="workflow_run"))[0] == 202
    (ev,) = events(s)
    data = ev["envelope"]["data"]
    assert data["head_sha"] == "cafebabe"
    assert data["head_branch"] == "main"
    assert data["pr_numbers"] == []
    assert data["workflow_name"] == "Deploy"
    assert data["repository"] == "x/y"


def test_review_comment_author_from_comment_not_sender():
    s = make()
    body = json.dumps(
        {
            "action": "created",
            "pull_request": {"number": 1, "title": "T", "html_url": "https://x/p/1"},
            "comment": {"id": 42, "user": {"login": "reviewer-a"}},
            "repository": {"full_name": "o/r"},
            "sender": {"login": "different-user"},
        }
    ).encode()
    assert post(s, body, hdrs(body, event="pull_request_review_comment"))[0] == 202
    (ev,) = events(s)
    assert ev["envelope"]["data"]["author"] == "reviewer-a"
    assert ev["envelope"]["data"]["action"] == "created"


def test_self_authored_tagged():
    """Bot-authored opened -> self_authored true; bot-authored synchronize -> true."""
    s = make()
    # opened by bot: self_authored is True (non-exempt type)
    b_opened = pr_body("opened").replace(b'"alice"', b'"culture[bot]"')
    post(s, b_opened, hdrs(b_opened, delivery="d-opened"))
    # synchronize by bot: self_authored is true
    b_sync = pr_body("synchronize").replace(b'"alice"', b'"culture[bot]"')
    post(s, b_sync, hdrs(b_sync, delivery="d-sync"))
    # synchronize should have self_authored true
    ev_sync = [e for e in events(s) if "d-sync" in json.dumps(e)][0]
    assert ev_sync["envelope"]["data"]["self_authored"] is True
    # opened should also have self_authored true (non-exempt type)
    ev_opened = [e for e in events(s) if "d-opened" in json.dumps(e)][0]
    assert ev_opened["envelope"]["data"]["self_authored"] is True


def test_check_suite_self_authored_is_false_for_bot():
    """A check_suite completed by the App has self_authored false."""
    s = make()
    body = json.dumps(
        {
            "action": "completed",
            "check_suite": {"app": {"slug": "ci-bot"}, "conclusion": "success"},
            "repository": {"full_name": "o/r"},
            "sender": {"login": "culture[bot]"},
        }
    ).encode()
    assert post(s, body, hdrs(body, event="check_suite", delivery="d-cs"))[0] == 202
    (ev,) = events(s)
    assert ev["envelope"]["data"]["self_authored"] is False


def test_workflow_run_self_authored_is_false_for_bot():
    """A workflow_run completed by the App has self_authored false."""
    s = make()
    body = json.dumps(
        {
            "action": "completed",
            "workflow_run": {"conclusion": "success", "name": "CI"},
            "repository": {"full_name": "o/r"},
            "sender": {"login": "culture[bot]"},
        }
    ).encode()
    assert post(s, body, hdrs(body, event="workflow_run", delivery="d-wr"))[0] == 202
    (ev,) = events(s)
    assert ev["envelope"]["data"]["self_authored"] is False


def test_sync_self_authored_false_for_human_author():
    """Synchronize by a human user is tagged self_authored false explicitly (the attempt
    budget resets only on an explicit false, never on an absent tag)."""
    s = make()
    b_sync = pr_body("synchronize")  # author is "alice"
    post(s, b_sync, hdrs(b_sync, delivery="d-human"))
    (ev,) = events(s)
    assert ev["envelope"]["data"]["self_authored"] is False


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


def test_on_check_failure_answers_503_and_redelivery_settles():
    s = make()
    seen = []

    def on_check(data):
        seen.append(data["head_sha"])
        if len(seen) == 1:
            raise RuntimeError("arming blew up")

    def go(body, event, delivery):
        return gh.handle(
            s,
            body=body,
            headers=hdrs(body, event=event, delivery=delivery),
            query={},
            secrets=resolver({REF: KEY_A}),
            on_check=on_check,
        )

    suite = json.dumps(
        {
            "action": "completed",
            "check_suite": {"status": "completed", "head_sha": "abc123d", "app": {"slug": "x"}},
            "repository": {"full_name": "o/r"},
            "sender": {"login": "ci-bot"},
        }
    ).encode()
    assert go(suite, "check_suite", "d-1")[0] == 503  # stored, but arming failed: retry
    assert go(suite, "check_suite", "d-1")[0] == 200  # redelivery re-runs on_check, now fine
    go(pr_body(), "pull_request", "d-2")  # non-check events never call it
    assert seen == ["abc123d", "abc123d"]


# ---------------------------------------------------------------- d14: PR facts everywhere

PR_FIELDS = (
    "head_sha",
    "head_branch",
    "head_repo",
    "base_repo",
    "base_branch",
    "base_sha",
    "draft",
    "pr_author",
)


def full_pr(**over):
    pr = {
        "number": 7,
        "draft": False,
        "head": {"sha": "abc123", "ref": "feature-branch", "repo": {"full_name": "o/r"}},
        "base": {"sha": "def456", "ref": "main", "repo": {"full_name": "o/r"}},
        "user": {"login": "alice"},
    }
    pr.update(over)
    return pr


def comment_body(pr=True, number=7, author="bob"):
    issue = {"number": number, "title": "T", "html_url": f"https://x/pull/{number}"}
    if pr:
        issue["pull_request"] = {"url": f"https://api.github.com/repos/o/r/pulls/{number}"}
    return json.dumps(
        {
            "action": "created",
            "issue": issue,
            "comment": {"body": "please fix"},
            "repository": {"full_name": "o/r"},
            "sender": {"login": author},
        }
    ).encode()


class Pulls:
    """A fake read-only App PR lookup: ``(repo, number) -> PR document``."""

    def __init__(self, pr=None, fail=None):
        self.pr = pr if pr is not None else full_pr()
        self.fail = fail
        self.calls: list[tuple[str, int]] = []

    def __call__(self, repo, number):
        self.calls.append((repo, number))
        if self.fail is not None:
            raise self.fail
        return self.pr


def post_pull(store, body, headers, pull):
    return gh.handle(
        store, body=body, headers=headers, query={}, secrets=resolver({REF: KEY_A}), pull=pull
    )


def data_of(store, etype):
    return [e["envelope"]["data"] for e in events(store) if e["envelope"]["type"] == etype]


def test_pr_review_and_review_comment_events_carry_base_sha_and_base_branch():
    s = make()
    b = pr_body("synchronize")
    assert post(s, b, hdrs(b, delivery="d-pr"))[0] == 202
    for event, extra, delivery in (
        ("pull_request_review", {"review": {"state": "commented"}}, "d-rv"),
        ("pull_request_review_comment", {"comment": {"id": 1, "user": {"login": "x"}}}, "d-rc"),
    ):
        payload = {
            "action": "submitted" if event == "pull_request_review" else "created",
            "pull_request": {**full_pr(), "title": "T", "html_url": "https://x/pr/7"},
            "repository": {"full_name": "o/r"},
            "sender": {"login": "bob"},
            **extra,
        }
        body = json.dumps(payload).encode()
        assert post(s, body, hdrs(body, event=event, delivery=delivery))[0] == 202
    for etype in (
        "github.pr.synchronize",
        "github.review.submitted",
        "github.review_comment.created",
    ):
        (data,) = data_of(s, etype)
        assert data["base_sha"] == "def456", etype
        assert data["base_branch"] == "main", etype
        assert set(PR_FIELDS) <= set(data), etype


def test_pr_comment_is_enriched_from_the_app_lookup():
    s = make()
    pulls = Pulls(
        full_pr(draft=True, head={"sha": "h1", "ref": "fx", "repo": {"full_name": "f/r"}})
    )
    b = comment_body()
    assert post_pull(s, b, hdrs(b, event="issue_comment"), pulls)[0] == 202
    assert pulls.calls == [("o/r", 7)]
    (data,) = data_of(s, "github.comment.created")
    assert data["pr_enriched"] is True
    assert data["head_sha"] == "h1"
    assert data["head_branch"] == "fx"
    assert data["head_repo"] == "f/r"
    assert data["base_repo"] == "o/r"
    assert data["base_branch"] == "main"
    assert data["base_sha"] == "def456"
    assert data["draft"] is True
    assert data["pr_author"] == "alice"
    assert data["author"] == "bob"  # the commenter, not the PR author
    assert data["comment"] == "please fix"


def test_plain_issue_comment_is_not_looked_up():
    s = make()
    pulls = Pulls()
    b = comment_body(pr=False, number=3)
    assert post_pull(s, b, hdrs(b, event="issue_comment"), pulls)[0] == 202
    assert pulls.calls == []
    (data,) = data_of(s, "github.comment.created")
    assert "pr_enriched" not in data
    assert not set(PR_FIELDS) & set(data)


def test_failed_lookup_stores_comment_unenriched_and_later_events_flow():
    from culture_rules.apps.github import GitHubError

    s = make()
    pulls = Pulls(fail=GitHubError("network_error", retryable=True))
    b = comment_body()
    assert post_pull(s, b, hdrs(b, event="issue_comment", delivery="d-c1"), pulls)[0] == 202
    (data,) = data_of(s, "github.comment.created")
    assert data["pr_enriched"] is False
    assert not set(PR_FIELDS) & set(data)
    # an unexpected exception type is contained the same way
    pulls.fail = RuntimeError("boom")
    b2 = comment_body(number=8)
    assert post_pull(s, b2, hdrs(b2, event="issue_comment", delivery="d-c2"), pulls)[0] == 202
    assert [d["pr_enriched"] for d in data_of(s, "github.comment.created")] == [False, False]
    # and the next delivery of any kind still flows
    pr = pr_body()
    assert post_pull(s, pr, hdrs(pr, delivery="d-pr"), pulls)[0] == 202
    assert len(data_of(s, "github.pr.opened")) == 1


def test_bad_lookup_result_is_treated_as_a_failure():
    s = make()
    b = comment_body()
    assert post_pull(s, b, hdrs(b, event="issue_comment"), Pulls(pr=["not", "a", "pr"]))[0] == 202
    (data,) = data_of(s, "github.comment.created")
    assert data["pr_enriched"] is False


def test_redelivered_pr_comment_is_not_looked_up_again_and_keeps_stored_data():
    s = make()
    pulls = Pulls()
    b = comment_body()
    h = hdrs(b, event="issue_comment", delivery="d-same")
    assert post_pull(s, b, h, pulls)[0] == 202
    before = data_of(s, "github.comment.created")
    pulls.pr = full_pr(head={"sha": "moved", "ref": "fx", "repo": {"full_name": "o/r"}})
    assert post_pull(s, b, h, pulls) == (200, {"duplicate": True})
    assert pulls.calls == [("o/r", 7)]  # the redelivery did not look the PR up again
    assert data_of(s, "github.comment.created") == before


def test_pr_comment_lookup_skipped_when_the_sink_would_not_store_it():
    actor = app_actor()
    actor["params"]["events"] = [t for t in ALL_TYPES if t != "github.comment.created"]
    s = make(actor)
    pulls = Pulls()
    b = comment_body()
    assert post_pull(s, b, hdrs(b, event="issue_comment"), pulls)[0] == 202
    assert pulls.calls == []
    disabled = make(app_actor(enabled=False))
    post_pull(disabled, b, hdrs(b, event="issue_comment"), pulls)
    assert pulls.calls == []


def test_pr_comment_without_lookup_seam_is_marked_unenriched():
    s = make()
    b = comment_body()
    assert post(s, b, hdrs(b, event="issue_comment"))[0] == 202
    (data,) = data_of(s, "github.comment.created")
    assert data["pr_enriched"] is False


def test_router_passes_the_pull_seam():
    s = make()
    pulls = Pulls()
    app = FastAPI()
    app.include_router(gh.router(s, secrets=resolver({REF: KEY_A}), pull=pulls))
    b = comment_body()
    r = TestClient(app).post("/hooks/github", content=b, headers=hdrs(b, event="issue_comment"))
    assert r.status_code == 202
    assert pulls.calls == [("o/r", 7)]
    assert data_of(s, "github.comment.created")[0]["pr_enriched"] is True
