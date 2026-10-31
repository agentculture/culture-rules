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
    assert valued == [(posted, value, False)]
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
    buttons = [_btn(custom_id="p" * (db.MAX_VALUED_PREFIX + 1), value={})]
    with pytest.raises(ValueError, match="custom_id"):
        db.prepare_buttons(buttons)


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
    buttons = [_btn(value=value)]
    with pytest.raises(ValueError, match="value"):
        db.prepare_buttons(buttons)


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
    ids = db.remember_buttons(s, "bot", "42", [("merge:t1", {"n": 1}, False)], now=NOW)
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
    ids = db.remember_buttons(
        s, "bot", "42", [("a:1", {}, False), ("b:2", {"x": 1}, False)], now=NOW
    )
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
        db.remember_buttons(s, "bot", "42", [("a:1", {}, False), ("b:2", {}, False)], now=NOW)
    assert s.find(db.BUTTONS_COLLECTION) == []


def test_button_value_reads_the_stored_value_by_actor_and_custom_id():
    s = MemoryStore()
    db.remember_buttons(s, "bot", "42", [("merge:t1", {"n": 1}, False)], now=NOW)
    assert db.button_value(s, "bot", "merge:t1", now=NOW) == {"n": 1}
    assert db.button_value(s, "other", "merge:t1", now=NOW) is None
    assert db.button_value(s, "bot", "merge:t2", now=NOW) is None


def test_an_expired_value_reads_as_gone():
    s = MemoryStore()
    db.remember_buttons(s, "bot", "42", [("merge:t1", {"n": 1}, False)], now=NOW)
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
        ),
        (
            db.CODES_COLLECTION,
            [("expires_at", 1)],
            {"name": db.CODES_TTL_INDEX, "ttl_seconds": 0},
        ),
    ]
    db.ensure_buttons_ttl(MemoryStore())  # no ensure_index: nothing to do


# --------------------------------------------------------------------------- posted_by (d2)

POSTED_BY = {"run_id": "run-1", "workflow_id": "merge_prompt"}


def test_remember_stores_who_posted_each_button():
    s = MemoryStore()
    db.remember_buttons(
        s, "bot", "42", [("merge:t1", {"n": 1}, False)], now=NOW, posted_by=POSTED_BY
    )
    assert s.get(db.BUTTONS_COLLECTION, "bot:merge:t1")["posted_by"] == POSTED_BY


def test_remember_without_a_poster_stores_none():
    s = MemoryStore()
    db.remember_buttons(s, "bot", "42", [("merge:t1", {"n": 1}, False)], now=NOW)
    assert s.get(db.BUTTONS_COLLECTION, "bot:merge:t1")["posted_by"] is None


def test_stored_button_reads_the_value_and_who_posted_it():
    s = MemoryStore()
    db.remember_buttons(
        s, "bot", "42", [("merge:t1", {"n": 1}, False)], now=NOW, posted_by=POSTED_BY
    )
    assert db.stored_button(s, "bot", "merge:t1", now=NOW) == {
        "value": {"n": 1},
        "posted_by": POSTED_BY,
        "code": None,
    }


def test_stored_button_is_none_when_absent_or_expired():
    s = MemoryStore()
    db.remember_buttons(
        s, "bot", "42", [("merge:t1", {"n": 1}, False)], now=NOW, posted_by=POSTED_BY
    )
    assert db.stored_button(s, "other", "merge:t1", now=NOW) is None
    assert db.stored_button(s, "bot", "merge:t1", now=NOW + db.BUTTON_VALUE_TTL) is None


def test_a_document_written_before_posted_by_reads_none():
    s = MemoryStore()
    db.remember_buttons(s, "bot", "42", [("merge:t1", {"n": 1}, False)], now=NOW)
    doc = s.get(db.BUTTONS_COLLECTION, "bot:merge:t1")
    del doc["posted_by"]
    s.put(db.BUTTONS_COLLECTION, doc)
    assert db.stored_button(s, "bot", "merge:t1", now=NOW)["posted_by"] is None


# --------------------------------------------------------------------------- one-time codes (d3)

VALUE = {"repo": "o/r", "number": 7, "sha": "a" * 40}


def _coded(s, custom_id="merge:t1", value=VALUE):
    db.remember_buttons(s, "bot", "42", [(custom_id, value, True)], now=NOW)
    return s.get(db.BUTTONS_COLLECTION, f"bot:{custom_id}")["code"]


def _use(s, code, *, custom_id="merge:t1", value=VALUE, run="run-1", now=NOW):
    return db.use_code(s, code, custom_id=custom_id, value=value, run_id=run, now=now)


def test_a_button_asks_for_a_code_with_code_true():
    _, valued = db.prepare_buttons([_btn(value=VALUE, code=True)])
    assert valued[0][2] is True


def test_a_code_is_never_part_of_the_posted_button():
    components, _ = db.prepare_buttons([_btn(value=VALUE, code=True)])
    assert set(components[0]["components"][0]) == {"type", "style", "label", "custom_id"}


def test_a_code_without_a_value_is_refused():
    buttons = [_btn(code=True)]
    with pytest.raises(ValueError, match="code needs a value"):
        db.prepare_buttons(buttons)


def test_a_code_that_is_not_a_bool_is_refused():
    buttons = [_btn(value=VALUE, code="yes")]
    with pytest.raises(ValueError, match="code must be true or false"):
        db.prepare_buttons(buttons)


def test_a_coded_button_stores_its_code_with_the_value():
    s = MemoryStore()
    code = _coded(s)
    assert isinstance(code, str)
    assert len(code) >= 32
    doc = s.get(db.CODES_COLLECTION, code)
    assert doc["button_id"] == "bot:merge:t1"
    assert doc["custom_id"] == "merge:t1"
    assert doc["value"] == VALUE
    assert doc["used_at"] is None
    assert doc["expires_at"] == NOW + db.BUTTON_VALUE_TTL


def test_an_uncoded_button_stores_no_code():
    s = MemoryStore()
    db.remember_buttons(s, "bot", "42", [("merge:t1", VALUE, False)], now=NOW)
    assert s.get(db.BUTTONS_COLLECTION, "bot:merge:t1")["code"] is None
    assert s.find(db.CODES_COLLECTION) == []


def test_two_coded_buttons_get_distinct_codes():
    s = MemoryStore()
    assert _coded(s, "merge:t1") != _coded(s, "merge:t2")


def test_stored_button_reads_the_code():
    s = MemoryStore()
    code = _coded(s)
    assert db.stored_button(s, "bot", "merge:t1", now=NOW)["code"] == code


def test_forget_drops_the_code_too():
    s = MemoryStore()
    _coded(s)
    db.forget_buttons(s, ["bot:merge:t1"])
    assert s.find(db.CODES_COLLECTION) == []


def test_a_valid_code_is_used_once():
    s = MemoryStore()
    code = _coded(s)
    assert _use(s, code) is None
    assert s.get(db.CODES_COLLECTION, code)["used_by"] == "run-1"


def test_a_used_code_refuses_another_run():
    s = MemoryStore()
    code = _coded(s)
    _use(s, code)
    assert _use(s, code, run="run-2") == db.CODE_USED


def test_a_used_code_passes_again_for_the_same_run():
    s = MemoryStore()
    code = _coded(s)
    _use(s, code)
    assert _use(s, code) is None


@pytest.mark.parametrize("code", [None, "", 7, "never-issued"])
def test_a_missing_or_unknown_code_is_not_found(code):
    s = MemoryStore()
    _coded(s)
    assert _use(s, code) == db.CODE_NOT_FOUND


def test_an_expired_code_is_not_found():
    s = MemoryStore()
    code = _coded(s)
    assert _use(s, code, now=NOW + db.BUTTON_VALUE_TTL) == db.CODE_NOT_FOUND


def test_a_code_of_another_button_is_a_mismatch():
    s = MemoryStore()
    code = _coded(s)
    assert _use(s, code, custom_id="merge:t2") == db.CODE_MISMATCH


@pytest.mark.parametrize(
    "value",
    [
        {**VALUE, "sha": "b" * 40},
        {**VALUE, "number": 8},
        {**VALUE, "repo": "o/other"},
        {**VALUE, "extra": 1},
    ],
)
def test_a_code_for_another_value_is_a_mismatch(value):
    s = MemoryStore()
    code = _coded(s)
    assert _use(s, code, value=value) == db.CODE_MISMATCH


def test_a_refused_use_leaves_the_code_unused():
    s = MemoryStore()
    code = _coded(s)
    _use(s, code, value={**VALUE, "number": 8})
    assert s.get(db.CODES_COLLECTION, code)["used_at"] is None


def test_of_two_runs_racing_one_wins():
    s = MemoryStore()
    code = _coded(s)
    real = s.update_if

    def race(collection, id, expected, changes, **kw):
        real(collection, id, expected, {"used_at": NOW, "used_by": "run-2"})  # run-2 first
        return real(collection, id, expected, changes, **kw)

    s.update_if = race
    assert _use(s, code) == db.CODE_USED


def test_a_stored_true_is_not_the_number_one():
    s = MemoryStore()
    code = _coded(s, value={**VALUE, "number": True})
    assert _use(s, code, value={**VALUE, "number": 1}) == db.CODE_MISMATCH


def test_release_gives_the_code_back_to_be_used_again():
    s = MemoryStore()
    code = _coded(s)
    _use(s, code)
    assert db.release_code(s, code, "run-1") is True
    assert _use(s, code, run="run-2") is None


def test_only_the_run_holding_a_code_can_release_it():
    s = MemoryStore()
    code = _coded(s)
    _use(s, code)
    assert db.release_code(s, code, "run-2") is False
    assert s.get(db.CODES_COLLECTION, code)["used_by"] == "run-1"
