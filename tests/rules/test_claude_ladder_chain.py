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
