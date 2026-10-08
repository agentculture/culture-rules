"""d21 phase 1, Codex round 5: each test reproduces a finding on 86e7820."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

import pytest

from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, budget_id
from culture_rules.engine.run_completions import RUN_COMPLETIONS, reopen_undelivered
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.events.ingest import (
    EVENTS_COLLECTION,
    QUARANTINE_MAX_FIELD,
    bounded_record,
    event_document,
)
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger
from culture_rules.ops.backup import RUN_COLLECTIONS
from culture_rules.store.memory import MemoryStore
from tests.node.test_node import Cluster
from tests.node.test_run_events import (
    SETTLED,
    cluster,
    completion,
    cycles,
    fix_rule,
    follow_rule,
    rule_is,
    settle,
)

KEY = "pr:{trigger.data.repository}#{trigger.data.number}"


def _restore(source: Cluster):
    """A restore: config plus the backed-up run history, then the restore's reconciliation
    (re-opening included), before any node starts."""
    from culture_rules.ops.reconcile import reconcile_restored

    c = Cluster("spark")
    for coll in ("rules", "workflows", "machines", *RUN_COLLECTIONS):
        for doc in source.base.find(coll):
            c.base.put(coll, doc)
    report = reconcile_restored(c.base)
    return c, report


def _runs(c, rule_id):
    return c.base.find(RUNS_COLLECTION, {"rule_id": rule_id})


# #3 an unverified envelope at an assigned id must not suppress the genuine one --


def test_a_conflict_evaluated_at_an_assigned_id_does_not_suppress_the_genuine_event():
    c = cluster(fix_rule(), follow_rule(condition=rule_is("fix")), verdict="no")
    settle(c)
    cycles(c, 4)
    record = completion(c)
    assigned = record["event_id"]
    assert len(_runs(c, "review")) == 1
    # simulate a restore: the event is gone, the record re-opened, the review's run gone too
    c.base.delete(EVENTS_COLLECTION, assigned)
    for run in _runs(c, "review"):
        c.base.delete(RUNS_COLLECTION, run["id"])
    for intent in c.base.find("rule_fires", {"rule_id": "review"}):
        c.base.delete("rule_fires", intent["id"])
    for mark in c.base.find("run_event_consumption"):
        c.base.delete("run_event_consumption", mark["id"])
    c.base.update_if(RUN_COMPLETIONS, record["id"], {}, {"emitted": False})
    # a conflicting envelope takes the id and is evaluated (and refused) first
    bad = {**copy.deepcopy(record["envelope"]), "id": assigned, "hops": 0}
    c.base.insert(EVENTS_COLLECTION, event_document(bad, host="elsewhere"))
    cycles(c, 2)
    assert _runs(c, "review") == []
    assert c.base.find("run_event_consumption") == []  # refused: no consumption recorded
    # the operator removes the conflict; the parked record retries and the genuine event
    # (same id) is stored and evaluated
    c.base.delete(EVENTS_COLLECTION, assigned)
    c.clock.advance(3600 * 2)
    cycles(c, 4)
    assert len(_runs(c, "review")) == 1


# #4 keyset pagination ---------------------------------------------------------


def test_reopening_finds_missing_events_behind_a_full_page_of_present_ones():
    from culture_rules.engine.run_completions import REOPEN_BATCH

    store = MemoryStore()
    for i in range(REOPEN_BATCH + 5):
        store.put(RUN_COMPLETIONS, {"id": f"a{i:05d}", "emitted": True, "event_id": f"e{i}"})
        store.put(EVENTS_COLLECTION, {"id": f"e{i}", "envelope": {"id": f"e{i}"}})
    store.put(RUN_COMPLETIONS, {"id": "zzz", "emitted": True, "event_id": "missing"})
    assert reopen_undelivered(store) == 1
    assert store.get(RUN_COMPLETIONS, "zzz")["emitted"] is False


def test_the_reopen_limit_counts_reopened_records_not_scanned_ones():
    store = MemoryStore()
    for i in range(10):
        store.put(RUN_COMPLETIONS, {"id": f"a{i:02d}", "emitted": True, "event_id": f"e{i}"})
        store.put(EVENTS_COLLECTION, {"id": f"e{i}", "envelope": {"id": f"e{i}"}})
    for i in range(3):
        store.put(RUN_COMPLETIONS, {"id": f"z{i}", "emitted": True, "event_id": f"gone{i}"})
    assert reopen_undelivered(store, limit=2) == 2


# #5 consistent limits; a fully bounded fallback ---------------------------------


def test_find_range_limits_like_find():
    store = MemoryStore()
    store.put("c", {"id": "a", "t": 1})
    assert store.find_range("c", None, field="t", upto=5, limit=0) == []
    with pytest.raises(ValueError):
        store.find_range("c", None, field="t", upto=5, limit=-1)


def test_bounded_records_last_fallback_bounds_every_retained_field():
    doc = {
        "id": "q_" + "i" * 50_000,
        "count": 1,
        "last_seen": "l" * 50_000,
        "received_at": "r" * 50_000,
        "sha256": "s" * 50_000,
        "reason": "x" * 50_000,
        "expires_at": datetime(2026, 11, 1, tzinfo=UTC),
    }
    out = bounded_record(doc)
    for key, value in out.items():
        if isinstance(value, str):
            assert len(value.encode()) <= QUARANTINE_MAX_FIELD, key
    assert len(json.dumps(out, default=str)) <= 16384


# restore reconciliation ---------------------------------------------------------


def test_an_orphan_reservation_is_dropped_and_the_key_fires_again():
    keyed = Rule(
        id="keyed",
        name="keyed",
        trigger=Trigger(kind="event", params={"type": SETTLED}),
        action=Action(kind="noop"),
        concurrency_key=KEY,
    )
    src = cluster(keyed)
    # a reservation captured without its intent or run (per-collection backup boundary)
    src.base.put(
        RULE_ATTEMPT_BUDGETS,
        {
            "id": budget_id("pr:org/repo#7"),
            "key": "pr:org/repo#7",
            "rule_id": "keyed",
            "run_id": "run-lost",
            "intent_id": "intent-lost",
            "count": 1,
            "revision": 1,
        },
    )
    c, report = _restore(src)
    assert report.reservations_dropped == 1
    c.start()
    settle(c, 2)
    cycles(c, 3)
    assert len(_runs(c, "keyed")) == 1


def test_a_must_after_dependant_waiting_at_the_backup_fires_once_after_restore():
    a = Rule(
        id="a",
        name="a",
        trigger=Trigger(kind="event", params={"type": SETTLED}),
        action=Action(kind="noop"),
    )
    b = Rule(
        id="b",
        name="b",
        trigger=Trigger(kind="event", params={"type": SETTLED}),
        action=Action(kind="noop"),
        must_after=("a",),
    )
    src = cluster(a, b)
    settle(src)
    cycles(src, 1)  # a fired and finished; b waits; the chain has not handled a's end yet
    assert _runs(src, "a")[0]["status"] == "succeeded"
    assert _runs(src, "b") == []
    c, report = _restore(src)
    assert report.chains_redriven == 1
    c.start()
    cycles(c, 4)
    assert len(_runs(c, "b")) == 1
    # idempotent: reconciling again re-drives nothing new and starts no second run
    from culture_rules.ops.reconcile import reconcile_restored

    assert reconcile_restored(c.base).chains_redriven == 0
    cycles(c, 3)
    assert len(_runs(c, "b")) == 1


def test_the_marker_ids_cleared_on_delivery_are_the_trigger_consumers_ids():
    from culture_rules.events.triggers import EventTriggers
    from culture_rules.node.firing import SHARED_CONSUMER, placed_consumer

    assert SHARED_CONSUMER == "triggers"
    assert placed_consumer("spark") == "triggers@spark"
    t = EventTriggers(MemoryStore(), lambda tx, ev: None, host="spark", consumer="triggers")
    assert t.fire_id("runevt_x") == "triggers/runevt_x"
