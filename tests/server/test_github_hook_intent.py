"""d21 phase 2 (E1, E2): what a PR comment asks for, and whether the PR is open.

Live findings: every comment by a trusted author started a fixer run - Qodo's billing
notice, the operator's status note, a closing comment. A comment, review or review comment
now carries two scalar facts a rule can match against a shared variable:

* ``command`` - the first word of the body when the body starts with ``/`` (``/fix``),
  lowercased;
* ``mention`` - ``@<app slug>`` when the body mentions the App itself (its
  ``params.self_identity`` without ``[bot]``), outside quoted lines and code;

and every PR-scoped event carries ``state`` (``open`` / ``closed``) with the PR facts.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from culture_rules.apps.github import complete_pr_facts, pr_facts
from culture_rules.server.hooks import github as gh_hook
from culture_rules.server.hooks.github import comment_intent
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


def intent(body):
    return comment_intent(body, "rules-culture-dev[bot]")


@pytest.mark.parametrize("name", sorted(REAL_BODIES))
def test_the_three_real_bodies_ask_for_nothing(name):
    assert intent(REAL_BODIES[name]) == {}


@pytest.mark.parametrize(
    "body, expected",
    [
        ("/fix", {"command": "/fix"}),
        ("/fix please", {"command": "/fix"}),
        ("  \n\n/FIX\nthe lint", {"command": "/fix"}),
        ("    /fix", {"command": "/fix"}),  # leading whitespace is not a token
        ("/fixed it myself", {"command": "/fixed"}),  # a fact; the variable decides
        (
            "@rules-culture-dev fix the lint",
            {"mention": "@rules-culture-dev", "mention_command": "@rules-culture-dev fix"},
        ),
        ("@Rules-Culture-Dev, please", {"mention": "@rules-culture-dev"}),
        ("@rules-culture-dev: fix", {"mention": "@rules-culture-dev"}),
        (
            "@rules-culture-dev[bot] fix",
            {"mention": "@rules-culture-dev", "mention_command": "@rules-culture-dev fix"},
        ),
        ("\n@rules-culture-dev", {"mention": "@rules-culture-dev"}),
    ],
)
def test_the_first_token_is_the_ask(body, expected):
    assert intent(body) == expected


@pytest.mark.parametrize(
    "body",
    [
        "please /fix this",
        "can you look, @rules-culture-dev?",  # a mention mid-sentence never counts
        "/fix the lint @rules-culture-dev"[5:],
        "mail rules@rules-culture-dev.example",
        "@rules-culture-devx is another app",
        "@rules-culture-dev-staging is another app",
        "@rules-culture-dev.example is a host",
        "> /fix",  # a quote
        "> @rules-culture-dev please fix",
        "```\n/fix\n```",
        "~~~\n@rules-culture-dev\n~~~",
        "~~~~python\n@rules-culture-dev fix\n~~~~",
        "````\n```\n@rules-culture-dev\n```\n````",
        "text\n\n    @rules-culture-dev in an indented block\n",
        "`a\n@rules-culture-dev\nb` multiline inline code",
        "- item\n  ```\n  @rules-culture-dev\n  ```",  # a fence nested in a list
        "> quoted\n@rules-culture-dev lazy continuation",
        "see `@rules-culture-dev` here",
        "/",
        "@",
        "",
        "   \n  \t\n",
    ],
)
def test_nothing_but_a_leading_fix_or_mention_is_an_ask(body):
    assert intent(body) == {}


def test_no_self_identity_reads_no_mention_and_bodies_are_bounded():
    assert comment_intent("@rules-culture-dev fix", None) == {}
    assert comment_intent("/" + "x" * 100, "a[bot]") == {}  # not a command word
    assert comment_intent(None, "a[bot]") == {}
    assert intent("\n" * 100_000 + "/fix") == {"command": "/fix"}  # whitespace is free
    assert intent("/fix " + "y" * 100_000)["command"] == "/fix"


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
    # a missing state never blocks the other facts: the fixer rule's state check fails closed
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


def test_a_pr_comment_carries_its_intent_and_the_prs_state():
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
    assert data["command"] == "/fix"
    assert "mention" not in data  # the first token only
    assert data["state"] == "closed"  # the PR as the App reads it


def test_a_review_and_a_review_comment_carry_their_intent_and_state():
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
    assert data["mention"] == "@rules-culture-dev"
    assert data["state"] == "open"
    comment = {
        "action": "created",
        "comment": {"body": "/fix", "user": {"login": "OriNachum"}},
        "pull_request": _pr("closed"),
        "repository": {"full_name": "o/r"},
        "sender": {"login": "OriNachum"},
    }
    data = _deliver(store, "pull_request_review_comment", comment, "d-3")
    assert data["command"] == "/fix"
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


# ------------------------------------------------------------------- the matcher (Codex, 301ced2)


def test_a_bound_never_manufactures_a_token_boundary():
    assert intent(" " * 9_996 + "/fixed") == {"command": "/fixed"}
    padded = " " * (10_000 - len("@rules-culture-dev")) + "@rules-culture-devx"
    assert intent(padded) == {}
    assert intent("\n" * 50_000 + "/fix please") == {"command": "/fix"}  # whitespace is free


@pytest.mark.parametrize(
    "body",
    [
        "@ruleſ-culture-dev fix",  # U+017F folds to "s" under Unicode case-insensitivity
        "@rules-culture-deＶ fix",  # fullwidth
        "/ﬁx",  # U+FB01 ligature
        "/fiXK",  # Kelvin sign K
    ],
)
def test_only_ascii_case_is_folded(body):
    assert intent(body) == {}


@pytest.mark.parametrize(
    "body",
    [
        "@rules-culture-dev[bot]x",
        "@rules-culture-dev[bot].example",
        "@rules-culture-dev[bot]-staging",
        "@rules-culture-devé",  # a Unicode letter is no boundary
        "@rules-culture-dev_x",
    ],
)
def test_a_present_bot_suffix_is_consumed_before_the_boundary(body):
    assert "mention" not in intent(body)


@pytest.mark.parametrize(
    "body",
    ["@rules-culture-dev[bot]", "@RULES-CULTURE-DEV[BOT], fix", "@rules-culture-dev[bot]: fix"],
)
def test_the_bot_suffix_form_still_counts(body):
    assert intent(body) == {"mention": "@rules-culture-dev"}


@pytest.mark.parametrize("body", ["/fix, please", "/fix: now", "/fix."])
def test_a_command_needs_whitespace_or_the_end_after_it(body):
    assert "command" not in intent(body)


def test_unicode_whitespace_is_a_boundary():
    assert intent("/fix please") == {"command": "/fix"}
    assert intent("@rules-culture-dev fix") == {
        "mention": "@rules-culture-dev",
        "mention_command": "@rules-culture-dev fix",
    }


# --------------------------------------------------------------------------- d34: stop


@pytest.mark.parametrize(
    "body, expected",
    [
        ("/stop", {"command": "/stop"}),
        ("/STOP please", {"command": "/stop"}),
        (
            "@rules-culture-dev stop",
            {"mention": "@rules-culture-dev", "mention_command": "@rules-culture-dev stop"},
        ),
        (
            "@Rules-Culture-Dev[bot]  STOP now",
            {"mention": "@rules-culture-dev", "mention_command": "@rules-culture-dev stop"},
        ),
        (
            "@rules-culture-dev stop.",
            {"mention": "@rules-culture-dev", "mention_command": "@rules-culture-dev stop"},
        ),
        ("@rules-culture-dev, stop", {"mention": "@rules-culture-dev"}),  # not right after it
        (
            "@rules-culture-dev stopping",
            {"mention": "@rules-culture-dev", "mention_command": "@rules-culture-dev stopping"},
        ),
        ("@rules-culture-dev\n", {"mention": "@rules-culture-dev"}),
        (
            "@rules-culture-dev stopx-",
            {"mention": "@rules-culture-dev", "mention_command": "@rules-culture-dev stopx-"},
        ),
        ("@rules-culture-dev stopé", {"mention": "@rules-culture-dev"}),  # no word boundary
    ],
)
def test_the_word_after_the_apps_mention_is_its_mention_command(body, expected):
    assert intent(body) == expected


def test_an_issue_comment_and_a_review_comment_carry_their_comment_id():
    store = MemoryStore()
    store.put("actors", dict(APP))
    payload = {
        "action": "created",
        "issue": {"number": 7, "title": "T", "html_url": "u", "pull_request": {}, "state": "open"},
        "comment": {"id": 4242, "body": "/stop", "user": {"login": "OriNachum"}},
        "repository": {"full_name": "o/r"},
        "sender": {"login": "OriNachum"},
    }
    data = _deliver(store, "issue_comment", payload, "d-9", pull=lambda r, n: _pr())
    assert data["comment_id"] == 4242
    assert data["command"] == "/stop"
    bad = {**payload, "comment": {**payload["comment"], "id": "4242"}}
    assert "comment_id" not in _deliver(
        store, "issue_comment", bad, "d-10", pull=lambda r, n: _pr()
    )
