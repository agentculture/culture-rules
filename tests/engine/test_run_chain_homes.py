"""The shared helpers live in generic homes: ``engine/run_chain.py`` and
``node/actions/github.py``."""

from __future__ import annotations

from culture_rules.engine import run_chain
from culture_rules.engine.run_chain import LineageError, step_state, upstream
from culture_rules.node.actions.github import source_rule_refusal
from culture_rules.store.memory import MemoryStore


def test_step_state_finds_a_step_by_key() -> None:
    run = {"steps": [{"key": "a", "status": "succeeded"}]}
    assert step_state(run, "a") == {"key": "a", "status": "succeeded"}


def test_step_state_is_none_when_absent() -> None:
    assert step_state({"steps": []}, "a") is None


def test_upstream_refuses_a_run_not_started_by_a_run_event() -> None:
    try:
        upstream(MemoryStore(), {"id": "r1", "trigger": {"type": "manual"}})
    except LineageError as exc:
        assert exc.code == run_chain.CHAIN_UNVERIFIED
    else:  # pragma: no cover - the call must raise
        raise AssertionError("upstream did not refuse")


def test_source_rule_refusal_without_a_run() -> None:
    assert source_rule_refusal(MemoryStore(), "missing") == "run_not_found"


def test_source_rule_refusal_on_a_disabled_rule() -> None:
    store = MemoryStore()
    store.put("rules", {"id": "rule-a", "enabled": False})
    store.put("runs", {"id": "run-1", "rule_id": "rule-a"})
    assert source_rule_refusal(store, "run-1") == "rule_disabled"
