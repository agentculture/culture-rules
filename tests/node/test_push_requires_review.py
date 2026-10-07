"""d20: ``github.push`` refuses unless the run's reviewer approved exactly this commit.

The check reads the run's review record in the store (written only by the built-in
``review`` step), never a param, and runs on **every** push before any git or network call.
So a workflow edited to drop the review step - or to wire a made-up "approve" into the
push - still pushes nothing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("cryptography")

from culture_rules.actors.review import REVIEWS_COLLECTION  # noqa: E402
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


def approve(store, sha, **over):
    doc = {
        "id": "run-1",
        "run_id": "run-1",
        "commit_sha": sha,
        "reviewed_commit": sha,
        "verdict": "approve",
        "reviewer_actor": "codex-reviewer",
        "reviewer_backend": "codex",
        "implementer_actor": "qwen-fixer",
        "implementer_backend": "qwen",
    }
    doc.update(over)
    store.put(REVIEWS_COLLECTION, doc)


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
    approve(store, world.b, verdict=verdict)
    assert_refused(*attempt(pem, world, store), world, "review_rejected")


def test_an_approval_of_another_commit_pushes_nothing(pem, world):  # noqa: F811
    store = make_store()
    approve(store, world.a0)
    assert_refused(*attempt(pem, world, store), world, "review_commit_mismatch")
    store = make_store()
    approve(store, world.b, reviewed_commit=world.a0)
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
    approve(store, world.b, **over)
    assert_refused(*attempt(pem, world, store), world, "reviewer_is_implementer")


def test_another_runs_approval_does_not_count(pem, world):  # noqa: F811
    store = make_store()
    approve(store, world.b, id="run-2", run_id="run-2")
    assert_refused(*attempt(pem, world, store), world, "review_missing")


def test_an_approval_of_this_commit_by_another_backend_pushes(pem, world):  # noqa: F811
    store = make_store()
    approve(store, world.b)
    res, _fake, _rec = attempt(pem, world, store, gate_verdict="pass")
    assert res.outcome == "completed" and res.output["pushed"] is True
    assert world.remote_head() == world.b


def test_the_gate_verdict_is_still_checked_first(pem, world):  # noqa: F811
    store = make_store()
    approve(store, world.b)
    res, fake, rec = attempt(pem, world, store, gate_verdict="fail")
    assert_refused(res, fake, rec, world, "gate_not_passed")
