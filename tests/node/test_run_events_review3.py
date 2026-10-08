"""d21 phase 1, Codex round 3: each test reproduces a finding on 2d32cad."""

from __future__ import annotations

import copy
import json

from culture_rules.engine.run_completions import RUN_COMPLETIONS
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.events.ingest import (
    EVENTS_COLLECTION,
    QUARANTINE_COLLECTION,
    QUARANTINE_MAX_RECORD,
    event_document,
    quarantine,
)
from culture_rules.node.run_events import RunEventOutbox, candidate_ids
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

BACKED_UP = ("rules", "workflows", "runs", "audit", "run_completions")


def _restore(source: Cluster) -> Cluster:
    """What a backup restore yields: config and run history only (no events, intents,
    markers or cursors), with the restore's completion re-opening applied."""
    from culture_rules.engine.run_completions import reopen_undelivered

    c = Cluster("spark")
    for coll in BACKED_UP:
        for doc in source.base.find(coll):
            c.base.put(coll, doc)
    reopen_undelivered(c.base)
    return c


def _reviews(c):
    return c.base.find(RUNS_COLLECTION, {"rule_id": "review"})


# #1 a backup between emission and consumption ---------------------------------


def test_a_backup_between_emit_and_consume_restores_to_exactly_one_downstream_run():
    src = cluster(fix_rule(), follow_rule(condition=rule_is("fix")))
    settle(src)
    cycles(src, 1)  # fix finishes
    outbox = src.nodes["spark"].firing.run_events
    outbox.poll()  # the event is stored and the record emitted ... no trigger has run yet
    assert completion(src)["emitted"] is True
    assert _reviews(src) == []
    c = _restore(src)  # ... and the backup is taken right here
    c.start()
    cycles(c, 4)
    assert len(_reviews(c)) == 1


def test_a_backup_after_consumption_never_reruns_the_downstream_rule():
    src = cluster(fix_rule(), follow_rule(condition=rule_is("fix")))
    settle(src)
    cycles(src, 4)
    assert len(_reviews(src)) == 1
    c = _restore(src)
    c.start()
    cycles(c, 4)
    assert len(_reviews(c)) == 1  # the restored one; no second


def test_a_restore_after_consumption_of_an_event_delivered_under_a_fallback_id_reruns_nothing():
    src = cluster(fix_rule(), follow_rule(condition=rule_is("fix")), verdict="no")
    settle(src)
    cycles(src, 1)
    genuine = completion(src)["envelope"]
    squat = {**copy.deepcopy(genuine), "hops": 0}
    src.base.insert(EVENTS_COLLECTION, event_document(squat, host="elsewhere"))
    cycles(src, 4)
    assert completion(src)["event_id"] != genuine["id"]
    assert len(_reviews(src)) == 1
    c = _restore(src)
    c.start()
    cycles(c, 4)
    assert len(_reviews(c)) == 1


# #2 blocked records cannot starve healthy ones --------------------------------


def test_a_hundred_blocked_records_do_not_starve_a_healthy_one():
    c = Cluster("spark")
    c.define(fix_workflow())
    c.start()
    for i in range(100):
        rid = f"run-a{i:03d}"
        env = {"id": f"runevt_a{i:03d}", "type": "rules.run.failed", "data": {"run_id": rid}}
        c.base.put(
            RUN_COMPLETIONS,
            {
                "id": rid,
                "run_id": rid,
                "envelope": env,
                "emitted": False,
                "event_id": None,
                "blocked": False,
            },
        )
        for cand in candidate_ids(env["id"]):
            c.base.insert(EVENTS_COLLECTION, event_document({**env, "id": cand, "x": 1}, host="x"))
    healthy = {"id": "runevt_zzz", "type": "rules.run.failed", "data": {"run_id": "run-zzz"}}
    c.base.put(
        RUN_COMPLETIONS,
        {
            "id": "run-zzz",
            "run_id": "run-zzz",
            "envelope": healthy,
            "emitted": False,
            "event_id": None,
            "blocked": False,
        },
    )
    outbox: RunEventOutbox = c.nodes["spark"].firing.run_events
    outbox.poll()
    outbox.poll()
    assert c.base.get(RUN_COMPLETIONS, "run-zzz")["emitted"] is True


# #3 the TTL index wherever quarantine records are produced --------------------


class IndexSpy(MemoryStore):
    def __init__(self):
        super().__init__()
        self.indexes = []

    def ensure_index(self, collection, keys, **kw):
        self.indexes.append((collection, kw.get("name")))


def test_a_node_installs_the_quarantine_ttl_index_without_an_event_source():
    from culture_rules.events.ingest import QUARANTINE_TTL_INDEX
    from culture_rules.node.daemon import Node

    store = IndexSpy()
    Node(store, "spark", event_source=None)
    assert (QUARANTINE_COLLECTION, QUARANTINE_TTL_INDEX) in store.indexes


def test_the_api_installs_the_quarantine_ttl_index_for_its_webhooks():
    from culture_rules.events.ingest import QUARANTINE_TTL_INDEX
    from culture_rules.server.app import create_app

    store = IndexSpy()
    create_app(store)
    assert (QUARANTINE_COLLECTION, QUARANTINE_TTL_INDEX) in store.indexes


# #4 bounded metadata and record size ------------------------------------------


def test_oversized_id_type_and_source_are_bounded():
    store = MemoryStore()
    huge = "x" * 100_000
    quarantine(store, {"id": huge, "type": huge, "source": huge}, "reserved", host="h")
    (rec,) = store.find(QUARANTINE_COLLECTION)
    assert len(json.dumps(rec, default=str)) <= QUARANTINE_MAX_RECORD
    for field in ("envelope_id", "type", "source"):
        value = rec[field]
        assert isinstance(value, dict)
        assert value["truncated"] is True
        assert value["sha256"]
    # a repeat of the same huge id still lands on the same record
    quarantine(store, {"id": huge, "type": "t"}, "reserved", host="h")
    assert store.find(QUARANTINE_COLLECTION)[0]["count"] == 2


def test_non_string_metadata_is_bounded_too():
    store = MemoryStore()
    quarantine(store, {"id": {"nested": ["y"] * 50_000}, "type": 7}, "reserved", host="h")
    (rec,) = store.find(QUARANTINE_COLLECTION)
    assert len(json.dumps(rec, default=str)) <= QUARANTINE_MAX_RECORD


def test_a_record_without_the_blocked_field_is_still_delivered():
    c = cluster(fix_rule())
    settle(c)
    cycles(c, 1)
    record = completion(c)
    c.base.put(RUN_COMPLETIONS, {k: v for k, v in record.items() if k != "blocked"})
    cycles(c, 1)
    assert completion(c)["emitted"] is True


def test_a_backup_after_the_intent_but_before_the_run_restores_to_exactly_one_run():
    """The window that rules out per-consumer consumption marks: the firing is decided but
    its run has not started when the backup is taken. Re-delivering the event re-decides it
    with the restored rules and the run starts once."""
    src = cluster(fix_rule(), follow_rule(condition=rule_is("fix")))
    settle(src)
    cycles(src, 1)
    node = src.nodes["spark"]
    node.firing.run_events.poll()
    for consumer in (node.firing.placed, node.firing.shared):
        node.firing.poll(consumer)  # the review's intent is committed ...
    assert src.base.find("rule_fires", {"rule_id": "review"})
    assert _reviews(src) == []
    c = _restore(src)  # ... and the backup is taken before any node starts it
    c.start()
    cycles(c, 4)
    assert len(_reviews(c)) == 1
