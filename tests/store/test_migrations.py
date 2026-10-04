"""t17: the run-doc backfill of top-level rule_id / workflow_id is idempotent."""

from __future__ import annotations

from culture_rules.store.memory import MemoryStore
from culture_rules.store.migrations import backfill_run_ids

RUNS = "runs"


def _legacy(id: str, rule: str | None = "r1", workflow: str | None = "w1") -> dict:
    doc: dict = {"id": id, "status": "succeeded", "steps": []}
    if rule:
        doc["rule"] = {"id": rule}
    if workflow:
        doc["workflow"] = {"id": workflow}
    return doc


def test_backfill_fills_both_fields_on_legacy_docs():
    store = MemoryStore()
    store.put(RUNS, _legacy("a"))
    store.put(RUNS, _legacy("b", workflow=None))
    assert backfill_run_ids(store) == 2
    a, b = store.get(RUNS, "a"), store.get(RUNS, "b")
    assert (a["rule_id"], a["workflow_id"]) == ("r1", "w1")
    assert (b["rule_id"], b["workflow_id"]) == ("r1", None)
    assert a["rule"] == {"id": "r1"}  # the pinned copy is untouched


def test_backfill_is_idempotent():
    store = MemoryStore()
    store.put(RUNS, _legacy("a"))
    store.put(RUNS, _legacy("b", workflow=None))
    store.put(RUNS, {"id": "orphan", "status": "failed"})  # no rule to copy from
    assert backfill_run_ids(store) == 2
    before = {d["id"]: d for d in store.find(RUNS)}
    assert backfill_run_ids(store) == 0
    assert {d["id"]: d for d in store.find(RUNS)} == before


def test_backfill_leaves_docs_that_already_have_the_fields():
    store = MemoryStore()
    store.put(RUNS, {**_legacy("new"), "rule_id": "r1", "workflow_id": "w1"})
    before = store.get(RUNS, "new")
    assert backfill_run_ids(store) == 0
    assert store.get(RUNS, "new") == before


def test_backfill_dry_run_counts_without_writing():
    store = MemoryStore()
    store.put(RUNS, _legacy("a"))
    assert backfill_run_ids(store, dry_run=True) == 1
    assert "rule_id" not in store.get(RUNS, "a")
    assert backfill_run_ids(store) == 1
