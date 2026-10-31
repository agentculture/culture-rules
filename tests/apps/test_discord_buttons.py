"""Button meaning kept in the store: valued buttons, their generated ids and the lookup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.apps import discord_buttons as db
from culture_rules.store.memory import MemoryStore

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def _btn(**kw):
    return {"label": "Merge", "style": "success", "custom_id": "merge", **kw}


def test_a_button_without_value_posts_its_custom_id_unchanged():
    components, valued = db.prepare_buttons([_btn()])
    assert components[0]["components"][0]["custom_id"] == "merge"
    assert valued == []


def test_a_valued_button_posts_its_prefix_and_a_generated_token():
    value = {"repo": "o/r", "number": 7, "sha": "a" * 40}
    components, valued = db.prepare_buttons([_btn(value=value)])
    posted = components[0]["components"][0]["custom_id"]
    assert posted.startswith("merge:")
    assert len(posted.split(":", 1)[1]) >= 20
    assert valued == [(posted, value)]
    assert "value" not in components[0]["components"][0]


def test_two_posts_of_one_valued_button_get_distinct_ids():
    first = db.prepare_buttons([_btn(value={"n": 1})])[1][0][0]
    second = db.prepare_buttons([_btn(value={"n": 1})])[1][0][0]
    assert first != second


def test_the_posted_id_never_exceeds_100_characters():
    prefix = "p" * db.MAX_VALUED_PREFIX
    components, _ = db.prepare_buttons([_btn(custom_id=prefix, value={})])
    assert len(components[0]["components"][0]["custom_id"]) <= 100


def test_a_prefix_too_long_for_a_token_is_refused():
    with pytest.raises(ValueError, match="custom_id"):
        db.prepare_buttons([_btn(custom_id="p" * (db.MAX_VALUED_PREFIX + 1), value={})])


def test_the_same_long_prefix_without_value_is_still_fine():
    components, _ = db.prepare_buttons([_btn(custom_id="p" * 100)])
    assert components[0]["components"][0]["custom_id"] == "p" * 100


@pytest.mark.parametrize(
    "value",
    [
        "a string",
        7,
        ["a", "list"],
        {1: "int key"},
        {"s": {1, 2}},
        {"f": float("nan")},
        {"f": float("inf")},
        {"deep": [{"x": object()}]},
        {"big": "x" * 5000},
    ],
)
def test_bad_values_are_refused(value):
    with pytest.raises(ValueError, match="value"):
        db.prepare_buttons([_btn(value=value)])


def test_nested_json_values_up_to_4096_bytes_are_accepted():
    value = {"a": [1, 2.5, True, None, {"b": "c"}], "pad": ""}
    value["pad"] = "x" * (db.MAX_VALUE_BYTES - len(db.encoded(value)))
    assert len(db.encoded(value)) == db.MAX_VALUE_BYTES
    assert db.prepare_buttons([_btn(value=value)])[1][0][1] == value


def test_a_null_value_means_no_value():
    components, valued = db.prepare_buttons([_btn(value=None)])
    assert components[0]["components"][0]["custom_id"] == "merge"
    assert valued == []


def test_remember_stores_one_document_per_valued_button():
    s = MemoryStore()
    ids = db.remember_buttons(s, "bot", "42", [("merge:t1", {"n": 1})], now=NOW)
    assert ids == ["bot:merge:t1"]
    doc = s.get(db.BUTTONS_COLLECTION, "bot:merge:t1")
    assert doc["actor_id"] == "bot"
    assert doc["custom_id"] == "merge:t1"
    assert doc["value"] == {"n": 1}
    assert doc["channel_id"] == "42"
    assert doc["message_id"] is None
    assert doc["created_at"] == NOW
    assert doc["expires_at"] == NOW + timedelta(days=30)


def test_set_message_id_and_forget():
    s = MemoryStore()
    ids = db.remember_buttons(s, "bot", "42", [("a:1", {}), ("b:2", {"x": 1})], now=NOW)
    db.set_message_id(s, ids, "m9")
    assert [s.get(db.BUTTONS_COLLECTION, i)["message_id"] for i in ids] == ["m9", "m9"]
    db.forget_buttons(s, ids)
    assert s.find(db.BUTTONS_COLLECTION) == []


def test_a_failed_insert_drops_the_ones_already_written():
    s = MemoryStore()
    real = s.insert
    calls = []

    def flaky(collection, doc):
        calls.append(doc["id"])
        if len(calls) == 2:
            raise RuntimeError("store down")
        return real(collection, doc)

    s.insert = flaky
    with pytest.raises(RuntimeError):
        db.remember_buttons(s, "bot", "42", [("a:1", {}), ("b:2", {})], now=NOW)
    assert s.find(db.BUTTONS_COLLECTION) == []


def test_button_value_reads_the_stored_value_by_actor_and_custom_id():
    s = MemoryStore()
    db.remember_buttons(s, "bot", "42", [("merge:t1", {"n": 1})], now=NOW)
    assert db.button_value(s, "bot", "merge:t1", now=NOW) == {"n": 1}
    assert db.button_value(s, "other", "merge:t1", now=NOW) is None
    assert db.button_value(s, "bot", "merge:t2", now=NOW) is None


def test_an_expired_value_reads_as_gone():
    s = MemoryStore()
    db.remember_buttons(s, "bot", "42", [("merge:t1", {"n": 1})], now=NOW)
    later = NOW + db.BUTTON_VALUE_TTL
    assert db.button_value(s, "bot", "merge:t1", now=later) is None


def test_the_ttl_index_is_created_on_expires_at():
    calls = []

    class Indexed:
        def ensure_index(self, collection, keys, **kw):
            calls.append((collection, keys, kw))

    db.ensure_buttons_ttl(Indexed())
    assert calls == [
        (
            db.BUTTONS_COLLECTION,
            [("expires_at", 1)],
            {"name": db.BUTTONS_TTL_INDEX, "ttl_seconds": 0},
        )
    ]
    db.ensure_buttons_ttl(MemoryStore())  # no ensure_index: nothing to do
