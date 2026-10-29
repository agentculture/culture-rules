"""Keysets on MongoDB (marker ``mongo``; skips without docker): stored dates come back naive
from pymongo and must read as UTC (code review on PR #119)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from culture_rules import keysets

pytest.importorskip("pymongo", reason="pymongo (culture-rules[store]) is not installed")
_mongo_tests = pytest.importorskip("tests.store.test_mongo", reason="Mongo binding missing")

pytestmark = pytest.mark.mongo

NOW = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)


@pytest.fixture
def mongo_store():
    _mongo_tests.get_rig()  # skips when docker / the image is unavailable
    binding = _mongo_tests.TestMongoStore()
    store = binding.make_store()
    yield store
    _mongo_tests._release_stores()


def test_seen_reads_a_stored_key_on_mongo(mongo_store):
    keysets.add_keys(mongo_store, "links", ["a"], NOW, 86400)
    assert keysets.seen(mongo_store, "links", ["a"], NOW + timedelta(hours=1)) == ["a"]


def test_an_expired_key_is_renewed_on_mongo(mongo_store):
    keysets.add_keys(mongo_store, "links", ["a"], NOW, 60)
    later = NOW + timedelta(minutes=5)
    assert keysets.add_keys(mongo_store, "links", ["a"], later, 60)["added"] == 1


def test_show_set_gives_utc_times_on_mongo(mongo_store):
    keysets.add_keys(mongo_store, "links", ["a"], NOW, 86400)
    row = keysets.show_set(mongo_store, "links", NOW)[0]
    assert row["first_seen"] == NOW


def test_list_sets_counts_live_keys_on_mongo(mongo_store):
    keysets.add_keys(mongo_store, "links", ["a", "b"], NOW, 86400)
    assert keysets.list_sets(mongo_store, NOW) == [{"set": "links", "keys": 2}]
