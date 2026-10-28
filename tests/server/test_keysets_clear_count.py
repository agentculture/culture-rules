"""A clear's dry run and its applied run report the same count (live keys only)."""

from datetime import UTC, datetime, timedelta

from culture_rules import keysets
from culture_rules.server.service import Keysets
from culture_rules.store.memory import MemoryStore

T0 = datetime(2026, 10, 1, tzinfo=UTC)
LATER = T0 + timedelta(days=2)


def _service():
    store = MemoryStore()
    keysets.add_keys(store, "s", ["old"], T0, 86400)  # expired by LATER, not reaped
    keysets.add_keys(store, "s", ["new"], LATER, 86400)
    return store, Keysets(store, clock=lambda: LATER)


def test_dry_run_counts_live_keys():
    _, svc = _service()
    assert svc.clear("s", dry_run=True)["count"] == 1


def test_applied_clear_reports_the_dry_run_count():
    _, svc = _service()
    assert svc.clear("s", dry_run=False)["count"] == 1


def test_applied_clear_still_removes_the_expired_document():
    store, svc = _service()
    svc.clear("s", dry_run=False)
    assert store.find(keysets.KEYSETS_COLLECTION, {"set": "s"}) == []


def test_clearing_an_expired_key_counts_zero_like_its_dry_run():
    _, svc = _service()
    assert svc.clear("s", "old", dry_run=False)["count"] == 0
