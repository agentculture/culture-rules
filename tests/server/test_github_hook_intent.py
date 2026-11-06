"""d21 phase 2 (E2): whether the PR is open.

Every PR-scoped event carries ``state`` (``open`` / ``closed``) with the PR facts. The
comment intent (``command`` / ``mention`` / ``mention_command``, the PR fixer's ``/fix`` and
stop triggers) was removed in R1: a comment carries none of them.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from culture_rules.apps.github import complete_pr_facts, pr_facts
from culture_rules.server.hooks import github as gh_hook
from culture_rules.store.memory import MemoryStore

SECRET = "hook-secret"
APP = {
    "id": "github-app",
    "name": "GitHub App",
    "kind": "app",
    "params": {
        "surface": "github",
        "events": [
            "github.comment.created",
            "github.review.submitted",
            "github.review_comment.created",
            "github.pr.synchronize",
        ],
        "self_identity": "rules-culture-dev[bot]",
        "connection": {"app_id": "111", "webhook_secret": "grant:HOOK", "repos": ["o/r"]},
    },
}

# The three real bodies that started runs live (lobes-cli#302, culture-rules-tester#4).
QODO_BILLING = (
    "<!-- qodo:billing-blocked -->\n\n**ⓘ Qodo reviews are paused because the subscription is "
    "no longer active.** Ask your workspace admin to reactivate the subscription to resume "
    "reviews. [Manage billing](https://app.qodo.ai/account/billing/manage-subscription?"
    "traffic_source=pr_comment)"
)
STATUS_NOTE = (
    "The one failing check was markdown lint: `CHANGELOG.md:11` had a bare "
    "`*.tail<hex>.ts.net`, which markdownlint reads as an HTML tag (MD033). Wrapped it in "
    "backticks in feff2f5. The rules-culture-dev fixer timed out before reaching it. Qodo is "
    "paused for billing, so there are no review threads to answer. Sonar passed.\n\n"
    "- lobes (Claude)"
)
CLOSING = (
    "Closing: the d20 proof is complete (run run-0f45b49415bb: gate pass, codex-reviewer "
    "approved 0e9d3f3, App pushed it as rules-culture-dev[bot]). The remaining test-publish "
    "failure is a TestPyPI step the code cannot fix, so there's nothing left for the fixer "
    "here.\n\n- Claude"
)
REAL_BODIES = {"qodo_billing": QODO_BILLING, "status_note": STATUS_NOTE, "closing": CLOSING}


def test_pr_facts_carry_the_state():
    pr = {
        "state": "closed",
        "draft": False,
        "head": {"sha": "a" * 40, "ref": "f", "repo": {"full_name": "o/r"}},
        "base": {"sha": "b" * 40, "ref": "main", "repo": {"full_name": "o/r"}},
        "user": {"login": "u"},
    }
    assert pr_facts(pr)["state"] == "closed"
    assert complete_pr_facts(pr)["state"] == "closed"
    assert "state" not in pr_facts({**pr, "state": "weird"})
    # a missing state never blocks the other facts: a rule's state check fails closed
    without = {k: v for k, v in pr.items() if k != "state"}
    assert complete_pr_facts(without) is not None
    assert "state" not in complete_pr_facts(without)


def _deliver(store, event, payload, delivery, pull=None):
    body = json.dumps(payload).encode()
    headers = {
        "x-github-event": event,
        "x-github-delivery": delivery,
        "x-hub-signature-256": "sha256="
        + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(),
        "x-github-hook-installation-target-id": "111",
    }
    status, _ = gh_hook.handle(
        store, body=body, headers=headers, query={}, secrets=lambda ref: SECRET, pull=pull
    )
    assert status == 202, status
    (doc,) = [d for d in store.find("events") if d["envelope"]["data"]["delivery_id"] == delivery]
    return doc["envelope"]["data"]


def _pr(state="open"):
    return {
        "number": 7,
        "state": state,
        "draft": False,
        "head": {"sha": "a" * 40, "ref": "fix", "repo": {"full_name": "o/r"}},
        "base": {"sha": "b" * 40, "ref": "main", "repo": {"full_name": "o/r"}},
        "user": {"login": "someone"},
    }


def test_a_pr_comment_carries_the_prs_state_and_no_intent():
    store = MemoryStore()
    store.put("actors", dict(APP))
    payload = {
        "action": "created",
        "issue": {"number": 7, "title": "T", "html_url": "u", "pull_request": {}, "state": "open"},
        "comment": {"body": "/fix the lint @rules-culture-dev", "user": {"login": "OriNachum"}},
        "repository": {"full_name": "o/r"},
        "sender": {"login": "OriNachum"},
    }
    data = _deliver(store, "issue_comment", payload, "d-1", pull=lambda r, n: _pr("closed"))
    assert "command" not in data  # R1: no comment intent
    assert data["state"] == "closed"  # the PR as the App reads it


def test_a_review_and_a_review_comment_carry_the_prs_state():
    store = MemoryStore()
    store.put("actors", dict(APP))
    review = {
        "action": "submitted",
        "review": {"state": "commented", "body": "@rules-culture-dev fix these"},
        "pull_request": _pr(),
        "repository": {"full_name": "o/r"},
        "sender": {"login": "OriNachum"},
    }
    data = _deliver(store, "pull_request_review", review, "d-2")
    assert "mention" not in data  # R1: no comment intent
    assert data["state"] == "open"
    comment = {
        "action": "created",
        "comment": {"body": "/fix", "user": {"login": "OriNachum"}},
        "pull_request": _pr("closed"),
        "repository": {"full_name": "o/r"},
        "sender": {"login": "OriNachum"},
    }
    data = _deliver(store, "pull_request_review_comment", comment, "d-3")
    assert data["state"] == "closed"
    sync = {
        "action": "synchronize",
        "pull_request": _pr(),
        "repository": {"full_name": "o/r"},
        "sender": {"login": "OriNachum"},
    }
    data = _deliver(store, "pull_request", sync, "d-4")
    assert data["state"] == "open"
    assert "command" not in data
