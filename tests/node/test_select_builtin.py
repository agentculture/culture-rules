"""Select one step's outputs from whichever workflow path ran."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.actions.select import SELECT_BUILTIN, SelectPort
from culture_rules.node.runner import default_ports
from culture_rules.store.memory import MemoryStore

CONFIG = {
    "builtin": SELECT_BUILTIN,
    "sources": {"primary": "primary", "fallback": "fallback"},
    "ports": ["worktree", "start_sha", "commit_sha"],
    "optional": ["summary"],
}
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
A, B = "a" * 40, "b" * 40


def invoke(inputs, config=None):
    ctx = InvocationContext("run-1", "select", "code", "spark2", config=config or CONFIG)
    return SelectPort().invoke(inputs, "k", DEADLINE, context=ctx)


def test_the_path_that_ran_is_selected_with_its_label():
    res = invoke({"fallback_worktree": "/w", "fallback_start_sha": A, "fallback_commit_sha": B})
    assert res.outcome == "completed", res.error
    assert dict(res.output) == {
        "worktree": "/w",
        "start_sha": A,
        "commit_sha": B,
        "summary": None,
        "source": "fallback",
    }


def test_optional_ports_pass_through_from_the_selected_path_only():
    res = invoke(
        {
            "primary_worktree": "/q",
            "primary_start_sha": A,
            "primary_commit_sha": B,
            "primary_summary": "made x 3",
            "fallback_summary": "not this one",
        }
    )
    assert res.outcome == "completed", res.error
    assert res.output["summary"] == "made x 3"
    assert res.output["source"] == "primary"


def test_no_path_ran_fails_closed():
    res = invoke({"primary_summary": "only an optional value"})
    assert res.outcome == "failed"
    assert res.error.startswith("select_none:")
    assert res.retryable is False


def test_two_paths_with_values_are_ambiguous():
    res = invoke({"primary_worktree": "/q", "fallback_worktree": "/c"})
    assert res.outcome == "failed"
    assert res.error.startswith("select_ambiguous:")
    assert res.retryable is False


def test_a_path_missing_a_required_port_is_incomplete():
    res = invoke({"fallback_worktree": "/w", "fallback_start_sha": A})
    assert res.outcome == "failed"
    assert res.error.startswith("select_incomplete:")
    assert "commit_sha" in res.error


@pytest.mark.parametrize(
    "config",
    [
        {"builtin": SELECT_BUILTIN},
        {**CONFIG, "sources": {}},
        {**CONFIG, "sources": ["primary"]},
        {**CONFIG, "sources": {"primary": 1}},
        {**CONFIG, "sources": {"bad prefix": "x"}},
        {**CONFIG, "ports": []},
        {**CONFIG, "ports": ["worktree", 2]},
        {**CONFIG, "optional": "summary"},
        {**CONFIG, "optional": ["worktree"]},
        {**CONFIG, "ports": ["source"]},
    ],
)
def test_a_bad_config_fails_closed(config):
    res = invoke({"primary_worktree": "/q"}, config)
    assert res.outcome == "failed"
    assert res.error.startswith("bad_config:")
    assert res.retryable is False


def test_default_ports_register_select():
    code = default_ports(MemoryStore(), "spark2")["code"]
    assert SELECT_BUILTIN in code._builtins
