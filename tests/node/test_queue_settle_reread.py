"""d38 (#48): a checks-settle timeout no longer starts a fix on its own.

``github.pr.checks_settled`` settles a head SHA by the timeout while a counted suite is still
running. ``pr-fixer-checks`` passes the settle's ``conclusion`` on as the request input
``checks_conclusion``; at dispatch ``queue.progress`` reads that head's check suites again for
a ``timeout`` request:

* every counted suite completed and green (SonarCloud's quality gate is one of them) - the
  request is dropped (``checks_green_on_reread``), no run starts, and the story's status
  comment, if it has one, ends with :data:`~culture_rules.node.actions.queue.GREEN_TEXT`;
* a suite still running - it dispatches, and the instruction names the suites still running;
* a suite completed red, no counted suite, or a failed read - it dispatches as before.

A request of any other conclusion is never read again.
"""

from __future__ import annotations

import json

from culture_rules.engine.actorport import InvocationResult
from culture_rules.node.actions import queue as queue_mod
from culture_rules.store.memory import MemoryStore
from tests.node.test_github_action import (  # noqa: F401 - fixtures
    DEADLINE,
    Fake,
    actor_doc,
    ctx,
    pem,
)
from tests.node.test_queue import PROGRESS, World, request

LOOKUP = {**PROGRESS, "lookup_actor": "github-app"}
HEAD = "a" * 40
RULES = "docs/rules/pr-fixer"


def suite(app, status="completed", conclusion="success"):
    return {"app_slug": app, "status": status, "conclusion": conclusion}


class ChecksLookup:
    """An open PR at :data:`HEAD`; ``with_checks`` answers ``suites`` (``None``: unread)."""

    def __init__(self, suites):
        self.suites = suites
        self.inputs = []

    def invoke(self, input, key, deadline, *, context):
        self.inputs.append(dict(input))
        out = {"head_sha": HEAD, "state": "open", "base_sha": "b" * 40}
        if input.get("with_checks") is True:
            out["check_suites"] = self.suites
        return InvocationResult.completed(out)


def timed_out(**extra):
    return request("o/a", 1, checks_conclusion="timeout", **extra)


def test_a_timeout_whose_checks_are_green_by_dispatch_is_dropped():
    lookup = ChecksLookup([suite("github-actions"), suite("sonarqubecloud")])
    w = World(lookup=lookup)
    w.add(**timed_out())
    res = w.progress(LOOKUP)
    assert res.output["dispatched"] == []
    assert res.output["dropped"] == [{"key": "o/a#1", "reason": "checks_green_on_reread"}]
    assert w.dispatches() == []
    assert w.doc()["waiting"] == [] and w.doc()["active"] == []
    assert lookup.inputs[0]["with_checks"] is True


def test_neutral_and_skipped_suites_count_as_green():
    lookup = ChecksLookup(
        [suite("github-actions", conclusion="neutral"), suite("x", conclusion="skipped")]
    )
    w = World(lookup=lookup)
    w.add(**timed_out())
    assert w.progress(LOOKUP).output["dropped"][0]["reason"] == "checks_green_on_reread"


def test_a_suite_of_an_ignored_app_does_not_hold_the_drop():
    """``ignored_check_apps`` (default ``claude``) is left out exactly as the settler does."""
    lookup = ChecksLookup(
        [suite("github-actions"), suite("claude", status="in_progress", conclusion=None)]
    )
    w = World(lookup=lookup)
    w.add(**timed_out())
    assert w.progress(LOOKUP).output["dropped"][0]["reason"] == "checks_green_on_reread"


def test_the_ignored_apps_come_from_the_shared_variable():
    lookup = ChecksLookup(
        [suite("github-actions"), suite("slowbot", status="queued", conclusion=None)]
    )
    w = World(lookup=lookup)
    w.store.put_variable("ignored_check_apps", ["SlowBot"], updated_by="test")
    w.add(**timed_out())
    assert w.progress(LOOKUP).output["dropped"][0]["reason"] == "checks_green_on_reread"


def test_a_suite_still_running_dispatches_and_the_instruction_names_it():
    lookup = ChecksLookup(
        [suite("github-actions"), suite("sonarqubecloud", status="in_progress", conclusion=None)]
    )
    w = World(lookup=lookup)
    w.add(**timed_out())
    res = w.progress(LOOKUP)
    assert res.output["dispatched"] == ["o/a#1"]
    (event,) = w.dispatches()
    text = event["data"]["instruction"]
    assert text.startswith("fix o/a#1")
    assert "sonarqubecloud" in text and "github-actions" not in text
    assert "still running" in text


def test_a_red_suite_dispatches_with_the_instruction_unchanged():
    lookup = ChecksLookup([suite("github-actions", conclusion="failure"), suite("sonarqubecloud")])
    w = World(lookup=lookup)
    w.add(**timed_out())
    assert w.progress(LOOKUP).output["dispatched"] == ["o/a#1"]
    (event,) = w.dispatches()
    assert event["data"]["instruction"] == "fix o/a#1"


def test_no_counted_suite_is_not_green_and_dispatches():
    w = World(lookup=ChecksLookup([suite("claude")]))
    w.add(**timed_out())
    assert w.progress(LOOKUP).output["dispatched"] == ["o/a#1"]


def test_an_unread_check_list_dispatches_as_before():
    w = World(lookup=ChecksLookup(None))
    w.add(**timed_out())
    assert w.progress(LOOKUP).output["dispatched"] == ["o/a#1"]
    (event,) = w.dispatches()
    assert event["data"]["instruction"] == "fix o/a#1"


def test_a_failure_request_is_never_read_again():
    lookup = ChecksLookup([suite("github-actions")])
    w = World(lookup=lookup)
    w.add(**request("o/a", 1, checks_conclusion="failure"))
    assert w.progress(LOOKUP).output["dispatched"] == ["o/a#1"]
    assert "with_checks" not in lookup.inputs[0]


def test_a_request_without_a_conclusion_is_never_read_again():
    lookup = ChecksLookup([suite("github-actions")])
    w = World(lookup=lookup)
    w.add(**request("o/a", 1))
    assert w.progress(LOOKUP).output["dispatched"] == ["o/a#1"]
    assert "with_checks" not in lookup.inputs[0]


def test_a_moved_head_is_still_dropped_as_head_moved():
    class Moved(ChecksLookup):
        def invoke(self, input, key, deadline, *, context):
            res = super().invoke(input, key, deadline, context=context)
            return InvocationResult.completed({**res.output, "head_sha": "f" * 40})

    w = World(lookup=Moved([suite("github-actions")]))
    w.add(**timed_out())
    assert w.progress(LOOKUP).output["dropped"] == [{"key": "o/a#1", "reason": "head_moved"}]


def test_the_dropped_storys_status_comment_gets_its_final_text(monkeypatch):
    ended = []

    class Board:
        def __init__(self, store, *, clock=None):
            pass

        def finish(self, run, text, *, where=None):
            ended.append((run["id"], text, where))

    monkeypatch.setattr("culture_rules.node.status_board.StatusBoard", Board)
    w = World(lookup=ChecksLookup([suite("github-actions")]))
    w.store.put("runs", {"id": "r1", "status": "succeeded"})  # the queue-add run (ctx)
    w.add(**timed_out())
    w.progress(LOOKUP)
    assert ended == [("r1", queue_mod.GREEN_TEXT, ("o/a", 1))]


def test_a_failed_status_ending_never_fails_the_pass(monkeypatch):
    class Board:
        def __init__(self, store, *, clock=None):
            pass

        def finish(self, run, text, *, where=None):
            raise RuntimeError("store down")

    monkeypatch.setattr("culture_rules.node.status_board.StatusBoard", Board)
    w = World(lookup=ChecksLookup([suite("github-actions")]))
    w.store.put("runs", {"id": "r1", "status": "succeeded"})  # the queue-add run (ctx)
    w.add(**timed_out())
    assert w.progress(LOOKUP).output["dropped"][0]["reason"] == "checks_green_on_reread"


# ------------------------------------------------------------------ the PR read


class SuitesFake(Fake):
    """``/pulls/3`` at head ``c*40`` and ``/commits/<sha>/check-suites``."""

    def __init__(self, suites_status=200):
        super().__init__(200)
        self.suites_status = suites_status

    def __call__(self, method, url, headers, body, timeout):
        if url.endswith("/access_tokens"):
            return super().__call__(method, url, headers, body, timeout)
        self.calls.append(url)
        if "/pulls/" in url:
            pull = {"head": {"sha": "c" * 40}, "base": {"sha": "b" * 40, "ref": "main"}}
            return 200, json.dumps({**pull, "state": "open", "merged": False}).encode()
        if "/check-suites" in url:
            if self.suites_status != 200:
                return self.suites_status, b"{}"
            body = {
                "check_suites": [
                    {
                        "app": {"slug": "github-actions"},
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            }
            return 200, json.dumps(body).encode()
        return 404, b"{}"


def head_port(pem, fake):  # noqa: F811
    from culture_rules.node.actions.github import GitHubPrHeadPort

    store = MemoryStore()
    store.put("actors", actor_doc())
    return GitHubPrHeadPort(store, transport=fake, secrets=lambda ref: pem)


def test_the_pr_read_lists_the_heads_check_suites_when_asked(pem):  # noqa: F811
    fake = SuitesFake()
    res = head_port(pem, fake).invoke(
        {"repo": "acme/widgets", "number": 3, "with_checks": True}, "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed"
    assert res.output["check_suites"] == [
        {"app_slug": "github-actions", "status": "completed", "conclusion": "success"}
    ]
    assert any(c.split("?")[0].endswith(f"/commits/{'c' * 40}/check-suites") for c in fake.calls)


def test_no_check_suites_are_read_unless_asked(pem):  # noqa: F811
    fake = SuitesFake()
    res = head_port(pem, fake).invoke(
        {"repo": "acme/widgets", "number": 3}, "k", DEADLINE, context=ctx()
    )
    assert "check_suites" not in res.output
    assert not any("/check-suites" in c for c in fake.calls)


def test_an_unreadable_check_list_is_none_not_a_failure(pem):  # noqa: F811
    res = head_port(pem, SuitesFake(suites_status=500)).invoke(
        {"repo": "acme/widgets", "number": 3, "with_checks": True}, "k", DEADLINE, context=ctx()
    )
    assert res.outcome == "completed"
    assert res.output["check_suites"] is None


# ------------------------------------------------------------------ the rules


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_pr_fixer_checks_passes_the_settle_conclusion_to_the_queue():
    rule = _load(f"{RULES}/rules/pr-fixer-checks.json")
    assert rule["workflow"]["inputs"]["checks_conclusion"] == "trigger.data.conclusion"


def test_queue_add_carries_checks_conclusion_into_the_request():
    wf = _load(f"{RULES}/workflows/queue-add.json")
    assert "checks_conclusion" in [i["name"] for i in wf["inputs"]]
    (step,) = [s for s in wf["steps"] if s["id"] == "enqueue"]
    assert "checks_conclusion" in [i["name"] for i in step["inputs"]]
    assert {
        "source": "inputs",
        "source_port": "checks_conclusion",
        "target": "enqueue",
        "target_port": "checks_conclusion",
    } in wf["edges"]
