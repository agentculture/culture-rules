"""Characterization tests (Sonar S3776 split of d21 code): pin ``settle_decision``'s first
insert and its supersede write - the trigger snapshot (d21) and a fire's ``by`` - exactly as
they behave, so splitting it into helpers is provably behaviour-preserving."""

from __future__ import annotations

from culture_rules.engine.decisions import (
    BLOCKED_BY_PREDECESSOR,
    DEDUPLICATED,
    RULE_DECISIONS,
    decision_key,
    settle_decision,
)
from culture_rules.engine.matching import CONDITION_FALSE, FIRE, Decision
from culture_rules.store.memory import MemoryStore

ENV = {"id": "e1", "type": "t", "data": {"x": 1}}
KEY = decision_key("r", "e1")


def _d(fire, reason, **kw):
    return Decision(rule_id="r", fire=fire, reason=reason, **kw)


def _settle(store, decision, **kw):
    return settle_decision(store, decision, event_id="e1", host="h", at="t1", **kw)


def test_a_first_fire_with_always_names_its_upstream_and_keeps_the_trigger():
    store = MemoryStore()
    fire = _d(True, FIRE, upstream={"p": {"status": "succeeded"}, "q": {}})
    doc = _settle(store, fire, run_id="run-1", always=True, trigger=ENV)
    assert doc["by"] == ["p", "q"]
    assert doc["message"] == "ran after p, q"
    assert (doc["run_id"], doc["trigger"]) == ("run-1", ENV)


def test_a_first_fire_or_unrecorded_skip_without_always_is_not_written():
    store = MemoryStore()
    assert _settle(store, _d(True, FIRE), trigger=ENV) is None
    assert _settle(store, _d(False, CONDITION_FALSE), trigger=ENV) is None
    assert store.find(RULE_DECISIONS) == []


def test_a_first_recorded_skip_without_a_trigger_has_no_snapshot():
    store = MemoryStore()
    doc = _settle(store, _d(False, BLOCKED_BY_PREDECESSOR, by=("p",)))
    assert "trigger" not in doc
    assert "trigger_omitted" not in doc


def _waiting(store, **extra):
    store.insert(
        RULE_DECISIONS,
        {
            "id": KEY,
            "rule_id": "r",
            "event_id": "e1",
            "fire": False,
            "reason": BLOCKED_BY_PREDECESSOR,
            "by": ["p"],
            "detail": "",
            "message": "waiting for predecessor p",
            "at": "t0",
            "host": "h0",
            **extra,
        },
    )


def test_a_superseded_record_without_a_snapshot_takes_the_trigger():
    store = MemoryStore()
    _waiting(store)
    doc = _settle(store, _d(True, FIRE, by=("x",)), run_id="run-1", trigger=ENV)
    assert (doc["fire"], doc["by"], doc["run_id"], doc["trigger"]) == (
        True,
        ["p"],
        "run-1",
        ENV,
    )
    assert doc["superseded"] == [
        {
            "reason": BLOCKED_BY_PREDECESSOR,
            "by": ["p"],
            "detail": "",
            "message": "waiting for predecessor p",
            "at": "t0",
            "host": "h0",
        }
    ]


def test_a_superseded_record_keeps_its_own_snapshot_or_omission():
    store = MemoryStore()
    _waiting(store, trigger={"id": "e1", "old": True})
    doc = _settle(store, _d(False, CONDITION_FALSE), trigger=ENV)
    assert doc["trigger"] == {"id": "e1", "old": True}
    other = MemoryStore()
    _waiting(other, trigger_omitted=True)
    doc = _settle(other, _d(False, CONDITION_FALSE), trigger=ENV)
    assert ("trigger" in doc, doc["trigger_omitted"]) == (False, True)


def test_a_deduplicated_record_is_superseded_by_another_decision_only():
    store = MemoryStore()
    _waiting(store, reason=DEDUPLICATED)
    same = _settle(store, _d(False, DEDUPLICATED), trigger=ENV)
    assert (same["reason"], "superseded" in same) == (DEDUPLICATED, False)
    doc = _settle(store, _d(False, CONDITION_FALSE))
    assert (doc["reason"], len(doc["superseded"]), "trigger" in doc) == (
        CONDITION_FALSE,
        1,
        False,
    )
