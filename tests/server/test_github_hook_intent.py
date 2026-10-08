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
        ("/fix the lint please", {"command": "/fix"}),
        ("  \n/FIX\nthe lint", {"command": "/fix"}),
        ("/fixed it myself", {"command": "/fixed"}),
        ("@rules-culture-dev please fix the lint", {"mention": "@rules-culture-dev"}),
        ("can you look, @Rules-Culture-Dev?", {"mention": "@rules-culture-dev"}),
        ("ping @rules-culture-dev[bot]", {"mention": "@rules-culture-dev"}),
        ("/fix @rules-culture-dev", {"command": "/fix", "mention": "@rules-culture-dev"}),
    ],
)
def test_commands_and_mentions_are_read(body, expected):
    assert intent(body) == expected


@pytest.mark.parametrize(
    "body",
    [
        "please /fix this",  # not at the start
        "mail rules@rules-culture-dev.example",  # part of an address
        "@rules-culture-devx is another app",
        "@rules-culture-dev-staging is another app",
        "> @rules-culture-dev please fix\n\nquoted, not asked",
        "```\n@rules-culture-dev\n```",
        "see `@rules-culture-dev` in the docs",
        "",
    ],
)
def test_what_is_not_an_ask(body):
    assert "mention" not in intent(body)
    if not body.startswith("/"):
        assert "command" not in intent(body)


def test_no_self_identity_reads_no_mention_and_bodies_are_bounded():
    assert comment_intent("@rules-culture-dev fix", None) == {}
    assert comment_intent("/" + "x" * 100, "a[bot]") == {}  # not a command word
    assert comment_intent(None, "a[bot]") == {}
    long = "/fix " + "y" * 100_000 + " @rules-culture-dev"
    assert intent(long) == {"command": "/fix"}  # read within a bound


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
    assert complete_pr_facts(without) is not None and "state" not in complete_pr_facts(without)


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
    assert data["command"] == "/fix" and data["mention"] == "@rules-culture-dev"
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
    assert data["mention"] == "@rules-culture-dev" and data["state"] == "open"
    comment = {
        "action": "created",
        "comment": {"body": "/fix", "user": {"login": "OriNachum"}},
        "pull_request": _pr("closed"),
        "repository": {"full_name": "o/r"},
        "sender": {"login": "OriNachum"},
    }
    data = _deliver(store, "pull_request_review_comment", comment, "d-3")
    assert data["command"] == "/fix" and data["state"] == "closed"
    sync = {
        "action": "synchronize",
        "pull_request": _pr(),
        "repository": {"full_name": "o/r"},
        "sender": {"login": "OriNachum"},
    }
    data = _deliver(store, "pull_request", sync, "d-4")
    assert data["state"] == "open" and "command" not in data


# ------------------------------------------------------------------- Markdown code (Codex #4)


@pytest.mark.parametrize(
    "body",
    [
        "~~~\n@rules-culture-dev\n~~~",
        "~~~~python\n@rules-culture-dev fix\n~~~~",
        "````\n```\n@rules-culture-dev\n```\n````",  # a longer fence holds a shorter one
        "```\n@rules-culture-dev\n",  # an unclosed fence runs to the end
        "text\n\n    @rules-culture-dev in an indented block\n",
        "text\n\n\t@rules-culture-dev in a tab-indented block\n",
        "see ``@rules-culture-dev`` here",
        "see ```@rules-culture-dev``` inline",
        "  > @rules-culture-dev quoted with leading spaces",
    ],
    ids=[
        "tilde",
        "long_tilde_info",
        "nested_backticks",
        "unclosed",
        "indented",
        "tab_indented",
        "double_backtick",
        "triple_backtick_inline",
        "quote",
    ],
)
def test_a_mention_inside_any_markdown_code_or_quote_is_not_an_ask(body):
    assert "mention" not in intent(body)


@pytest.mark.parametrize(
    "body",
    [
        "~~~\ncode\n~~~\n@rules-culture-dev please fix",
        "```\ncode\n```\nthen @rules-culture-dev fix it",
        "intro\n    @rules-culture-dev",  # not preceded by a blank line: a paragraph line
    ],
)
def test_a_mention_outside_the_code_still_counts(body):
    assert intent(body).get("mention") == "@rules-culture-dev"


@pytest.mark.parametrize(
    "body",
    ["    /fix", "\t/fix", "```\n/fix\n```", "~~~\n/fix\n~~~"],
    ids=["indented", "tab", "backtick_fence", "tilde_fence"],
)
def test_a_command_in_code_is_not_a_command(body):
    assert "command" not in intent(body)


def test_a_command_may_be_indented_by_up_to_three_spaces():
    assert intent("   /fix it")["command"] == "/fix"


def test_hostile_backtick_runs_parse_in_linear_time():
    import time

    bodies = [
        "@rules-culture-dev " + "`" * 9_900,
        "".join("`" * n + "x " for n in range(1, 140)),
        "`a" * 5_000,
    ]
    start = time.monotonic()
    for body in bodies:
        intent(body)
    assert time.monotonic() - start < 0.5
