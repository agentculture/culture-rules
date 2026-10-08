"""d21 phase 1, Codex round 2: each test reproduces a finding on aeb2f68."""

from __future__ import annotations

import copy
import logging
from datetime import datetime

import pytest

from culture_rules.engine.run_completions import RUN_COMPLETIONS, record_completion
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.events.emit import event_hops
from culture_rules.events.hook_sink import sink
from culture_rules.events.ingest import (
    EVENTS_COLLECTION,
    QUARANTINE_COLLECTION,
    QUARANTINE_MAX_PAYLOAD,
    event_document,
    quarantine,
)
from culture_rules.node.run_events import (
    OUTBOX_BATCH,
    RunEventOutbox,
    deliver,
    verify_run_event,
)
from culture_rules.node.runner import open_emitter
from culture_rules.store.memory import MemoryStore
from tests.node.test_node import Cluster
from tests.node.test_run_events import (
    cluster,
    completion,
    cycles,
    fix_rule,
    fix_workflow,
    follow_rule,
    rule_is,
    settle,
)

# #1 first start: cursors before the first outbox drain -----------------------


def test_a_completion_pending_before_the_first_node_start_reaches_its_downstream_rule():
    # an engine finished fix's run and died before delivering it; the mesh then starts a
    # node with no cursors at all (a fresh store restore, or the first node of this version)
    first = cluster(fix_rule(), follow_rule(condition=rule_is("fix")))
    settle(first)
    cycles(first, 1)
    run = first.run("fix", "evt_1")
    assert completion(first)["emitted"] is False
    c = Cluster("spark")
    c.define(fix_workflow(), fix_rule(), follow_rule(condition=rule_is("fix")))
    for coll in (RUNS_COLLECTION, RUN_COMPLETIONS, EVENTS_COLLECTION):
        for doc in first.base.find(coll):
            c.base.put(coll, doc)
    c.start()
    cycles(c, 3)
    assert c.base.find(RUNS_COLLECTION, {"rule_id": "review"}), "the review never fired"
    assert c.base.get(RUN_COMPLETIONS, run["id"])["emitted"] is True


# #2 every candidate id is collision-checked ---------------------------------


def test_conflicts_at_both_the_id_and_the_first_fallback_never_mark_a_wrong_event_emitted():
    c = cluster(fix_rule(), verdict="no")
    settle(c)
    cycles(c, 1)
    record = completion(c)
    genuine = record["envelope"]
    for suffix in ("", "-genuine"):
        squat = copy.deepcopy(genuine)
        squat["id"] = genuine["id"] + suffix
        squat["data"]["outputs"] = {"verdict": "approve"}
        c.base.insert(EVENTS_COLLECTION, event_document(squat, host="elsewhere"))
    with c.base.transaction() as tx:
        event_id = deliver(tx, record["id"])
    stored = c.base.get(EVENTS_COLLECTION, event_id)["envelope"]
    assert stored == {**genuine, "id": event_id}
    assert event_id not in (genuine["id"], genuine["id"] + "-genuine")
    assert len(c.base.find(QUARANTINE_COLLECTION)) == 2
    with c.base.transaction() as tx:
        assert verify_run_event(tx, stored) is None


# #4 the pending query is bounded and server-side -----------------------------


class SpyStore:
    def __init__(self, inner):
        self.inner = inner
        self.finds = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def find(self, collection, where=None, *, limit=None):
        self.finds.append((collection, dict(where or {}), limit))
        return self.inner.find(collection, where, limit=limit)


def test_the_pending_query_is_limited_by_the_store():
    store = MemoryStore()
    for i in range(OUTBOX_BATCH + 50):
        store.put(RUN_COMPLETIONS, {"id": f"run-{i:04d}", "emitted": False, "envelope": {}})
    for i in range(500):
        store.put(RUN_COMPLETIONS, {"id": f"old-{i:04d}", "emitted": True, "envelope": {}})
    spy = SpyStore(store)
    outbox = RunEventOutbox(spy, paused=lambda tx: False, defer=Exception)
    assert len(outbox.pending()) == OUTBOX_BATCH
    assert spy.finds == [(RUN_COMPLETIONS, {"emitted": False}, OUTBOX_BATCH)]


# #5 quarantine is bounded ----------------------------------------------------


def test_quarantine_keeps_one_record_per_id_and_reason_and_counts_repeats(caplog):
    store = MemoryStore()
    env = {"id": "runevt_x", "type": "rules.run.succeeded", "data": {"n": 1}}
    caplog.set_level(logging.WARNING)
    for n in range(5):
        quarantine(store, {**env, "data": {"n": n}}, "reserved", host="h")
    (rec,) = store.find(QUARANTINE_COLLECTION)
    assert rec["count"] == 5
    assert isinstance(rec["expires_at"], datetime)
    assert sum("quarantined" in r.message for r in caplog.records) == 1


def test_quarantine_caps_the_payload():
    store = MemoryStore()
    big = {"id": "evt_big", "type": "rules.run.succeeded", "data": {"x": "y" * 100_000}}
    quarantine(store, big, "reserved", host="h")
    (rec,) = store.find(QUARANTINE_COLLECTION)
    assert "envelope" not in rec
    assert rec["truncated"] is True and rec["size"] > QUARANTINE_MAX_PAYLOAD
    assert len(rec["preview"]) <= QUARANTINE_MAX_PAYLOAD


# #6 every engine source is internal ------------------------------------------


def test_a_derived_event_from_the_nodes_real_emitter_without_hops_fails_closed():
    source = open_emitter(MemoryStore(), "spark").source
    derived = {"id": "e", "type": "human.ask.answered", "source": source, "causationId": "c"}
    assert event_hops(derived) is None
    assert event_hops({**derived, "hops": 2}) == 2
    root = {"id": "e", "type": "t", "source": source}
    assert event_hops(root) == 0


# #7 webhook refusals of the reserved namespace are quarantined ---------------


def test_a_webhook_carrying_a_reserved_type_is_quarantined():
    store = MemoryStore()
    actor = {
        "id": "gh",
        "kind": "app",
        "enabled": True,
        "params": {"surface": "github", "events": ["rules.run.succeeded"]},
    }
    assert sink(store, actor, "rules.run.succeeded", {"n": 1}, "d1", "x") == "quarantined"
    assert store.find(EVENTS_COLLECTION) == []
    (rec,) = store.find(QUARANTINE_COLLECTION)
    assert rec["reason"]
    # an ordinary undeclared type is still just ignored, not quarantined
    assert sink(store, actor, "github.other", {"n": 1}, "d2", "x") == "ignored"
    assert len(store.find(QUARANTINE_COLLECTION)) == 1


# verification with the genuine id kept, one field changed ---------------------


@pytest.mark.parametrize(
    "path,value",
    [
        (("type",), "rules.run.failed"),
        (("source",), "agent://x"),
        (("time",), "2030-01-01T00:00:00Z"),
        (("correlationId",), "other"),
        (("causationId",), "other"),
        (("runId",), "run-other"),
        (("hops",), 0),
        (("schemaVersion",), "2"),
        (("data", "outputs", "verdict"), "approve"),
        (("data", "rule_id"), "trusted"),
        (("data", "workflow_version"), 99),
        (("data", "number"), 8),
        (("data", "status"), "failed"),
        (("extra",), True),
    ],
)
def test_verification_refuses_one_changed_field_under_the_genuine_id(path, value):
    c = cluster(fix_rule(), verdict="no")
    settle(c)
    cycles(c, 2)
    record = completion(c)
    stored = copy.deepcopy(c.base.get(EVENTS_COLLECTION, record["event_id"])["envelope"])
    target = stored
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with c.base.transaction() as tx:
        assert verify_run_event(tx, stored) is not None


def test_verification_refuses_a_removed_field_under_the_genuine_id():
    c = cluster(fix_rule())
    settle(c)
    cycles(c, 2)
    record = completion(c)
    stored = dict(c.base.get(EVENTS_COLLECTION, record["event_id"])["envelope"])
    stored.pop("hops")
    with c.base.transaction() as tx:
        assert verify_run_event(tx, stored) is not None


def test_record_completion_writes_nothing_for_a_non_terminal_or_repeated_transition():
    store = MemoryStore()
    run = {"id": "r", "status": "running", "rule_id": "x", "trigger": {}}
    with store.transaction() as tx:
        assert record_completion(tx, run, run) is False
        assert record_completion(tx, run, {**run, "status": "failed"}) is True
        assert record_completion(tx, run, {**run, "status": "succeeded"}) is False  # first wins
    assert store.get(RUN_COMPLETIONS, "r")["status"] == "failed"


def test_the_outbox_initialises_missing_trigger_cursors_before_it_drains():
    from culture_rules.store.port import CURSOR_COLLECTION

    c = cluster(fix_rule(), follow_rule(condition=rule_is("fix")))
    settle(c)
    cycles(c, 1)
    assert completion(c)["emitted"] is False
    # a trigger consumer whose cursor was never initialised (its pin failed at start)
    for cur in c.base.find(CURSOR_COLLECTION):
        if cur["collection"] == EVENTS_COLLECTION:
            c.base.delete(CURSOR_COLLECTION, cur["id"])
    cycles(c, 3)
    assert c.base.find(RUNS_COLLECTION, {"rule_id": "review"})


def test_a_record_with_every_candidate_taken_stays_pending_and_others_still_deliver():
    from culture_rules.node.run_events import candidate_ids

    c = cluster(fix_rule(), verdict="no")
    settle(c)
    settle(c, 2, number=8)
    cycles(c, 1)
    first = completion(c)
    for event_id in candidate_ids(first["envelope"]["id"]):
        squat = {**copy.deepcopy(first["envelope"]), "id": event_id, "hops": 0}
        c.base.insert(EVENTS_COLLECTION, event_document(squat, host="elsewhere"))
    cycles(c, 2)
    assert completion(c)["emitted"] is False  # never marked with a wrong event
    assert len(c.base.find(QUARANTINE_COLLECTION)) == len(candidate_ids("x"))
    other = completion(c, "fix", "evt_2")
    assert other["emitted"] is True
