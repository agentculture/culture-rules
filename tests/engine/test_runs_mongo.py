"""The run executor on the MongoDB StoragePort adapter (marker ``mongo``; skips without docker).

Reuses the storage task's replica-set binding, like the claims contract does.
"""

from __future__ import annotations

import contextlib

import pytest

from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.claims import idempotency_key
from culture_rules.engine.runs import RUN_COLLECTIONS, Containment, Executor, step_state
from culture_rules.store.port import TransientStoreError
from tests.engine.run_helpers import Clock, human_actor_for_mongo, ports_for, rule
from tests.engine.run_helpers import three_step_workflow_for_mongo as three_steps

pytest.importorskip("pymongo", reason="pymongo (culture-rules[store]) is not installed")
_mongo_tests = pytest.importorskip("tests.store.test_mongo", reason="Mongo binding missing")

pytestmark = pytest.mark.mongo


@pytest.fixture
def mongo_store():
    _mongo_tests.get_rig()  # skips when docker / the image is unavailable
    binding = _mongo_tests.TestMongoStore()
    store = binding.make_store()
    yield store
    _mongo_tests._release_stores()


def test_run_survives_restart_and_cancel_on_mongo(mongo_store):
    clock = Clock()
    a = human_actor_for_mongo()
    ex = Executor(mongo_store, "spark", ports_for(a), clock=clock)
    assert {"runs", "claims", "audit", "controls"} <= set(RUN_COLLECTIONS)
    run = ex.start(rule(), three_steps())
    ex.run_until_idle()
    assert step_state(ex.run(run["id"]), "s2")["status"] == "waiting"

    again = Executor(mongo_store, "spark", ports_for(a), clock=clock)
    assert again.deliver(idempotency_key(run["id"], "s2"), InvocationResult.completed({"ok": True}))
    again.run_until_idle()
    assert again.run(run["id"])["status"] == "succeeded"
    assert a.effects_for("s1") == 1
    assert a.effects_for("s3") == 1

    second = again.start(rule(), three_steps())
    again.run_until_idle()
    Containment(mongo_store, clock=clock).cancel(second["id"], "alice")
    assert again.run(second["id"])["status"] == "cancelled"


def test_completion_record_outbox_and_verification_on_mongo(mongo_store):
    """d21: the terminal CAS and the completion record commit together on a real replica
    set; the outbox delivers once; a stored copy verifies only when identical."""
    from culture_rules.engine.run_completions import RUN_COMPLETIONS
    from culture_rules.events.ingest import EVENTS_COLLECTION, QUARANTINE_COLLECTION
    from culture_rules.node.run_events import RunEventOutbox, verify_run_event

    clock = Clock()
    a = human_actor_for_mongo()
    ex = Executor(mongo_store, "spark", ports_for(a), clock=clock)
    run = ex.start(rule(), three_steps())
    ex.run_until_idle()
    Containment(mongo_store, clock=clock).cancel(run["id"], "alice")
    record = mongo_store.get(RUN_COMPLETIONS, run["id"])
    assert record["status"] == "cancelled" and record["emitted"] is False
    mongo_store.ensure_collections(EVENTS_COLLECTION, QUARANTINE_COLLECTION)
    outbox = RunEventOutbox(mongo_store, paused=lambda tx: False, defer=Exception)
    assert outbox.poll() == [record["envelope"]["id"]]
    assert outbox.poll() == []
    stored = mongo_store.get(EVENTS_COLLECTION, record["envelope"]["id"])["envelope"]
    with mongo_store.transaction() as tx:
        assert verify_run_event(tx, stored) is None
        assert verify_run_event(tx, {**stored, "hops": 0}) is not None


# --------------------------------------------------------------- d21 round 2 (Mongo)


def _waiting_run(mongo_store, clock):
    a = human_actor_for_mongo()
    ex = Executor(mongo_store, "spark", ports_for(a), clock=clock)
    run = ex.start(rule(), three_steps())
    ex.run_until_idle()
    assert step_state(ex.run(run["id"]), "s2")["status"] == "waiting"
    return ex, run


def test_a_failure_after_the_record_rolls_back_the_terminal_transition_on_mongo(
    mongo_store, monkeypatch
):
    import culture_rules.engine.runs as runs
    from culture_rules.engine.run_completions import RUN_COMPLETIONS

    clock = Clock()
    ex, run = _waiting_run(mongo_store, clock)
    real = runs.record_completion

    def record_then_fail(tx, before, after, now=None):
        real(tx, before, after, now)
        raise RuntimeError("died after writing the record, before commit")

    monkeypatch.setattr(runs, "record_completion", record_then_fail)
    with pytest.raises(RuntimeError):
        Containment(mongo_store, clock=clock).cancel(run["id"], "alice")
    assert ex.run(run["id"])["status"] == "running"
    assert mongo_store.get(RUN_COMPLETIONS, run["id"]) is None


def test_overlapping_deliveries_store_one_event_on_mongo(mongo_store, monkeypatch):
    import threading

    from culture_rules.engine.run_completions import RUN_COMPLETIONS
    from culture_rules.events.ingest import EVENTS_COLLECTION, QUARANTINE_COLLECTION
    from culture_rules.node import run_events
    from culture_rules.node.run_events import RunEventOutbox, deliver

    clock = Clock()
    ex, run = _waiting_run(mongo_store, clock)
    Containment(mongo_store, clock=clock).cancel(run["id"], "alice")
    mongo_store.ensure_collections(EVENTS_COLLECTION, QUARANTINE_COLLECTION)
    both_read = threading.Barrier(2, timeout=10)
    real_doc = run_events.event_document

    def after_both_read(envelope, **kw):
        both_read.wait()  # both transactions have read: record pending, no event yet
        return real_doc(envelope, **kw)

    monkeypatch.setattr(run_events, "event_document", after_both_read)
    outcomes: list[object] = []

    def one():
        try:
            with mongo_store.transaction() as tx:
                outcomes.append(deliver(tx, run["id"]))
        except Exception as exc:  # noqa: BLE001 - the loser's conflict is the point
            outcomes.append(exc)

    threads = [threading.Thread(target=one) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    monkeypatch.undo()
    won = [o for o in outcomes if isinstance(o, str)]
    assert len(won) == 1, outcomes
    record = mongo_store.get(RUN_COMPLETIONS, run["id"])
    assert record["emitted"] is True and record["event_id"] == won[0]
    assert len([e for e in mongo_store.find(EVENTS_COLLECTION) if e["id"] == won[0]]) == 1
    assert RunEventOutbox(mongo_store, paused=lambda tx: False, defer=Exception).poll() == []


@pytest.mark.parametrize("first", ["complete", "cancel"])
def test_cancel_racing_completion_leaves_one_matching_record_on_mongo(
    mongo_store, monkeypatch, first
):
    import threading

    import culture_rules.engine.runs as runs
    from culture_rules.engine.run_completions import RUN_COMPLETIONS

    clock = Clock()
    ex, run = _waiting_run(mongo_store, clock)
    inside, release = threading.Event(), threading.Event()
    real = runs.record_completion
    holder: list[str] = []

    def held(tx, before, after, now=None):
        if after.get("status") in runs.RUN_DONE and not holder:
            holder.append(after["status"])
            inside.set()  # this transition has written the run in its transaction ...
            release.wait(timeout=10)  # ... and holds there while the other one runs
        return real(tx, before, after, now)

    monkeypatch.setattr(runs, "record_completion", held)

    def complete():
        ex.deliver(idempotency_key(run["id"], "s2"), InvocationResult.completed({"ok": True}))
        ex.run_until_idle()

    def cancel():
        Containment(mongo_store, clock=clock).cancel(run["id"], "alice")

    order = (complete, cancel) if first == "complete" else (cancel, complete)
    errors: list[BaseException] = []

    def guarded(fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - the loser may conflict
            errors.append(exc)

    a = threading.Thread(target=guarded, args=(order[0],))
    a.start()
    assert inside.wait(timeout=20)
    b = threading.Thread(target=guarded, args=(order[1],))
    b.start()
    b.join(timeout=5)  # the second transition conflicts (or waits) while the first holds
    release.set()
    a.join(timeout=20)
    b.join(timeout=20)
    # the transition that got in first wins; the cancel that ran into it lost on a write
    # conflict, and a completion that ran into a cancel finds the run cancelled
    assert holder == (["succeeded"] if first == "complete" else ["cancelled"])
    if first == "complete":
        assert errors and all(isinstance(e, TransientStoreError) for e in errors)
    monkeypatch.undo()
    with contextlib.suppress(Exception):
        ex.run_until_idle()  # settle whatever the loser left (a run still running ticks on)
    doc = ex.run(run["id"])
    records = [r for r in mongo_store.find(RUN_COMPLETIONS) if r["run_id"] == run["id"]]
    assert doc["status"] == holder[0]
    assert len(records) == 1
    assert records[0]["status"] == doc["status"]
    assert records[0]["envelope"]["data"]["status"] == doc["status"]


def test_the_pending_and_ttl_indexes_exist_and_are_used_on_mongo(mongo_store):
    from culture_rules.engine.run_completions import RUN_COMPLETIONS
    from culture_rules.events.ingest import (
        QUARANTINE_COLLECTION,
        QUARANTINE_TTL_INDEX,
        ensure_quarantine_ttl,
        quarantine,
    )
    from culture_rules.node.run_events import PENDING_INDEX, RunEventOutbox

    mongo_store.ensure_collections(RUN_COMPLETIONS, QUARANTINE_COLLECTION)
    RunEventOutbox(mongo_store, paused=lambda tx: False, defer=Exception)
    ensure_quarantine_ttl(mongo_store)
    db = mongo_store._db
    pending = {i["name"]: i for i in db[RUN_COMPLETIONS].list_indexes()}[PENDING_INDEX]
    assert pending["partialFilterExpression"] == {"emitted": False}
    for query in ({"emitted": False, "blocked": False}, {"emitted": False, "blocked": None}):
        plan = db[RUN_COMPLETIONS].find(query).sort("_id", 1).limit(100).explain()
        assert PENDING_INDEX in str(plan["queryPlanner"]["winningPlan"]), query
    ttl = {i["name"]: i for i in db[QUARANTINE_COLLECTION].list_indexes()}[QUARANTINE_TTL_INDEX]
    assert ttl["expireAfterSeconds"] == 0
    quarantine(mongo_store, {"id": "runevt_q", "type": "rules.run.failed"}, "reserved", host="h")
    quarantine(
        mongo_store, {"id": "runevt_q", "type": "rules.run.failed", "x": 1}, "reserved", host="h"
    )
    (rec,) = mongo_store.find(QUARANTINE_COLLECTION)
    from datetime import datetime

    assert isinstance(rec["expires_at"], datetime) and rec["count"] == 2


def test_parked_records_due_first_and_legacy_migration_on_mongo(mongo_store):
    from datetime import UTC, datetime

    from culture_rules.engine.run_completions import RUN_COMPLETIONS
    from culture_rules.node.run_events import PARKED_INDEX, RunEventOutbox

    mongo_store.ensure_collections(RUN_COMPLETIONS)
    for i in range(30):
        mongo_store.put(
            RUN_COMPLETIONS,
            {
                "id": f"a{i:03d}",
                "emitted": False,
                "blocked": True,
                "retry_at": "2999-01-01T00:00:00+00:00",
                "envelope": {},
            },
        )
    mongo_store.put(
        RUN_COMPLETIONS,
        {"id": "zzz", "emitted": False, "blocked": True, "retry_at": "2000-01-01", "envelope": {}},
    )
    mongo_store.put(RUN_COMPLETIONS, {"id": "legacy", "emitted": False, "envelope": {}})
    outbox = RunEventOutbox(mongo_store, paused=lambda tx: False, defer=Exception)
    assert mongo_store.get(RUN_COMPLETIONS, "legacy")["blocked"] is False
    due = outbox.parked_due(datetime(2026, 10, 8, tzinfo=UTC))
    assert [r["id"] for r in due] == ["zzz"]
    plan = (
        mongo_store._db[RUN_COMPLETIONS]
        .find({"emitted": False, "blocked": True, "retry_at": {"$lte": "2026-10-08"}})
        .sort([("retry_at", 1), ("_id", 1)])
        .limit(20)
        .explain()
    )
    assert PARKED_INDEX in str(plan["queryPlanner"]["winningPlan"])


def test_find_range_pages_by_id_and_limits_like_find_on_mongo(mongo_store):
    mongo_store.ensure_collections("run_completions")
    for i in range(5):
        mongo_store.put("run_completions", {"id": f"r{i}", "emitted": True})
    page = mongo_store.find_range(
        "run_completions", {"emitted": True}, field="id", after="r1", limit=2
    )
    assert [d["id"] for d in page] == ["r2", "r3"]
    assert mongo_store.find_range("run_completions", None, field="id", limit=0) == []
    with pytest.raises(ValueError):
        mongo_store.find_range("run_completions", None, field="id", limit=-1)
