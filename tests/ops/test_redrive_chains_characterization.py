"""Characterization tests (Sonar S3776 split of d21 code): pin ``reconcile._redrive_chains``
- which finished runs, final skip decisions and failed intents it touches, which it leaves
for review, and its counts - exactly as it behaves, so splitting it into helpers is provably
behaviour-preserving."""

from __future__ import annotations

from datetime import UTC, datetime

from culture_rules.engine.claims import firing_key
from culture_rules.engine.decisions import RULE_DECISIONS, decision_key
from culture_rules.engine.matching import BLOCKED_BY_PREDECESSOR, PREDECESSOR_FAILED
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.node.firing import RULE_FIRES, run_id_for
from culture_rules.ops.reconcile import _redrive_chains
from culture_rules.store.memory import MemoryStore
from culture_rules.store.versioning import utc_timestamp

NOW = datetime(2026, 10, 8, tzinfo=UTC)
STAMP = utc_timestamp(NOW)


class _Lost:
    won = False


def _rule(id, must=(), may=(), **extra):
    return {
        "id": id,
        "name": id,
        "trigger": {"kind": "event", "params": {"type": "t"}},
        "action": {"kind": "message", "params": {}},
        "must_after": list(must),
        "may_after": list(may),
        **extra,
    }


def _store(*rules):
    store = MemoryStore()
    for r in rules:
        store.insert("rules", r)
    return store


def _run(store, rule_id, event_id, status="succeeded", **extra):
    doc = {
        "id": run_id_for(rule_id, event_id),
        "rule_id": rule_id,
        "rule": {"id": rule_id},
        "status": status,
        "rev": 1,
        "trigger": {"id": event_id},
        **extra,
    }
    store.insert(RUNS_COLLECTION, doc)
    return doc["id"]


def _skip(store, rule_id, event_id, **extra):
    doc = {
        "id": decision_key(rule_id, event_id),
        "rule_id": rule_id,
        "event_id": event_id,
        "fire": False,
        "reason": PREDECESSOR_FAILED,
        **extra,
    }
    store.insert(RULE_DECISIONS, doc)
    return doc["id"]


def _failed(store, rule_id, event_id):
    doc = {
        "id": firing_key(rule_id, event_id),
        "rule_id": rule_id,
        "event_id": event_id,
        "status": "failed",
    }
    store.insert(RULE_FIRES, doc)
    return doc["id"]


def test_finished_event_runs_with_undecided_dependants_are_touched():
    store = _store(_rule("a"), _rule("b", must=["a"]), _rule("c", may=["a"]))
    touched = _run(store, "a", "e1")
    _run(store, "a", "e2", status="running")  # not finished
    store.insert(
        RUNS_COLLECTION,
        {
            "id": "manual",
            "rule_id": "a",
            "rule": {"id": "a"},
            "status": "succeeded",
            "trigger": {"id": "e3"},
        },
    )  # not started by an event
    decided = _run(store, "a", "e4")
    store.insert(RULE_FIRES, {"id": firing_key("b", "e4"), "status": "started"})
    store.insert(RULE_DECISIONS, {"id": decision_key("c", "e4"), "reason": "condition_false"})
    assert _redrive_chains(store, NOW) == (1, 0)
    assert store.get(RUNS_COLLECTION, touched)["redriven_at"] == STAMP
    assert "redriven_at" not in store.get(RUNS_COLLECTION, decided)


def test_a_waiting_or_newest_deduplicated_dependant_is_still_undecided():
    store = _store(_rule("a"), _rule("b", must=["a"]))
    run = _run(store, "a", "e1")
    store.insert(RULE_DECISIONS, {"id": decision_key("b", "e1"), "reason": BLOCKED_BY_PREDECESSOR})
    run2 = _run(store, "a", "e2")
    store.insert(RULE_DECISIONS, {"id": decision_key("b", "e2"), "reason": "deduplicated"})
    assert _redrive_chains(store, NOW) == (2, 0)
    assert store.get(RUNS_COLLECTION, run)["redriven_at"] == STAMP
    assert store.get(RUNS_COLLECTION, run2)["redriven_at"] == STAMP


def test_rules_without_dependants_and_deleted_rules_are_ignored():
    store = _store(_rule("a"), _rule("b", must=["a"], deleted_at="x"))
    run = _run(store, "a", "e1")
    assert _redrive_chains(store, NOW) == (0, 0)
    assert "redriven_at" not in store.get(RUNS_COLLECTION, run)


def test_final_skips_are_touched_or_left_for_review_without_a_trigger():
    store = _store(_rule("a"), _rule("b", must=["a"]))
    with_snapshot = _skip(store, "a", "e1", trigger={"id": "e1"})
    recoverable = _skip(store, "a", "e2")
    store.insert(RULE_FIRES, {"id": firing_key("a", "e2"), "trigger": {"id": "e2"}})
    lost = _skip(store, "a", "e3")
    _skip(store, "a", "e4", reason=BLOCKED_BY_PREDECESSOR)  # not a settled skip
    done = _skip(store, "a", "e5", trigger={"id": "e5"})
    store.insert(RULE_FIRES, {"id": firing_key("b", "e5"), "status": "started"})
    assert _redrive_chains(store, NOW) == (2, 1)
    assert store.get(RULE_DECISIONS, with_snapshot)["redriven_at"] == STAMP
    assert store.get(RULE_DECISIONS, recoverable)["redriven_at"] == STAMP  # a's intent holds it
    assert "redriven_at" not in store.get(RULE_DECISIONS, lost)
    assert "redriven_at" not in store.get(RULE_DECISIONS, done)


def test_failed_intents_with_undecided_dependants_are_touched():
    store = _store(_rule("a"), _rule("b", may=["a"]))
    touched = _failed(store, "a", "e1")
    store.insert(RULE_FIRES, {"id": "x", "rule_id": "a", "event_id": "e2", "status": "pending"})
    done = _failed(store, "a", "e3")
    store.insert(RULE_DECISIONS, {"id": decision_key("b", "e3"), "reason": "condition_false"})
    assert _redrive_chains(store, NOW) == (1, 0)
    assert store.get(RULE_FIRES, touched)["redriven_at"] == STAMP
    assert "redriven_at" not in store.get(RULE_FIRES, done)


def test_lost_writes_are_not_counted():
    store = _store(_rule("a"), _rule("b", must=["a"]))
    _run(store, "a", "e1")
    _skip(store, "a", "e2", trigger={"id": "e2"})
    _failed(store, "a", "e3")
    writes = []

    def lose(collection, id, expected, changes, **kw):
        writes.append((collection, dict(expected), dict(changes)))
        return _Lost()

    store.update_if = lose
    assert _redrive_chains(store, NOW) == (0, 0)
    assert writes == [
        (RUNS_COLLECTION, {"rev": 1}, {"redriven_at": STAMP}),
        (RULE_DECISIONS, {"reason": PREDECESSOR_FAILED}, {"redriven_at": STAMP}),
        (RULE_FIRES, {"status": "failed"}, {"redriven_at": STAMP}),
    ]
