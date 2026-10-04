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


# --------------------------------------------------------------------------- t18: typeless rules

from culture_rules.store.migrations import (  # noqa: E402
    disable_typeless_event_rules,
    find_typeless_event_rules,
)

RULES = "rules"


def _rule(id: str, kind: str = "event", params=None, **extra) -> dict:
    return {"id": id, "name": id, "trigger": {"kind": kind, "params": params or {}}, **extra}


def _seed(store) -> None:
    store.put(RULES, _rule("empty", params={}, enabled=True))
    store.put(RULES, _rule("blank", params={"type": ""}, enabled=True))
    store.put(RULES, _rule("num", params={"type": 5}, enabled=True))
    store.put(RULES, _rule("noparams-enabled-missing"))
    store.put(RULES, _rule("typed", params={"type": "github.pr"}, enabled=True))
    store.put(RULES, _rule("sched", kind="schedule", params={"cron": "* * * * *"}, enabled=True))
    store.put(RULES, _rule("off", params={}, enabled=False))
    store.put(RULES, _rule("gone", params={}, enabled=True, deleted_at="2026-01-01T00:00:00Z"))


def test_finder_lists_live_typeless_event_rules_only():
    store = MemoryStore()
    _seed(store)
    ids = sorted(d["id"] for d in find_typeless_event_rules(store))
    assert ids == ["blank", "empty", "noparams-enabled-missing", "num", "off"]


def test_dry_run_changes_nothing_and_reports_enabled_before():
    store = MemoryStore()
    _seed(store)
    before = {d["id"]: dict(d) for d in store.find(RULES)}
    out = disable_typeless_event_rules(store, lambda _id: 1 / 0, dry_run=True)
    assert {r["id"]: r["enabled_before"] for r in out}["off"] is False
    assert {r["id"]: r["enabled_before"] for r in out}["empty"] is True
    assert {d["id"]: dict(d) for d in store.find(RULES)} == before


def test_apply_disables_only_enabled_ones_through_the_callback_and_deletes_nothing():
    store = MemoryStore()
    _seed(store)
    seen: list[str] = []

    def disable(rule_id: str) -> None:
        seen.append(rule_id)
        store.update_if(RULES, rule_id, {}, {"enabled": False})

    out = disable_typeless_event_rules(store, disable, dry_run=False)
    assert sorted(seen) == ["blank", "empty", "noparams-enabled-missing", "num"]
    assert len(out) == 5
    assert len(store.find(RULES)) == 8
