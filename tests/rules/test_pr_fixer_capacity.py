"""#39 t2 end to end: a try whose agent hits its backend's usage limit (capacity_exhausted).

On the two-node chain world with the shipped bundle: ``pr-fixer-retry-capacity`` fires on the
try's ``rules.run.failed`` event and runs ``queue-add-capacity``. Its built-in
``fixer.cooldown`` rests the try's level (``fixer_cooldowns``, for
``vars.fixer_capacity_cooldown_s``) and ``queue.add`` puts the story back in line as a retry,
so ``queue.progress`` dispatches the next try at the next available level, or hands the story
back once when there is none. The failed try counts toward the 3.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from culture_rules.model.condition import evaluate
from culture_rules.node.actions.fixer_levels import FIXER_COOLDOWNS
from culture_rules.node.actions.queue import DISPATCH_TYPE
from tests.rules.chain_world import CHAIN_VARIABLES, ChainWorld, rule_docs
from tests.rules.test_pr_fixer_queue import budget, fixes, handed_back, settle

RULE = "pr-fixer-retry-capacity"
CODEX_ON = [
    CHAIN_VARIABLES["fixer_levels"][0],
    CHAIN_VARIABLES["fixer_levels"][1].replace("enabled=false", "enabled=true"),
]


def codex_world(tmp_path, turns) -> ChainWorld:
    """The chain world with level 2 (codex-fixer on its own queue and pool) enabled."""
    w = ChainWorld(tmp_path, turns=turns)
    w.c.base.put_variable("fixer_levels", CODEX_ON, updated_by="t")
    w.c.base.put(
        "actors",
        {
            "id": "codex-fixer",
            "name": "codex-fixer",
            "kind": "agent",
            "params": {"concurrency_pool": "codex-spark", "max_concurrency": 1},
        },
    )
    return w


def dispatched(w: ChainWorld) -> list[dict]:
    events = [e["envelope"] for e in w.c.base.find("events")]
    found = [e for e in events if e.get("type") == DISPATCH_TYPE]
    return sorted((e["data"] for e in found), key=lambda d: d["attempt"])


def until_of(w: ChainWorld, actor: str) -> datetime:
    return datetime.fromisoformat(w.c.base.get(FIXER_COOLDOWNS, actor)["until"])


def test_a_capacity_failure_rests_level_1_and_the_retry_runs_at_level_2(tmp_path):
    w = codex_world(tmp_path, ["capacity", "commit"])
    settle(w, 1)
    w.run_chain()
    first, second = fixes(w)
    assert first["status"] == "failed"
    (capacity,) = w.runs(RULE)
    assert capacity["status"] == "succeeded"
    assert w.runs("pr-fixer-retry-failed") == []  # not a timeout
    ended = datetime.fromisoformat(first["finished_at"])
    assert until_of(w, "qwen-fixer") == ended + timedelta(seconds=3600)
    assert w.c.base.get(FIXER_COOLDOWNS, "codex-fixer") is None
    one, two = dispatched(w)
    assert (one["level"], one["fixer"], one["queue"]) == (1, "qwen-fixer", "pr-fixer")
    assert (two["level"], two["fixer"], two["queue"]) == (2, "codex-fixer", "pr-fixer-codex")
    assert second["trigger"]["data"]["retry"] is True
    assert second["inputs"]["instruction"].startswith(first["inputs"]["instruction"])
    assert "reached its usage limit" in second["inputs"]["instruction"]
    assert budget(w)["count"] == 2  # the failed try counted: never refunded
    assert len(w.run_of("publish-fix")) == 1
    assert handed_back(w) == []
    assert w.runs("pr-fixer-queue-progress-capacity")


def test_with_only_level_1_the_story_is_handed_back_once_while_it_rests(tmp_path):
    """As shipped (level 2 disabled): try 2 is due at level 1 again, which rests, and no
    level comes after it, so the story is handed back once instead of stalling."""
    w = ChainWorld(tmp_path, turns=["capacity", "commit"])
    settle(w, 1)
    w.run_chain()
    assert len(fixes(w)) == 1
    (capacity,) = w.runs(RULE)
    assert capacity["status"] == "succeeded"
    assert w.c.base.get(FIXER_COOLDOWNS, "qwen-fixer") is not None
    assert budget(w)["count"] == 1
    (handed,) = handed_back(w)
    assert "no_fixer_available" in handed
    assert "capacity_cooldown" in handed


def test_the_cooldown_length_is_the_variable(tmp_path):
    w = codex_world(tmp_path, ["capacity", "commit"])
    w.c.base.put_variable("fixer_capacity_cooldown_s", 120, updated_by="t")
    settle(w, 1)
    w.run_chain()
    first = fixes(w)[0]
    ended = datetime.fromisoformat(first["finished_at"])
    assert until_of(w, "qwen-fixer") == ended + timedelta(seconds=120)


def fires(rule_id: str, message: str) -> bool:
    cond = rule_docs()[rule_id]["condition"]
    data = {
        "workflow_id": "pr-fix",
        "error_message": message,
        "outputs": {"instruction": "fix it"},
        "repository": "o/r",
    }
    variables = {"fixer_repos": ["o/r"], "fixer_excluded_repos": []}
    return evaluate(cond, {"trigger": {"data": data}, "variables": variables})


@pytest.mark.parametrize("step", ["agent", "agent_qwen", "agent_codex"])
def test_the_capacity_retry_matches_any_agent_step_of_the_fix_loop(step):
    assert fires(RULE, f"fix[0]/{step}: capacity_exhausted: usage limit reached")
    assert fires(RULE, f"fix[0]/{step}: capacity_exhausted")
    assert not fires("pr-fixer-retry-failed", f"fix[0]/{step}: capacity_exhausted: limit")


@pytest.mark.parametrize(
    "message",
    [
        "fix[0]/agent: timeout: qwen did not finish within 3600s",
        "fix[0]/agent: no_changes: the agent made no commit",
        "fix[0]/gate: capacity_exhausted: not an agent step",
        "secrets: capacity_exhausted: outside the fix loop",
        "fix[0]/agent: capacity_exhaustedly",
    ],
)
def test_the_capacity_retry_matches_nothing_else(message):
    assert not fires(RULE, message)


@pytest.mark.parametrize(
    ("turn", "rule_id"), [("timeout", "pr-fixer-retry-failed"), ("capacity", RULE)]
)
def test_a_retry_after_a_failed_try_keeps_the_storys_one_status_comment(tmp_path, turn, rule_id):
    """The retry's queue-add run stands on a verified rules.run.failed link, so the story's
    root (and its one status comment) is still the run that began it."""
    from culture_rules.node.story_stop import story_root

    w = codex_world(tmp_path, [turn, "commit"])
    settle(w, 1)
    w.run_chain()
    (retry,) = w.runs(rule_id)
    assert story_root(w.c.base, retry)["rule_id"] == "pr-fixer-checks"
    (status,) = w.comments()
    assert status.startswith("PR fixer pushed the fix")


def test_the_capacity_rule_and_its_workflow_ship_as_designed():
    from tests.rules.chain_world import bundle, workflow_docs

    by = {r.id: r for r in bundle().rules}
    rule = by[RULE]
    assert rule.enabled is False
    assert rule.trigger.params == {"type": "rules.run.failed"}
    assert rule.workflow.id == "queue-add-capacity"
    assert rule.counts_toward_budget is False  # the failed try counted; never refunded
    assert "retry" not in rule.workflow.inputs  # the cooldown step supplies it
    progress = by["pr-fixer-queue-progress-capacity"]
    assert progress.trigger.params == {"type": "rules.run.succeeded"}
    assert progress.condition["right"] == {"literal": "queue-add-capacity"}
    steps = {s["id"]: s for s in workflow_docs()["queue-add-capacity"]["steps"]}
    assert steps["cooldown"]["config"] == {
        "builtin": "fixer.cooldown",
        "cooldown": "fixer_capacity_cooldown_s",
        "levels": "fixer_levels",
    }
    assert steps["enqueue"]["config"] == workflow_docs()["queue-add"]["steps"][0]["config"]
