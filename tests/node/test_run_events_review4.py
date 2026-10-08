"""d21 phase 1, Codex round 4: each test reproduces a finding on a362711."""

from __future__ import annotations

import copy
import json
from datetime import timedelta

from culture_rules.engine.run_completions import RUN_COMPLETIONS
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.events.ingest import (
    EVENTS_COLLECTION,
    QUARANTINE_COLLECTION,
    QUARANTINE_MAX_RECORD,
    event_document,
)
from culture_rules.node.run_events import RunEventOutbox, deliver
from culture_rules.ops.backup import RUN_COLLECTIONS
from culture_rules.store.memory import MemoryStore
from tests.node.test_node import Cluster
from tests.node.test_run_events import (
    SUCCEEDED,
    cluster,
    completion,
    cycles,
    fix_rule,
    follow_rule,
    rule_is,
    settle,
)


def _restore(source: Cluster) -> Cluster:
    """A restore: config plus exactly the run-history collections the backup carries."""
    from culture_rules.engine.run_completions import reopen_undelivered

    c = Cluster("spark")
    for coll in ("rules", "workflows", *RUN_COLLECTIONS):
        for doc in source.base.find(coll):
            c.base.put(coll, doc)
    reopen_undelivered(c.base)
    return c


def _runs(c, rule_id):
    return c.base.find(RUNS_COLLECTION, {"rule_id": rule_id})


def _squat(c, event_id, envelope):
    bad = {**copy.deepcopy(envelope), "id": event_id, "hops": 0}
    c.base.insert(EVENTS_COLLECTION, event_document(bad, host="elsewhere"))


# #3 an assigned event id is immutable ---------------------------------------


def test_a_reopened_record_whose_id_is_squatted_parks_and_never_takes_a_new_id():
    c = cluster(fix_rule(), follow_rule(condition=rule_is("fix")))
    settle(c)
    cycles(c, 4)
    record = completion(c)
    assigned = record["event_id"]
    assert len(_runs(c, "review")) == 1
    # a restore re-opened it, and something else now holds its id
    c.base.delete(EVENTS_COLLECTION, assigned)
    c.base.update_if(RUN_COMPLETIONS, record["id"], {}, {"emitted": False})
    _squat(c, assigned, record["envelope"])
    with c.base.transaction() as tx:
        assert deliver(tx, record["id"]) is None
    record = completion(c)
    assert record["event_id"] == assigned  # never re-assigned
    assert record["blocked"] is True and record["emitted"] is False
    cycles(c, 4)
    assert len(_runs(c, "review")) == 1  # no second downstream run under another id


# #2 no age window; decisions backed up -------------------------------------


def test_a_consumer_offline_for_days_still_gets_its_event_after_a_restore():
    src = cluster(fix_rule(), follow_rule(condition=rule_is("fix")))
    settle(src)
    cycles(src, 1)
    src.nodes["spark"].firing.run_events.poll()  # emitted ...
    rec = completion(src)
    old = (src.clock() - timedelta(days=3)).isoformat()
    src.base.update_if(RUN_COMPLETIONS, rec["id"], {}, {"emitted_at": old})
    # ... three days ago, and no trigger consumer ran since
    c = _restore(src)
    c.start()
    cycles(c, 4)
    assert len(_runs(c, "review")) == 1


def test_an_event_consumed_before_the_backup_is_not_re_decided_by_a_rule_added_later():
    src = cluster(fix_rule(), follow_rule(condition=rule_is("fix")))
    settle(src)
    cycles(src, 4)
    c = _restore(src)
    c.define(follow_rule("late", SUCCEEDED, rule_is("fix")))  # added after the backup
    c.start()
    cycles(c, 4)
    assert len(_runs(c, "review")) == 1
    assert _runs(c, "late") == []  # like a live rule: no backfill of consumed events


def test_an_event_not_yet_consumed_at_the_backup_is_decided_by_the_rules_of_now():
    src = cluster(fix_rule(), follow_rule(condition=rule_is("fix")))
    settle(src)
    cycles(src, 1)
    src.nodes["spark"].firing.run_events.poll()
    c = _restore(src)
    c.define(follow_rule("late", SUCCEEDED, rule_is("fix")))
    c.start()
    cycles(c, 4)
    assert len(_runs(c, "review")) == 1 and len(_runs(c, "late")) == 1


def test_a_keyed_intent_pending_at_the_backup_starts_once_after_the_restore():
    key = "pr:{trigger.data.repository}#{trigger.data.number}"
    src = cluster(fix_rule(), follow_rule(condition=rule_is("fix"), concurrency_key=key))
    settle(src)
    cycles(src, 1)
    node = src.nodes["spark"]
    node.firing.run_events.poll()
    for consumer in (node.firing.placed, node.firing.shared):
        node.firing.poll(consumer)  # the keyed review intent and its reservation commit
    assert src.base.find("rule_fires", {"rule_id": "review", "status": "pending"})
    c = _restore(src)
    c.start()
    cycles(c, 4)
    assert len(_runs(c, "review")) == 1


def test_reopening_is_batched():
    from culture_rules.engine.run_completions import REOPEN_BATCH, reopen_undelivered

    store = MemoryStore()
    for i in range(REOPEN_BATCH * 2 + 3):
        store.put(RUN_COMPLETIONS, {"id": f"r{i:05d}", "emitted": True, "event_id": f"e{i}"})
    assert reopen_undelivered(store, limit=REOPEN_BATCH) == REOPEN_BATCH
    assert reopen_undelivered(store) == REOPEN_BATCH + 3
    assert store.find(RUN_COMPLETIONS, {"emitted": True}) == []


# #1 due parked records are never hidden --------------------------------------


def test_a_due_parked_record_is_retried_behind_twenty_backing_off_ones():
    store = MemoryStore()
    for i in range(30):
        store.put(
            RUN_COMPLETIONS,
            {
                "id": f"a{i:03d}",
                "emitted": False,
                "blocked": True,
                "retry_at": "2999-01-01T00:00:00+00:00",
                "envelope": {},
            },
        )
    store.put(
        RUN_COMPLETIONS,
        {
            "id": "zzz",
            "emitted": False,
            "blocked": True,
            "retry_at": "2000-01-01T00:00:00+00:00",
            "envelope": {},
        },
    )
    from datetime import UTC, datetime

    outbox = RunEventOutbox(store, paused=lambda tx: False, defer=Exception)
    due = outbox.parked_due(datetime(2026, 10, 8, tzinfo=UTC))
    assert [r["id"] for r in due] == ["zzz"]


# #4 legacy records are migrated, not starved ---------------------------------


def test_legacy_records_without_blocked_are_migrated_and_not_starved():
    store = MemoryStore()
    for i in range(150):
        store.put(
            RUN_COMPLETIONS, {"id": f"a{i:03d}", "emitted": False, "blocked": False, "envelope": {}}
        )
    store.put(RUN_COMPLETIONS, {"id": "legacy", "emitted": False, "envelope": {}})
    RunEventOutbox(store, paused=lambda tx: False, defer=Exception)
    assert store.get(RUN_COMPLETIONS, "legacy")["blocked"] is False


# #5 conflict records are bounded ---------------------------------------------


def test_a_conflict_record_for_a_huge_run_id_is_bounded():
    c = cluster(fix_rule(), verdict="no")
    settle(c)
    cycles(c, 1)
    record = completion(c)
    huge_id = "run-" + "x" * 20_000
    c.base.put(RUN_COMPLETIONS, {**record, "id": huge_id, "run_id": huge_id})
    _squat(c, record["envelope"]["id"], record["envelope"])
    with c.base.transaction() as tx:
        deliver(tx, huge_id)
    for rec in c.base.find(QUARANTINE_COLLECTION):
        assert len(json.dumps(rec, default=str)) <= QUARANTINE_MAX_RECORD
        assert len(rec["id"]) <= 64


def test_bounded_record_enforces_the_final_size_whatever_the_fields():
    from culture_rules.events.ingest import bounded_record

    doc = {"id": "q_1", "reason": "r" * 50_000, "host": "h" * 50_000, "count": 1}
    out = bounded_record(doc)
    assert len(json.dumps(out, default=str)) <= QUARANTINE_MAX_RECORD
    assert out["id"] == "q_1" and out["count"] == 1
