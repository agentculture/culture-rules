"""#39 t5: review-commit's reviewer follows the try's fixer.

On the two-node chain world with the shipped bundle: ``review_codex`` (codex-reviewer, Codex
on spark) reviews a Qwen try, ``review_qwen`` (qwen-reviewer, Qwen Code on spark2 in mode
``plan``) reviews a Codex try, routed by the ``reviewer`` the dispatch gave the try. The
verdict step takes the one review step that succeeded and still derives the reviewer and
the implementer from the store: a routing that pairs a try with a reviewer on its own
backend is refused (``reviewer_is_implementer``), and a reviewer no review step knows runs
no review (``review_missing``). Without ``fixer_levels`` the chain is as before #39.

Acceptance criteria (plan pr-fixer-escalation, t5):

1. routing and the verdict - ``test_a_codex_try_*``, ``test_without_fixer_levels_*``,
   ``test_a_try_routed_to_*``, ``test_an_unknown_reviewer_*``; ``REVIEWER_BACKENDS`` and
   ``review_steps`` - tests/actors/test_review_steps.py;
2. the qwen-reviewer actor and the digests - tests/rules/test_pr_fixer_bundle.py
   (``test_the_bundle_ships_both_reviewer_actors_*``, ``test_qwen_reviewer_is_*``,
   ``test_the_d21_review_commit_stays_trusted_*``).
"""

from __future__ import annotations

import copy

import pytest

from culture_rules.actors.review import REVIEWER_BRIEF, current_review, review_target
from culture_rules.engine.runs import step_state
from tests.rules import chain_world
from tests.rules.chain_world import CHAIN_VARIABLES, CODEX_FIXER, REPO, ChainWorld
from tests.rules.test_pr_fixer_queue import handed_back, settle

QWEN_LEVEL, CODEX_LEVEL = CHAIN_VARIABLES["fixer_levels"]
SUCCEEDED, SKIPPED = "succeeded", "skipped"
REVIEW_CODEX = "review_codex"
REVIEW_QWEN = "review_qwen"
QWEN_REVIEWER = "qwen-reviewer"


def world(tmp_path, levels: list[str]) -> ChainWorld:
    """The chain world with ``levels`` as ``fixer_levels`` and codex-fixer on its pool."""
    w = ChainWorld(tmp_path)
    w.c.base.put_variable("fixer_levels", levels, updated_by="t")
    actor = copy.deepcopy(CODEX_FIXER)
    actor["params"].update(concurrency_pool="codex-spark", max_concurrency=1)
    w.c.base.put("actors", actor)
    return w


def codex_first(tmp_path) -> ChainWorld:
    """Level 1 disabled, level 2 (codex-fixer, reviewed by qwen-reviewer) enabled."""
    return world(
        tmp_path,
        [
            QWEN_LEVEL.replace("enabled=true", "enabled=false"),
            CODEX_LEVEL.replace("enabled=false", "enabled=true"),
        ],
    )


def story(w: ChainWorld) -> dict:
    """Run one story; its one review-commit run."""
    settle(w, 1)
    w.run_chain()
    (review,) = w.run_of("review-commit")
    return review


def record(w: ChainWorld, review: dict) -> dict:
    out = review["outputs"] or {}
    target = review_target(REPO, 7, out.get("base_sha"), out.get("start_sha"), out["commit_sha"])
    _rid, rec, _state = current_review(w.c.base, target)
    return rec


def refusal(review: dict) -> str:
    return review["error"]["message"]


def test_a_codex_try_is_reviewed_by_qwen_reviewer_in_plan_mode_and_published(tmp_path):
    w = codex_first(tmp_path)
    review = story(w)
    assert review["status"] == SUCCEEDED, review.get("error")
    assert step_state(review, REVIEW_CODEX)["status"] == SKIPPED
    qwen = step_state(review, REVIEW_QWEN)
    assert qwen["status"] == SUCCEEDED
    assert qwen["host"] == "spark2"  # where qwen-reviewer lives
    assert w.reviewer.inputs == []  # codex-reviewer never sees a Codex try
    (given,) = w.qwen_reviewer.inputs
    assert given["mode"] == "plan"  # the actor's mode, locked by the adapter
    assert given["sandbox"] == "read-only"
    assert given["instruction"] == REVIEWER_BRIEF
    assert given["reviewer"] == QWEN_REVIEWER
    assert review["outputs"]["review"] == "approve"
    rec = record(w, review)
    assert (rec["reviewer_actor"], rec["reviewer_backend"]) == (QWEN_REVIEWER, "qwen")
    assert (rec["implementer_actor"], rec["implementer_backend"]) == ("codex-fixer", "codex")
    assert set(rec["trusted_actors"]) == {QWEN_REVIEWER}
    (publish,) = w.run_of("publish-fix")
    assert publish["status"] == SUCCEEDED
    assert len(w.push.calls) == 1
    assert handed_back(w) == []


def test_without_fixer_levels_codex_reviewer_reviews_as_before(tmp_path, monkeypatch):
    """No ``fixer_levels``: the dispatch names no reviewer, so review_codex runs."""
    plain = {k: v for k, v in CHAIN_VARIABLES.items() if k != "fixer_levels"}
    monkeypatch.setattr(chain_world, "CHAIN_VARIABLES", plain)
    w = ChainWorld(tmp_path)
    review = story(w)
    assert review["status"] == SUCCEEDED, review.get("error")
    assert review["inputs"].get("reviewer") is None
    assert step_state(review, REVIEW_CODEX)["status"] == SUCCEEDED
    assert step_state(review, REVIEW_QWEN)["status"] == SKIPPED
    assert w.qwen_reviewer.inputs == []
    rec = record(w, review)
    assert (rec["reviewer_actor"], rec["implementer_actor"]) == ("codex-reviewer", "qwen-fixer")
    assert len(w.push.calls) == 1


@pytest.mark.parametrize(
    ("levels", "ran", "pair"),
    [
        (  # a Qwen try routed to qwen-reviewer: another actor, the same backend
            [QWEN_LEVEL.replace("reviewer=codex-reviewer", "reviewer=qwen-reviewer")],
            REVIEW_QWEN,
            "reviewer qwen-reviewer/qwen vs implementer qwen-fixer/qwen",
        ),
        (  # a Codex try routed to codex-reviewer
            [
                QWEN_LEVEL.replace("enabled=true", "enabled=false"),
                CODEX_LEVEL.replace("reviewer=qwen-reviewer", "reviewer=codex-reviewer").replace(
                    "enabled=false", "enabled=true"
                ),
            ],
            REVIEW_CODEX,
            "reviewer codex-reviewer/codex vs implementer codex-fixer/codex",
        ),
    ],
)
def test_a_try_routed_to_a_reviewer_on_its_own_backend_is_refused(tmp_path, levels, ran, pair):
    """The routing is a hint: the verdict step derives both sides from the store."""
    w = world(tmp_path, levels)
    review = story(w)
    assert review["status"] == "failed"
    assert step_state(review, ran)["status"] == SUCCEEDED
    assert refusal(review) == f"reviewer_is_implementer: {pair}"
    assert w.run_of("publish-fix") == []
    assert w.push.calls == []
    assert len(handed_back(w)) == 1


def test_an_unknown_reviewer_runs_no_review_and_pushes_nothing(tmp_path):
    w = world(tmp_path, [QWEN_LEVEL.replace("reviewer=codex-reviewer", "reviewer=claude-reviewer")])
    review = story(w)
    assert review["status"] == "failed"
    assert step_state(review, REVIEW_CODEX)["status"] == SKIPPED
    assert step_state(review, REVIEW_QWEN)["status"] == SKIPPED
    assert refusal(review).startswith("review_missing")
    assert w.reviewer.inputs == []
    assert w.qwen_reviewer.inputs == []
    assert w.push.calls == []
    assert len(handed_back(w)) == 1
