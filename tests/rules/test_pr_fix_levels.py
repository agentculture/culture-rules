"""#39 t4: pr-fix runs one agent step per level, then the single gate.

The shipped pr-fix, run directly in the two-node chain world with each ``fixer`` a dispatch
can carry. ``agent_qwen`` runs when the try's fixer is qwen-fixer or none is given (no
``fixer_levels``: as before #39); ``agent_codex`` runs on spark when it is codex-fixer, its
commits carried to spark2 by ``handoff_export`` and ``handoff_import``; ``agent_claude``
(#56) runs on spark when it is claude-fixer, through its own ``handoff_export_claude`` and
``handoff_import_claude``; ``select`` hands the path that ran to the one gate (never two
edges into one port). An unknown fixer fails closed. The store, not the routing, still says
which agent step made the gated commit.

Acceptance criteria (plan pr-fixer-escalation, t4):

1. the fix body and its single gate - ``test_a_codex_try_*``, ``test_a_qwen_try_*``,
   ``test_without_a_fixer_*``, ``test_an_unknown_fixer_*``, ``test_the_fix_body_*``;
   dispatch mapping - ``test_the_dispatch_maps_the_level_fields``;
2. status comment - tests/node/test_fixer_status.py (``*level*``);
3. no new push path - tests/rules/test_pr_fixer_bundle.py
   (``test_publish_fix_is_unchanged_by_the_escalation``).
"""

from __future__ import annotations

import pytest

from culture_rules.actors.lineage import final_gate
from culture_rules.actors.review import ReviewVerdictPort
from culture_rules.engine.runs import step_state
from tests.rules.chain_world import (
    DISPATCH_RULE,
    RETRY_FAILED_RULE,
    RETRY_RULE,
    STAGE_RULES,
    ChainWorld,
    rule_docs,
    workflow_docs,
)
from tests.rules.test_pr_fixer_single import REPO, TRUSTED

CODEX_STEPS = ("agent_codex", "handoff_export", "handoff_import")
CLAUDE_STEPS = ("agent_claude", "handoff_export_claude", "handoff_import_claude")
"""#56: the Claude path, on spark like Codex's, with its own hand-off."""
SUCCEEDED = "succeeded"
SKIPPED = "skipped"
CODEX = "codex-fixer"
CLAUDE = "claude-fixer"
QWEN = "qwen-fixer"


def world(tmp_path) -> ChainWorld:
    """Only pr-fix: no stage or retry rule continues the run."""
    return ChainWorld(tmp_path, disabled=(*STAGE_RULES, RETRY_RULE, RETRY_FAILED_RULE))


def run_fix(w: ChainWorld, **extra) -> dict:
    inputs = {
        "repo": REPO,
        "number": 7,
        "head_sha": w.repo.start,
        "head_branch": "fix",
        "base_sha": w.repo.base,
        "clone_url": f"file://{w.repo.wt}",
        "instruction": "make x 3",
        "trusted_authors": [TRUSTED],
        **extra,
    }
    run = w.c.nodes["spark2"].executor.start_workflow("pr-fix", inputs, "test@test")
    w.run_chain()
    return w.c.base.get("runs", run["id"])


def status(doc: dict, step: str) -> str:
    return step_state(doc, f"fix[0]/{step}")["status"]


def made_by(doc: dict) -> str:
    """The agent step the review's own store check finds behind the gated commit."""
    fg = final_gate(doc)
    definition, _state = ReviewVerdictPort._implementer(
        doc, fg.parent, fg.iteration, fg.body, None, fg.outputs
    )
    return definition["id"]


def test_a_codex_try_runs_on_spark_and_the_gate_judges_its_commit(tmp_path):
    w = world(tmp_path)
    doc = run_fix(w, fixer=CODEX, reviewer="qwen-reviewer", level=2, attempt=2)
    assert doc["status"] == SUCCEEDED, doc.get("error")
    assert status(doc, "agent_qwen") == SKIPPED
    for step in CODEX_STEPS:
        assert status(doc, step) == SUCCEEDED
    for step in CLAUDE_STEPS:
        assert status(doc, step) == SKIPPED
    assert w.export_accounts == {"culture-codex-fixer"}  # #56: the step's run_as
    assert step_state(doc, "fix[0]/agent_codex")["host"] == "spark"
    assert step_state(doc, "fix[0]/handoff_export")["host"] == "spark"
    assert step_state(doc, "fix[0]/handoff_import")["host"] == "spark2"
    assert step_state(doc, "fix[0]/gate")["host"] == "spark2"
    codex_tip = step_state(doc, "fix[0]/agent_codex")["outputs"]["head_after"]
    gate = step_state(doc, "fix[0]/gate")["outputs"]
    assert gate["verdict"] == "pass"
    assert gate["agent_commit_sha"] == codex_tip  # the same SHA after the hand-off
    assert doc["outputs"]["implementer"] == CODEX
    assert doc["outputs"]["reviewer"] == "qwen-reviewer"
    assert doc["outputs"]["summary"] == "made x 3"
    assert made_by(doc) == "agent_codex"
    assert w.qwen.inputs == []
    assert w.claude.inputs == []
    assert len(w.codex.inputs) == 1


def test_a_claude_try_runs_on_spark_and_its_own_hand_off_labels_it(tmp_path):
    """#56: agent_claude on spark, its commit carried by its own hand-off (exported as
    culture-claude-fixer) into select's claude source: the implementer is claude-fixer."""
    w = world(tmp_path)
    doc = run_fix(w, fixer=CLAUDE, reviewer="qwen-reviewer", level=1, attempt=1)
    assert doc["status"] == SUCCEEDED, doc.get("error")
    for step in ("agent_qwen", *CODEX_STEPS):
        assert status(doc, step) == SKIPPED
    for step in CLAUDE_STEPS:
        assert status(doc, step) == SUCCEEDED
    assert step_state(doc, "fix[0]/agent_claude")["host"] == "spark"
    assert step_state(doc, "fix[0]/handoff_export_claude")["host"] == "spark"
    assert step_state(doc, "fix[0]/handoff_import_claude")["host"] == "spark2"
    assert w.export_accounts == {"culture-claude-fixer"}
    claude_tip = step_state(doc, "fix[0]/agent_claude")["outputs"]["head_after"]
    gate = step_state(doc, "fix[0]/gate")["outputs"]
    assert gate["verdict"] == "pass"
    assert gate["agent_commit_sha"] == claude_tip
    assert doc["outputs"]["implementer"] == CLAUDE
    assert doc["outputs"]["reviewer"] == "qwen-reviewer"
    assert made_by(doc) == "agent_claude"
    assert w.qwen.inputs == []
    assert w.codex.inputs == []
    assert len(w.claude.inputs) == 1


@pytest.mark.parametrize("extra", [{"fixer": QWEN, "level": 1, "attempt": 1}, {"fixer": ""}])
def test_a_qwen_try_runs_on_spark2_and_skips_the_hand_off(tmp_path, extra):
    w = world(tmp_path)
    doc = run_fix(w, reviewer="codex-reviewer", **extra)
    assert doc["status"] == SUCCEEDED, doc.get("error")
    assert status(doc, "agent_qwen") == SUCCEEDED
    assert step_state(doc, "fix[0]/agent_qwen")["host"] == "spark2"
    for step in (*CLAUDE_STEPS, *CODEX_STEPS):
        assert status(doc, step) == SKIPPED
    assert doc["outputs"]["implementer"] == QWEN
    assert made_by(doc) == "agent_qwen"
    assert w.codex.inputs == []
    assert w.claude.inputs == []


def test_without_a_fixer_pr_fix_runs_qwen_as_before(tmp_path):
    """No ``fixer_levels``: the dispatch names no fixer, and pr-fix is today's Qwen try."""
    w = world(tmp_path)
    doc = run_fix(w)
    assert doc["status"] == SUCCEEDED, doc.get("error")
    assert status(doc, "agent_qwen") == SUCCEEDED
    assert doc["outputs"]["implementer"] == QWEN
    assert doc["outputs"].get("reviewer") is None
    assert "fixer" not in w.qwen.inputs[0]  # nothing new reaches the agent's prompt


def test_an_unknown_fixer_fails_closed_before_any_gate(tmp_path):
    w = world(tmp_path)
    doc = run_fix(w, fixer="gemini-fixer")
    assert doc["status"] == "failed"
    assert "select_none" in doc["error"]["message"]
    for step in ("agent_qwen", "agent_claude", "agent_codex"):
        assert status(doc, step) == SKIPPED
    assert step_state(doc, "fix[0]/gate")["status"] != SUCCEEDED
    assert w.qwen.inputs == []
    assert w.codex.inputs == []
    assert w.claude.inputs == []


def test_the_fix_body_feeds_the_gate_from_one_source_step():
    wf = workflow_docs()["pr-fix"]
    (fix,) = (s for s in wf["steps"] if s["id"] == "fix")
    ids = [b["id"] for b in fix["body"]]
    assert ids == ["agent_qwen", *CLAUDE_STEPS, *CODEX_STEPS, "select", "gate"]
    into = {}
    for e in wf["edges"]:
        into.setdefault((e["target"], e["target_port"]), []).append(e["source"])
    assert all(len(sources) == 1 for sources in into.values()), into
    for port in ("worktree", "start_sha", "commit_sha"):
        assert into[("gate", port)] == ["select"]
    assert into[("handoff_import", "handoff_id")] == ["handoff_export"]
    assert into[("handoff_import_claude", "handoff_id")] == ["handoff_export_claude"]
    for prefix, last in (("claude", "handoff_import_claude"), ("codex", "handoff_import")):
        for port in ("worktree", "start_sha", "commit_sha"):
            assert into[("select", f"{prefix}_{port}")] == [last]  # never another path's
    assert into[("fix", "clone_url")] == ["inputs"]  # the import's URL: the trusted input


def test_the_dispatch_maps_the_level_fields():
    inputs = rule_docs()[DISPATCH_RULE]["workflow"]["inputs"]
    for name in ("fixer", "reviewer", "attempt", "level"):
        assert inputs[name] == f"trigger.data.{name}"


# --------------------------------------------------------------------------- a story


def test_a_story_whose_qwen_try_fails_its_gate_gets_a_codex_try(tmp_path):
    """Level 2 enabled: try 1 (Qwen) fails its gate's diff guard, so the retry is dispatched
    at level 2 and pr-fix runs agent_codex through the hand-off; qwen-reviewer reviews the
    Codex commit (t5) and it is published; the story keeps one status comment naming each
    try's level and fixer, and never hands back."""
    from tests.rules.chain_world import CODEX_LEVEL, QWEN_LEVEL
    from tests.rules.test_pr_fixer_queue import guard

    levels = [QWEN_LEVEL, CODEX_LEVEL.replace("enabled=false", "enabled=true")]
    w = ChainWorld(tmp_path, turns=[guard], levels=levels)
    w.fire()
    first, second = w.run_of("pr-fix")[:2]
    assert first["inputs"]["fixer"] == QWEN
    assert status(first, "agent_qwen") == SUCCEEDED
    assert step_state(first, "fix[0]/gate")["outputs"]["verdict"] != "pass"
    assert {k: second["inputs"][k] for k in ("fixer", "reviewer", "level", "attempt")} == {
        "fixer": CODEX,
        "reviewer": "qwen-reviewer",
        "level": 2,
        "attempt": 2,
    }
    assert status(second, "agent_qwen") == SKIPPED
    assert status(second, "handoff_import") == SUCCEEDED
    assert step_state(second, "fix[0]/gate")["outputs"]["verdict"] == "pass"
    assert second["outputs"]["implementer"] == CODEX
    assert len(w.issues.posts) == 1  # one status comment for the story
    (body,) = w.comments()
    assert "try 1 (qwen-fixer, level 1): verdict " in body
    assert "try 2 (codex-fixer, level 2): " in body
    assert "handed back" not in body
    (review,) = w.run_of("review-commit")
    assert review["trigger"]["data"]["run_id"] == second["id"]
    assert step_state(review, "review_codex")["status"] == SKIPPED
    assert step_state(review, "review_qwen")["status"] == SUCCEEDED
    assert review["outputs"]["review"] == "approve"
    assert w.reviewer.inputs == []  # codex-reviewer never reviewed the Codex try
    assert len(w.qwen_reviewer.inputs) == 1
    (publish,) = w.run_of("publish-fix")
    assert publish["status"] == SUCCEEDED
    assert len(w.push.calls) == 1


def test_after_a_guard_retry_the_reviewer_judges_the_original_task(tmp_path):  # #59
    """Try 1 fails the diff guard; try 2 passes. The reviewer's pr_intent is the story's
    original instruction, never the guard's 'revert these changes' text (live on
    culture-rules-tester#6: a correct merge was refused for not reverting an unpushed
    commit)."""
    from tests.rules.test_pr_fixer_queue import guard

    w = ChainWorld(tmp_path, turns=[guard])
    w.fire()
    first = w.run_of("pr-fix")[0]
    (seen,) = w.reviewer.inputs
    assert (seen["pr_intent"], "diff guard rejected" in seen["pr_intent"]) == (
        first["inputs"]["instruction"],
        False,
    )
