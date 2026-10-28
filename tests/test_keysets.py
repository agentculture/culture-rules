"""Generic keysets: set names, declarative key normalisation, ids and the store helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from culture_rules import keysets
from culture_rules.keysets import KeysetError
from culture_rules.store.memory import MemoryStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FULL = {
    "lower_host": True,
    "drop_fragment": True,
    "drop_trailing_slash": True,
    "drop_query": ["utm_*", "ref"],
}


@pytest.mark.parametrize("name", ["a", "Set_1:x-y", "x" * 100])
def test_valid_set_names_pass(name):
    assert keysets.check_set_name(name) == name


@pytest.mark.parametrize("name", ["", "a b", "a/b", "x" * 101, "é", None, 3])
def test_bad_set_names_refused(name):
    with pytest.raises(KeysetError):
        keysets.check_set_name(name)


def test_error_carries_bad_params_code():
    with pytest.raises(KeysetError) as info:
        keysets.check_set_name("")
    assert info.value.code == "bad_params"


def test_no_config_leaves_value_unchanged():
    assert keysets.normalise("HTTP://Example.com/a/?utm_source=x#f", None) == (
        "HTTP://Example.com/a/?utm_source=x#f"
    )


def test_empty_config_leaves_value_unchanged():
    assert keysets.normalise("HTTP://Example.com/a/", {}) == "HTTP://Example.com/a/"


def test_normalisation_makes_one_key():
    a = keysets.normalise("http://Example.com/a/?utm_source=x#f", {**FULL, "drop_scheme": True})
    b = keysets.normalise("https://example.com/a", {**FULL, "drop_scheme": True})
    assert a == b


def test_scheme_kept_without_drop_scheme():
    a = keysets.normalise("http://example.com/a", FULL)
    assert a != keysets.normalise("https://example.com/a", FULL)


def test_drop_query_keeps_other_params():
    out = keysets.normalise("https://e.com/a?id=3&utm_x=1&ref=2", FULL)
    assert out == "https://e.com/a?id=3"


def test_no_pattern_is_built_in():
    out = keysets.normalise("https://e.com/a?utm_x=1", {"drop_fragment": True})
    assert out == "https://e.com/a?utm_x=1"


def test_bad_config_refused():
    with pytest.raises(KeysetError):
        keysets.normalise("x", {"drop_query": "utm_*"})


def test_long_key_refused():
    with pytest.raises(KeysetError):
        keysets.check_key("k" * 2049)


def test_max_length_key_allowed():
    assert keysets.check_key("k" * 2048) == "k" * 2048


def test_empty_key_refused():
    with pytest.raises(KeysetError):
        keysets.check_key("")


def test_doc_id_is_sha256_of_set_nul_key():
    import hashlib

    assert keysets.doc_id("s", "k") == hashlib.sha256(b"s\x00k").hexdigest()


def test_keys_differing_after_200_chars_get_different_ids():
    assert keysets.doc_id("s", "a" * 200 + "x") != keysets.doc_id("s", "a" * 200 + "y")


def test_add_inserts_one_document_per_key():
    store = MemoryStore()
    keysets.add_keys(store, "s", ["a", "b"], NOW, 60)
    assert len(store.find(keysets.KEYSETS_COLLECTION)) == 2


def test_add_counts_added():
    assert keysets.add_keys(MemoryStore(), "s", ["a", "b"], NOW, 60)["added"] == 2


def test_document_shape():
    store = MemoryStore()
    keysets.add_keys(store, "s", ["a"], NOW, 60)
    doc = store.get(keysets.KEYSETS_COLLECTION, keysets.doc_id("s", "a"))
    assert (doc["set"], doc["key"], doc["first_seen"], doc["expires_at"]) == (
        "s",
        "a",
        NOW,
        NOW + timedelta(seconds=60),
    )


def test_repeated_add_leaves_one_document():
    store = MemoryStore()
    keysets.add_keys(store, "s", ["a"], NOW, 60)
    keysets.add_keys(store, "s", ["a"], NOW + timedelta(seconds=5), 60)
    assert len(store.find(keysets.KEYSETS_COLLECTION)) == 1


def test_repeated_add_keeps_first_seen():
    store = MemoryStore()
    keysets.add_keys(store, "s", ["a"], NOW, 60)
    keysets.add_keys(store, "s", ["a"], NOW + timedelta(seconds=5), 60)
    doc = store.get(keysets.KEYSETS_COLLECTION, keysets.doc_id("s", "a"))
    assert doc["first_seen"] == NOW


def test_repeated_add_counts_already_present():
    store = MemoryStore()
    keysets.add_keys(store, "s", ["a"], NOW, 60)
    out = keysets.add_keys(store, "s", ["a", "b"], NOW, 60)
    assert (out["added"], out["already_present"]) == (1, 1)


def test_duplicate_keys_in_one_call_count_once():
    out = keysets.add_keys(MemoryStore(), "s", ["a", "a"], NOW, 60)
    assert (out["added"], out["already_present"]) == (1, 1)


def test_window_must_be_positive():
    with pytest.raises(KeysetError):
        keysets.add_keys(MemoryStore(), "s", ["a"], NOW, 0)


def test_naive_now_refused():
    with pytest.raises(KeysetError):
        keysets.add_keys(MemoryStore(), "s", ["a"], datetime(2026, 1, 1), 60)


def test_seen_returns_live_keys():
    store = MemoryStore()
    keysets.add_keys(store, "s", ["a", "b"], NOW, 60)
    assert keysets.seen(store, "s", ["a", "c"], NOW) == ["a"]


def test_expired_key_not_seen_though_document_exists():
    store = MemoryStore()
    keysets.add_keys(store, "s", ["a"], NOW, 60)
    later = NOW + timedelta(seconds=61)
    assert keysets.seen(store, "s", ["a"], later) == []


def test_expired_document_still_exists():
    store = MemoryStore()
    keysets.add_keys(store, "s", ["a"], NOW, 60)
    assert len(store.find(keysets.KEYSETS_COLLECTION)) == 1


def test_readd_after_expiry_renews():
    store = MemoryStore()
    keysets.add_keys(store, "s", ["a"], NOW, 60)
    later = NOW + timedelta(seconds=61)
    out = keysets.add_keys(store, "s", ["a"], later, 60)
    assert (out["added"], keysets.seen(store, "s", ["a"], later)) == (1, ["a"])


def test_two_sets_with_one_name_share_keys():
    store = MemoryStore()
    keysets.add_keys(store, "shared", ["a"], NOW, 60)
    keysets.add_keys(store, "shared", ["b"], NOW, 60)
    assert keysets.seen(store, "shared", ["a", "b"], NOW) == ["a", "b"]


def test_different_sets_do_not_share():
    store = MemoryStore()
    keysets.add_keys(store, "one", ["a"], NOW, 60)
    assert keysets.seen(store, "two", ["a"], NOW) == []


def test_list_sets_counts_live_keys():
    store = MemoryStore()
    keysets.add_keys(store, "one", ["a", "b"], NOW, 60)
    keysets.add_keys(store, "two", ["a"], NOW, 10)
    later = NOW + timedelta(seconds=30)
    assert keysets.list_sets(store, later) == [{"set": "one", "keys": 2}]


def test_show_set_lists_keys_sorted():
    store = MemoryStore()
    keysets.add_keys(store, "one", ["b", "a"], NOW, 60)
    assert [k["key"] for k in keysets.show_set(store, "one", NOW)] == ["a", "b"]


def test_clear_key_removes_it():
    store = MemoryStore()
    keysets.add_keys(store, "s", ["a", "b"], NOW, 60)
    assert keysets.clear_key(store, "s", "a") is True
    assert keysets.seen(store, "s", ["a", "b"], NOW) == ["b"]


def test_clear_key_missing_is_false():
    assert keysets.clear_key(MemoryStore(), "s", "a") is False


def test_clear_set_removes_only_that_set():
    store = MemoryStore()
    keysets.add_keys(store, "one", ["a", "b"], NOW, 60)
    keysets.add_keys(store, "two", ["a"], NOW, 60)
    assert keysets.clear_set(store, "one") == 2
    assert keysets.seen(store, "two", ["a"], NOW) == ["a"]


def test_ensure_ttl_index_calls_store_when_supported():
    calls = []

    class Fake:
        def ensure_index(self, collection, keys, **kw):
            calls.append((collection, keys, kw))

    keysets.ensure_ttl_index(Fake())
    assert calls[0][0] == keysets.KEYSETS_COLLECTION
    assert calls[0][1] == [("expires_at", 1)]
    assert calls[0][2]["ttl_seconds"] == 0


def test_ensure_ttl_index_noop_without_support():
    keysets.ensure_ttl_index(object())
