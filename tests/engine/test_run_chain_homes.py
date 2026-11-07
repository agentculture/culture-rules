"""The shared helpers live in generic homes: ``engine/run_chain.py`` and
``extensions/github/port.py``."""

from __future__ import annotations

from culture_rules.engine import run_chain
from culture_rules.engine.run_chain import LineageError, upstream
from culture_rules.extensions.github.port import source_rule_refusal
from culture_rules.store.memory import MemoryStore


def test_run_chain_does_not_duplicate_the_runs_step_state() -> None:
    assert not hasattr(run_chain, "step_state")


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
