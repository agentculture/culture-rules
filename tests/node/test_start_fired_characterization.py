"""Characterization tests (Sonar S3776 refactor of firing.start_fired): every outcome of
starting one pending intent, against a scripted executor, pinned as it behaves today."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from culture_rules.engine.runs import RunError
from culture_rules.node.firing import RULE_FIRES, RuleFiring, _IntentGone
from culture_rules.store.memory import MemoryStore
from culture_rules.store.port import DuplicateKeyError, TransientStoreError

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


class ScriptedExecutor:
    def __init__(self, outcome=None):
        self.outcome = outcome
        self.calls = []

    def start_from_store(self, rule_id, **kw):
        self.calls.append((rule_id, kw))
        if self.outcome is not None:
            raise self.outcome
        return {"id": kw["run_id"]}


def _firing(outcome=None):
    store = MemoryStore()
    ex = ScriptedExecutor(outcome)
    firing = RuleFiring(store, "spark", ex, clock=lambda: T0)
    store.insert(
        RULE_FIRES,
        {
            "id": "i1",
            "rule_id": "r1",
            "event_id": "e1",
            "run_id": "run-1",
            "host": "spark",
            "placed": False,
            "status": "pending",
            "trigger": {"id": "e1"},
            "variables": {},
        },
    )
    return store, ex, firing


def test_a_started_intent_is_returned_and_marked_started():
    store, ex, firing = _firing()
    assert firing.start_fired() == ["run-1"]
    assert store.get(RULE_FIRES, "i1")["status"] == "started"
    ((rule_id, kw),) = ex.calls
    assert rule_id == "r1"
    assert kw["run_id"] == "run-1"
    assert kw["upstream"] is None
    assert kw["variables"] == {}


def test_a_duplicate_start_is_marked_started_but_not_returned():
    store, _, firing = _firing(DuplicateKeyError("runs", "run-1"))
    assert firing.start_fired() == []
    assert store.get(RULE_FIRES, "i1")["status"] == "started"


def test_a_paused_engine_leaves_the_intent_pending():
    store, _, firing = _firing(RunError("paused", "the engine is paused"))
    assert firing.start_fired() == []
    assert store.get(RULE_FIRES, "i1")["status"] == "pending"


def test_another_run_error_fails_the_intent_with_its_code():
    store, _, firing = _firing(RunError("workflow_required", "x"))
    assert firing.start_fired() == []
    doc = store.get(RULE_FIRES, "i1")
    assert (doc["status"], doc["error"]) == ("failed", "workflow_required")


@pytest.mark.parametrize("outcome", [_IntentGone("gone"), TransientStoreError("conflict")])
def test_a_gone_or_conflicting_intent_is_left_alone(outcome):
    store, _, firing = _firing(outcome)
    assert firing.start_fired() == []
    assert store.get(RULE_FIRES, "i1")["status"] == "pending"


def test_another_hosts_placed_intent_is_not_started_here():
    store, ex, firing = _firing()
    store.update_if(RULE_FIRES, "i1", {}, {"placed": True, "host": "thor", "fired_at": None})
    assert firing.start_fired() == []
    assert ex.calls == []
