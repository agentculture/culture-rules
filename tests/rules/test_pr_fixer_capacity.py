"""#39 t2 end to end: a try whose agent hits its backend's usage limit (capacity_exhausted).

On the two-node chain world with the shipped bundle: ``pr-fixer-retry-capacity`` fires on the
try's ``rules.run.failed`` event and runs ``queue-add-capacity``. Its built-in
``fixer.cooldown`` rests the try's level (``fixer_cooldowns``, for
``vars.fixer_capacity_cooldown_s``) and ``queue.add`` puts the story back in line as a retry,
so ``queue.progress`` dispatches the next try at the next available level, or hands the story
back once when there is none. The failed try counts toward the 3.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta

import pytest

from culture_rules.model.condition import evaluate
from culture_rules.node.actions.fixer_levels import FIXER_COOLDOWNS
from culture_rules.node.actions.queue import DISPATCH_TYPE
from tests.rules.chain_world import (
    CODEX_FIXER,
    CODEX_LEVEL,
    QWEN_LEVEL,
    ChainWorld,
    rule_docs,
)
from tests.rules.test_pr_fixer_queue import fixes, settle

RULE = "pr-fixer-retry-capacity"
CODEX_ON = [QWEN_LEVEL, CODEX_LEVEL.replace("enabled=false", "enabled=true")]


def codex_world(tmp_path, turns) -> ChainWorld:
    """The chain world with level 2 (codex-fixer on its own queue and pool) enabled."""
    w = ChainWorld(tmp_path, turns=turns)
    w.c.base.put_variable("fixer_levels", CODEX_ON, updated_by="t")
    actor = copy.deepcopy(CODEX_FIXER)  # the shipped actor (t6), on its own pool (t2)
    actor["params"].update(concurrency_pool="codex-spark", max_concurrency=1)
    w.c.base.put("actors", actor)
    return w


def dispatched(w: ChainWorld) -> list[dict]:
    events = [e["envelope"] for e in w.c.base.find("events")]
    found = [e for e in events if e.get("type") == DISPATCH_TYPE]
    return sorted((e["data"] for e in found), key=lambda d: d["attempt"])


def until_of(w: ChainWorld, actor: str) -> datetime:
    return datetime.fromisoformat(w.c.base.get(FIXER_COOLDOWNS, actor)["until"])


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
