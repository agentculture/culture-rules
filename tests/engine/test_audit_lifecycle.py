"""Audit log (insert-only) and soft delete / restore / purge lifecycle (t14)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.engine import audit as audit_mod
from culture_rules.engine.audit import AUDIT_COLLECTION, MUTATING_VERBS, AuditError, AuditLog, diff
from culture_rules.engine.lifecycle import (
    RETENTION,
    Lifecycle,
    LifecycleError,
    PermissionDenied,
)
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import RUN_AUDIT_SCENARIOS
from tests.server.scenarios import SERVER_AUDIT_SCENARIOS

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> MemoryStore:
    s = MemoryStore()
    s.put("rules", {"id": "r1", "name": "first", "enabled": True})
    return s


@pytest.fixture
def life(store, clock) -> Lifecycle:
    log = AuditLog(host="host-a", clock=clock)
    return Lifecycle(store, log, admins={"root"}, clock=clock)


def entries(store):
    return store.find(AUDIT_COLLECTION)


# --- audit ---------------------------------------------------------------------------------


def test_diff_reports_changed_added_removed_fields():
    d = diff({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 5, "d": 4})
    assert d == {
        "b": {"before": 2, "after": 5},
        "c": {"before": 3, "after": None},
        "d": {"before": None, "after": 4},
    }


def test_diff_ignores_envelope_noise():
    assert diff({"id": "x", "updated_at": "1"}, {"id": "x", "updated_at": "2"}) == {}


def test_entry_carries_identity_host_time_and_diff(store, life, clock):
    life.soft_delete("rules", "r1", "alice")
    (e,) = entries(store)
    assert e["identity"] == "alice"
    assert e["host"] == "host-a"
    assert e["at"] == T0.isoformat(timespec="microseconds")
    assert e["verb"] == "lifecycle.soft_delete"
    assert e["target"] == {"collection": "rules", "id": "r1"}
    assert "deleted_at" in e["diff"]


@pytest.mark.parametrize("bad", ["", None, 5, "   "])
def test_empty_identity_rejected_and_nothing_mutates(store, life, bad):
    with pytest.raises(AuditError):
        life.soft_delete("rules", "r1", bad)
    assert entries(store) == []
    assert store.get("rules", "r1").get("deleted_at") is None


def test_audit_api_is_insert_only():
    public = {n for n in dir(AuditLog) if not n.startswith("_")}
    assert not {n for n in public if n.startswith(("update", "delete", "remove", "put", "edit"))}
    assert not hasattr(audit_mod, "delete_entry") and not hasattr(audit_mod, "update_entry")


def test_audit_entries_reader_returns_entries_in_time_order(store, life, clock):
    life.soft_delete("rules", "r1", "alice")
    clock.now += timedelta(minutes=1)
    life.restore("rules", "r1", "bob")
    got = AuditLog.entries(store, target_id="r1")
    assert [e["identity"] for e in got] == ["alice", "bob"]


def _scenario_soft_delete(life, store):
    return lambda: life.soft_delete("rules", "r1", "alice")


def _scenario_restore(life, store):
    life.soft_delete("rules", "r1", "alice")
    return lambda: life.restore("rules", "r1", "alice")


def _scenario_purge(life, store):
    life.soft_delete("rules", "r1", "alice")
    return lambda: life.purge("rules", "r1", "root", apply=True)


SCENARIOS = {
    "lifecycle.soft_delete": _scenario_soft_delete,
    "lifecycle.restore": _scenario_restore,
    "lifecycle.purge": _scenario_purge,
    "runs.start": RUN_AUDIT_SCENARIOS["runs.start"],
    "runs.cancel": RUN_AUDIT_SCENARIOS["runs.cancel"],
    "engine.pause": RUN_AUDIT_SCENARIOS["engine.pause"],
    "engine.resume": RUN_AUDIT_SCENARIOS["engine.resume"],
    "machine.drain": RUN_AUDIT_SCENARIOS["machine.drain"],
    "machine.undrain": RUN_AUDIT_SCENARIOS["machine.undrain"],
    **SERVER_AUDIT_SCENARIOS,
}


def test_registry_scenarios_cover_every_registered_verb():
    assert set(SCENARIOS) == set(MUTATING_VERBS)


@pytest.mark.parametrize("verb", sorted(SCENARIOS))
def test_every_mutating_verb_writes_exactly_one_entry_with_identity(verb, store, life):
    call = SCENARIOS[verb](life, store)
    before = len(entries(store))
    call()
    new = entries(store)
    assert len(new) == before + 1
    mine = [e for e in new if e["verb"] == verb]
    assert len(mine) == 1
    assert isinstance(mine[0]["identity"], str) and mine[0]["identity"].strip()


# --- lifecycle -----------------------------------------------------------------------------


def test_soft_delete_tombstones_and_keeps_document(store, life, clock):
    life.soft_delete("rules", "r1", "alice")
    doc = store.get("rules", "r1")
    assert doc["name"] == "first"
    assert doc["deleted_by"] == "alice"
    assert datetime.fromisoformat(doc["restorable_until"]) == T0 + RETENTION
    assert RETENTION == timedelta(days=30)
    assert Lifecycle.is_deleted(doc)


def test_deleted_item_never_fires(store, life):
    assert life.is_fireable("rules", "r1")
    life.soft_delete("rules", "r1", "alice")
    assert not life.is_fireable("rules", "r1")
    with pytest.raises(LifecycleError):
        life.assert_fireable("rules", "r1")
    assert not life.is_fireable("rules", "missing")


def test_soft_delete_twice_or_missing_is_an_error_and_writes_no_entry(store, life):
    life.soft_delete("rules", "r1", "alice")
    with pytest.raises(LifecycleError):
        life.soft_delete("rules", "r1", "alice")
    with pytest.raises(LifecycleError):
        life.soft_delete("rules", "nope", "alice")
    assert len(entries(store)) == 1


def test_restore_within_window_brings_item_back_with_history(store, life, clock):
    life.soft_delete("rules", "r1", "alice")
    clock.now += timedelta(days=29)
    life.restore("rules", "r1", "bob")
    doc = store.get("rules", "r1")
    assert not Lifecycle.is_deleted(doc)
    assert doc["name"] == "first"
    assert life.is_fireable("rules", "r1")
    assert [e["verb"] for e in AuditLog.entries(store, target_id="r1")] == [
        "lifecycle.soft_delete",
        "lifecycle.restore",
    ]


def test_restore_after_30_days_refused(store, life, clock):
    life.soft_delete("rules", "r1", "alice")
    clock.now += timedelta(days=30, seconds=1)
    with pytest.raises(LifecycleError, match="expired"):
        life.restore("rules", "r1", "bob")
    assert Lifecycle.is_deleted(store.get("rules", "r1"))
    assert len(entries(store)) == 1


def test_restore_of_live_item_is_an_error(store, life):
    with pytest.raises(LifecycleError):
        life.restore("rules", "r1", "bob")


def test_purge_requires_admin(store, life):
    life.soft_delete("rules", "r1", "alice")
    with pytest.raises(PermissionDenied):
        life.purge("rules", "r1", "alice", apply=True)
    assert store.get("rules", "r1") is not None
    assert len(entries(store)) == 1


def test_purge_is_dry_run_without_apply(store, life):
    life.soft_delete("rules", "r1", "alice")
    plan = life.purge("rules", "r1", "root")
    assert plan.applied is False
    assert store.get("rules", "r1") is not None
    assert len(entries(store)) == 1


def test_purge_only_applies_to_tombstoned_items(store, life):
    with pytest.raises(LifecycleError):
        life.purge("rules", "r1", "root", apply=True)
    assert store.get("rules", "r1") is not None


def test_purge_apply_removes_item_but_history_remains(store, life):
    life.soft_delete("rules", "r1", "alice")
    result = life.purge("rules", "r1", "root", apply=True)
    assert result.applied is True
    assert store.get("rules", "r1") is None
    got = AuditLog.entries(store, target_id="r1")
    assert [e["verb"] for e in got] == ["lifecycle.soft_delete", "lifecycle.purge"]
    assert got[-1]["identity"] == "root"
    assert got[-1]["diff"]["name"] == {"before": "first", "after": None}


def test_non_admin_dry_run_is_also_denied(store, life):
    life.soft_delete("rules", "r1", "alice")
    with pytest.raises(PermissionDenied):
        life.purge("rules", "r1", "alice")
