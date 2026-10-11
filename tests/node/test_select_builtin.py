"""#39 t4: the built-in ``select`` - one step's outputs from whichever path of a workflow ran.

pr-fix runs ``agent_qwen`` or ``agent_codex`` (then the hand-off) by ``when``; the gate is
fed from one source step, ``select``, never from two edges into one port.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.select import SELECT_BUILTIN, SelectPort
from culture_rules.node.runner import default_ports
from culture_rules.store.memory import MemoryStore

CONFIG = {
    "builtin": SELECT_BUILTIN,
    "sources": {"qwen": "qwen-fixer", "codex": "codex-fixer"},
    "ports": ["worktree", "start_sha", "commit_sha"],
    "optional": ["summary"],
}
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
PR_FIX = Path(__file__).resolve().parents[2] / "docs/rules/pr-fixer/workflows/pr-fix.json"
A, B = "a" * 40, "b" * 40


def invoke(inputs, config=None):
    ctx = InvocationContext("run-1", "fix[0]/select", "code", "spark2", config=config or CONFIG)
    return SelectPort().invoke(inputs, "k", DEADLINE, context=ctx)


def test_the_path_that_ran_is_selected_with_its_label():
    res = invoke({"codex_worktree": "/w", "codex_start_sha": A, "codex_commit_sha": B})
    assert res.outcome == "completed", res.error
    assert dict(res.output) == {
        "worktree": "/w",
        "start_sha": A,
        "commit_sha": B,
        "summary": None,
        "source": "codex-fixer",
    }


def test_optional_ports_pass_through_from_the_selected_path_only():
    res = invoke(
        {
            "qwen_worktree": "/q",
            "qwen_start_sha": A,
            "qwen_commit_sha": B,
            "qwen_summary": "made x 3",
            "codex_summary": "not this one",
        }
    )
    assert res.outcome == "completed", res.error
    assert res.output["summary"] == "made x 3"
    assert res.output["source"] == "qwen-fixer"


def test_no_path_ran_fails_closed():
    res = invoke({"qwen_summary": "only an optional value"})
    assert res.outcome == "failed"
    assert res.error.startswith("select_none:")
    assert res.retryable is False


def test_two_paths_with_values_are_ambiguous():
    res = invoke({"qwen_worktree": "/q", "codex_worktree": "/c"})
    assert res.outcome == "failed"
    assert res.error.startswith("select_ambiguous:")
    assert res.retryable is False


def test_a_path_missing_a_required_port_is_incomplete():
    res = invoke({"codex_worktree": "/w", "codex_start_sha": A})
    assert res.outcome == "failed"
    assert res.error.startswith("select_incomplete:")
    assert "commit_sha" in res.error


@pytest.mark.parametrize(
    "config",
    [
        {"builtin": SELECT_BUILTIN},
        {**CONFIG, "sources": {}},
        {**CONFIG, "sources": ["qwen"]},
        {**CONFIG, "sources": {"qwen": 1}},
        {**CONFIG, "sources": {"bad prefix": "x"}},
        {**CONFIG, "ports": []},
        {**CONFIG, "ports": ["worktree", 2]},
        {**CONFIG, "optional": "summary"},
        {**CONFIG, "optional": ["worktree"]},
        {**CONFIG, "ports": ["source"]},
    ],
)
def test_a_bad_config_fails_closed(config):
    res = invoke({"qwen_worktree": "/q"}, config)
    assert res.outcome == "failed"
    assert res.error.startswith("bad_config:")
    assert res.retryable is False


def test_default_ports_register_select():
    code = default_ports(MemoryStore(), "spark2")["code"]
    assert SELECT_BUILTIN in code._builtins


def _shipped_select() -> tuple[dict, set[str]]:
    """pr-fix's own select step: its config and the input names it declares."""
    (fix,) = (s for s in json.loads(PR_FIX.read_text())["steps"] if s["id"] == "fix")
    (step,) = (b for b in fix["body"] if b["id"] == "select")
    return step["config"], {p["name"] for p in step["inputs"]}


def test_the_shipped_pr_fix_labels_a_claude_commit_claude_fixer():
    """#56: the Claude path has its own hand-off, feeding select's claude source, so its
    commit is labelled claude-fixer - never codex-fixer."""
    config, declared = _shipped_select()
    inputs = {"claude_worktree": "/c", "claude_start_sha": A, "claude_commit_sha": B}
    assert set(inputs) <= declared
    res = invoke({**inputs, "claude_summary": "made x 3"}, config)
    assert res.outcome == "completed", res.error
    assert res.output["source"] == "claude-fixer"
    assert res.output["summary"] == "made x 3"


@pytest.mark.parametrize("prefix", ["qwen", "codex", "claude"])
def test_each_shipped_pr_fix_path_declares_its_inputs(prefix):
    config, declared = _shipped_select()
    names = (*config["ports"], *config["optional"])
    assert {f"{prefix}_{n}" for n in names} <= declared
    assert config["sources"][prefix] == f"{prefix}-fixer"
