"""#56 t5: one story through all three seeded levels, on fakes.

Level 1 is seeded disabled, so these worlds flip it on (``THREE_LEVELS_LIVE``): try 1 is
claude-fixer reviewed by qwen-reviewer, try 2 qwen-fixer reviewed by codex-reviewer, try 3
codex-fixer reviewed by qwen-reviewer. Each of the first two reviews asks for changes, so the
retry climbs the ladder; the third approves and the fix is published once. With the seed as
shipped (Claude off) try 1 goes to qwen-fixer.
"""

from __future__ import annotations

import pytest

from culture_rules.engine.runs import step_state
from tests.rules.chain_world import CHAIN_VARIABLES, THREE_LEVELS_LIVE, ChainWorld
from tests.rules.test_pr_fixer_chain import changes
from tests.rules.test_pr_fixer_single import verdict_text
from tests.rules.test_review_routing import record

TRIES = [
    ("claude-fixer", "qwen-reviewer", "claude"),
    ("qwen-fixer", "codex-reviewer", "qwen"),
    ("codex-fixer", "qwen-reviewer", "codex"),
]


@pytest.fixture(scope="module")
def story(tmp_path_factory):
    w = ChainWorld(
        tmp_path_factory.mktemp("ladder"),
        levels=THREE_LEVELS_LIVE,
        reviews=[changes],  # codex-reviewer judges try 2 (Qwen) and asks for changes
        qwen_reviews=[changes, verdict_text],  # qwen-reviewer: try 1 changes, try 3 approves
    )
    w.fire()
    return w


def test_the_seeded_variables_are_the_live_ladder_with_claude_off():
    levels = CHAIN_VARIABLES["fixer_levels"]
    assert [lv.split()[1] for lv in levels] == [
        "actor=claude-fixer",
        "actor=qwen-fixer",
        "actor=codex-fixer",
    ]


def test_the_story_runs_exactly_three_tries(story):
    assert len(story.run_of("pr-fix")) == 3


def test_each_try_is_dispatched_to_its_levels_fixer(story):
    assert [r["inputs"]["fixer"] for r in story.run_of("pr-fix")] == [t[0] for t in TRIES]


def test_each_try_is_dispatched_with_its_levels_reviewer(story):
    assert [r["inputs"]["reviewer"] for r in story.run_of("pr-fix")] == [t[1] for t in TRIES]


def test_the_levels_are_numbered_one_to_three(story):
    assert [r["inputs"]["level"] for r in story.run_of("pr-fix")] == [1, 2, 3]


def test_only_the_tried_levels_agent_step_ran_in_each_try(story):
    ran = [
        [s for s in ("agent_claude", "agent_qwen", "agent_codex") if status(r, s) == "succeeded"]
        for r in story.run_of("pr-fix")
    ]
    assert ran == [["agent_claude"], ["agent_qwen"], ["agent_codex"]]


def test_each_bridge_took_one_request(story):
    assert [len(b.inputs) for b in (story.claude, story.qwen, story.codex)] == [1, 1, 1]


def test_each_try_is_reviewed_by_its_levels_reviewer(story):
    reviews = story.run_of("review-commit")
    got = [
        (
            "qwen-reviewer"
            if step_state(r, "review_qwen")["status"] == "succeeded"
            else "codex-reviewer"
        )
        for r in reviews
    ]
    assert got == [t[1] for t in TRIES]


def test_each_review_records_the_pair_of_actors_and_backends(story):
    recs = [record(story, r) for r in story.run_of("review-commit")]
    assert [(x["implementer_actor"], x["reviewer_actor"]) for x in recs] == [
        (t[0], t[1]) for t in TRIES
    ]


def test_no_review_is_by_the_backend_of_the_try_it_judges(story):
    recs = [record(story, r) for r in story.run_of("review-commit")]
    assert [x["implementer_backend"] == x["reviewer_backend"] for x in recs] == [False] * 3


def test_the_reviewer_backends_are_qwen_codex_qwen(story):
    recs = [record(story, r) for r in story.run_of("review-commit")]
    assert [x["reviewer_backend"] for x in recs] == ["qwen", "codex", "qwen"]


def test_the_reviewers_were_asked_in_ladder_order(story):
    assert (len(story.qwen_reviewer.inputs), len(story.reviewer.inputs)) == (2, 1)


def test_the_third_try_is_approved_and_published_once(story):
    reviews = story.run_of("review-commit")
    assert [r["status"] for r in reviews] == ["succeeded"] * 3
    assert len(story.run_of("publish-fix")) == 1


def test_the_story_pushes_once_and_keeps_one_status_comment(story):
    assert (len(story.push.calls), len(story.comments())) == (1, 1)


def test_the_status_comment_names_each_tries_level_and_fixer(story):
    (body,) = story.comments()
    assert [f"try {n} ({t[0]}, level {n})" in body for n, t in enumerate(TRIES, 1)] == [True] * 3


def test_with_the_seed_as_shipped_try_1_goes_to_qwen_fixer(tmp_path):
    w = ChainWorld(tmp_path, levels=CHAIN_VARIABLES["fixer_levels"])
    w.fire()
    (run,) = w.run_of("pr-fix")
    assert (run["inputs"]["fixer"], run["inputs"]["level"]) == ("qwen-fixer", 2)


def test_with_the_seed_as_shipped_the_claude_bridge_is_never_called(tmp_path):
    w = ChainWorld(tmp_path, levels=CHAIN_VARIABLES["fixer_levels"])
    w.fire()
    assert w.claude.inputs == []


def status(run, step):
    state = step_state(run, f"fix[0]/{step}")
    return state["status"] if state else "absent"
