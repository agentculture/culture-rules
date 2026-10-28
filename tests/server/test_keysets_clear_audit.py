"""An applied keysets clear writes one audit entry; a dry run writes none (deviation d1)."""

from datetime import UTC, datetime

import pytest

from culture_rules import keysets
from culture_rules.engine.audit import AUDIT_COLLECTION
from culture_rules.server.service import Keysets
from culture_rules.store.memory import MemoryStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _service():
    store = MemoryStore()
    keysets.add_keys(store, "links", ["a", "b"], NOW, 86400)
    return store, Keysets(store, clock=lambda: NOW)


def _entries(store):
    return [e for e in store.find(AUDIT_COLLECTION) if e.get("verb") == "keysets.clear"]


def test_a_dry_run_writes_no_audit_entry():
    store, svc = _service()
    svc.clear("links", dry_run=True, identity="alice")
    assert _entries(store) == []


def test_an_applied_clear_writes_one_entry():
    store, svc = _service()
    svc.clear("links", dry_run=False, identity="alice")
    assert len(_entries(store)) == 1


def test_the_entry_names_who_cleared():
    store, svc = _service()
    svc.clear("links", dry_run=False, identity="alice")
    assert _entries(store)[0]["identity"] == "alice"


def test_the_entry_targets_the_set():
    store, svc = _service()
    svc.clear("links", dry_run=False, identity="alice")
    assert _entries(store)[0]["target"]["id"] == "links"


def test_the_entry_records_the_count_cleared():
    store, svc = _service()
    svc.clear("links", dry_run=False, identity="alice")
    assert _entries(store)[0]["diff"]["keys"]["before"] == 2


def test_a_set_clear_says_the_whole_set():
    store, svc = _service()
    svc.clear("links", dry_run=False, identity="alice")
    assert _entries(store)[0]["diff"]["cleared"]["after"] == "the whole set"


def test_a_key_clear_records_the_key():
    store, svc = _service()
    svc.clear("links", "a", dry_run=False, identity="alice")
    assert _entries(store)[0]["diff"]["cleared"]["after"] == "a"


def test_an_applied_clear_needs_an_identity():
    _, svc = _service()
    with pytest.raises(ValueError):
        svc.clear("links", dry_run=False, identity="")
