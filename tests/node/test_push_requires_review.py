"""d20: ``github.push`` refuses unless the run's reviewer approved exactly this commit.

The check reads the run's review record in the store (written only by the built-in
``review`` step), never a param, and runs on **every** push before any git or network call.
So a workflow edited to drop the review step - or to wire a made-up "approve" into the
push - still pushes nothing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("cryptography")

from culture_rules.actors.review import record_review  # noqa: E402
from tests.node.test_github_pr_actions import (  # noqa: E402,F401 - fixtures
    DEADLINE,
    FakeGitHub,
    RecordingGit,
    World,
    ctx,
    make_store,
    pem,
    push_params,
    push_port,
    world,
)


def approve(store, sha, *, iteration=0, attempt_no=1, **over):
    fields = {
        "commit_sha": sha,
        "reviewed_commit": sha,
        "verdict": "approve",
        "reviewer_actor": "codex-reviewer",
        "reviewer_backend": "codex",
        "implementer_actor": "qwen-fixer",
        "implementer_backend": "qwen",
        "repo": "acme/widgets",
        "number": 3,
        "start_sha": None,
    }
    run_id = over.pop("run_id", "run-1")
    over.pop("id", None)
    fields.update(over)
    record_review(store, run_id, iteration=iteration, attempt=attempt_no, fields=fields)


def attempt(pem, world, store, **params):  # noqa: F811
    fake, rec = FakeGitHub(world), RecordingGit()
    port = push_port(pem, world, fake, store=store, gitrec=rec, review=False)
    res = port.invoke(push_params(world, **params), "k", DEADLINE, context=ctx())
    return res, fake, rec


def assert_refused(res, fake, rec, world, code):  # noqa: F811
    assert (res.outcome, res.error, res.retryable) == ("failed", code, False)
    assert fake.calls == [] and rec.verbs() == []  # no network, no git
    assert world.remote_head() == world.a


def test_no_review_record_pushes_nothing(pem, world):  # noqa: F811
    res, fake, rec = attempt(pem, world, make_store(), gate_verdict="pass")
    assert_refused(res, fake, rec, world, "review_missing")


def test_a_made_up_review_param_is_not_a_review(pem, world):  # noqa: F811
    # someone edits the workflow: drops the review step and wires an "approve" literal
    res, fake, rec = attempt(
        pem,
        world,
        make_store(),
        gate_verdict="pass",
        review="approve",
        review_verdict="approve",
        reviewed_commit=world.b,
    )
    assert_refused(res, fake, rec, world, "review_missing")


@pytest.mark.parametrize("verdict", ["request_changes", "not_run", "review_invalid"])
def test_a_review_that_did_not_approve_pushes_nothing(pem, world, verdict):  # noqa: F811
    store = make_store()
    approve(store, world.b, start_sha=world.a, verdict=verdict)
    assert_refused(*attempt(pem, world, store), world, "review_rejected")


def test_an_approval_of_another_commit_pushes_nothing(pem, world):  # noqa: F811
    store = make_store()
    approve(store, world.a0)
    assert_refused(*attempt(pem, world, store), world, "review_commit_mismatch")
    store = make_store()
    approve(store, world.b, start_sha=world.a, reviewed_commit=world.a0)
    assert_refused(*attempt(pem, world, store), world, "review_commit_mismatch")


@pytest.mark.parametrize(
    "over",
    [
        {"reviewer_actor": "qwen-fixer"},
        {"reviewer_backend": "qwen"},
        {"reviewer_backend": None},
    ],
)
def test_a_reviewer_that_is_the_implementer_pushes_nothing(pem, world, over):  # noqa: F811
    store = make_store()
    approve(store, world.b, start_sha=world.a, **over)
    assert_refused(*attempt(pem, world, store), world, "reviewer_is_implementer")


def test_another_runs_approval_does_not_count(pem, world):  # noqa: F811
    store = make_store()
    approve(store, world.b, start_sha=world.a, id="run-2", run_id="run-2")
    assert_refused(*attempt(pem, world, store), world, "review_missing")


def test_an_approval_of_this_commit_by_another_backend_pushes(pem, world):  # noqa: F811
    store = make_store()
    approve(store, world.b, start_sha=world.a)
    res, _fake, _rec = attempt(pem, world, store, gate_verdict="pass")
    assert res.outcome == "completed" and res.output["pushed"] is True
    assert world.remote_head() == world.b


def test_the_gate_verdict_is_still_checked_first(pem, world):  # noqa: F811
    store = make_store()
    approve(store, world.b, start_sha=world.a)
    res, fake, rec = attempt(pem, world, store, gate_verdict="fail")
    assert_refused(res, fake, rec, world, "gate_not_passed")


# --------------------------------------------------------------------------- #3: range binding


@pytest.mark.parametrize(
    "over, code",
    [
        ({"repo": "acme/other"}, "review_target_mismatch"),
        ({"repo": None}, "review_target_mismatch"),
        ({"number": 4}, "review_target_mismatch"),
        ({"number": None}, "review_target_mismatch"),
        ({"start_sha": "a0"}, "review_commit_mismatch"),  # reviewed from another start
        ({"start_sha": None}, "review_commit_mismatch"),
    ],
)
def test_an_approval_for_another_pr_or_range_pushes_nothing(pem, world, over, code):  # noqa: F811
    store = make_store()
    if over.get("start_sha") == "a0":
        over = {**over, "start_sha": world.a0}
    approve(store, world.b, **{"start_sha": world.a, **over})
    assert_refused(*attempt(pem, world, store), world, code)


def test_the_reviewed_range_matching_the_push_pushes(pem, world):  # noqa: F811
    store = make_store()
    approve(store, world.b, start_sha=world.a, repo="ACME/Widgets")  # repo case-insensitive
    res, _fake, _rec = attempt(pem, world, store)
    assert res.outcome == "completed" and res.output["pushed"] is True


# --------------------------------------------------------------------------- #6: re-check


def _revoked_in_flight(pem, world, write):  # noqa: F811
    store = make_store()
    approve(store, world.b, start_sha=world.a)
    fake, rec = FakeGitHub(world), RecordingGit()
    fake.on_push_token = lambda: write(store)  # after the first check, before the push
    port = push_port(pem, world, fake, store=store, gitrec=rec, review=False)
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    return res, rec


def test_a_newer_rejection_landing_mid_push_stops_the_push(pem, world):  # noqa: F811
    res, rec = _revoked_in_flight(
        pem,
        world,
        lambda store: approve(
            store, world.b, start_sha=world.a, iteration=1, verdict="request_changes"
        ),
    )
    assert (res.outcome, res.error) == ("failed", "review_rejected")
    assert "push" not in rec.verbs() and world.remote_head() == world.a


def test_another_current_approval_mid_push_is_review_changed(pem, world):  # noqa: F811
    # even an equivalent approval, if it is a different record, is not the one judged
    res, rec = _revoked_in_flight(
        pem, world, lambda store: approve(store, world.b, start_sha=world.a, iteration=1)
    )
    assert (res.outcome, res.error) == ("failed", "review_changed")
    assert "push" not in rec.verbs() and world.remote_head() == world.a


def test_a_stale_record_written_mid_push_changes_nothing(pem, world):  # noqa: F811
    store = make_store()
    approve(store, world.b, start_sha=world.a, iteration=2)
    fake = FakeGitHub(world)
    fake.on_push_token = lambda: approve(
        store, world.b, start_sha=world.a, iteration=0, verdict="request_changes"
    )
    port = push_port(pem, world, fake, store=store, review=False)
    res = port.invoke(push_params(world), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed" and world.remote_head() == world.b


# --------------------------------------------------------------------------- round 2, #7


def test_replies_name_the_given_pushed_commit_and_refuse_a_malformed_one():
    from culture_rules.engine.actorport import InvocationContext
    from culture_rules.node.actions.github_pr import AddressedThreadsPort

    threads = [{"thread_id": "T1", "comment_id": 1}]
    addressed = [{"thread_id": "T1", "commit": "a" * 40, "reply": "done"}]
    ctx_ = InvocationContext("r", "pick", "code", "h")
    port = AddressedThreadsPort()
    out = port.invoke(
        {"threads": threads, "addressed": addressed, "commit": "b" * 40}, "k", None, context=ctx_
    )
    assert out.output["replies"][0]["commit"] == "b" * 40
    bad = port.invoke(
        {"threads": threads, "addressed": addressed, "commit": "HEAD"}, "k", None, context=ctx_
    )
    assert bad.outcome == "failed" and bad.error == "bad_input"
