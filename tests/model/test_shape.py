"""The generic shape validator: a declarative spec, a normalised value, an exact field path.

The ``RECORD`` spec below is test data only: it ports the rules of a short structured answer
(a one-or-two line summary, one lower-cased word, two bounded lists of strings) to show that the
vocabulary can express them without the module naming them.
"""

from __future__ import annotations

import copy
import json

import pytest

from culture_rules.model.shape import ShapeError, ShapeSpecError, validate

_LINE = {"type": "text", "strip": True, "min_chars": 1, "max_chars": 300}
RECORD = {
    "type": "object",
    "fields": {
        "summary": {"type": "lines", "min": 1, "max": 2, "max_chars": 300},
        "word": {
            "type": "word",
            "strip": True,
            "lower": True,
            "pattern": "^[A-Za-z][A-Za-z-]{1,23}$",
        },
        "facts": {"type": "list", "required": True, "max_items": 30, "items": _LINE},
        "action_items": {"type": "list", "required": True, "max_items": 30, "items": _LINE},
    },
}
GOOD = {
    "summary": "  First line.\n\n Second line. ",
    "word": " Budget ",
    "facts": [" a fact "],
    "action_items": [],
}


def _fail(value, spec) -> ShapeError:
    with pytest.raises(ShapeError) as info:
        validate(value, spec)
    return info.value


def _bad_spec(spec) -> ShapeSpecError:
    with pytest.raises(ShapeSpecError) as info:
        validate("x", spec)
    return info.value


# -- the ported record rules -------------------------------------------------------------------


def test_record_is_normalised():
    expected = {
        "summary": ["First line.", "Second line."],
        "word": "budget",
        "facts": ["a fact"],
        "action_items": [],
    }
    assert validate(GOOD, RECORD) == expected


def test_record_does_not_mutate_its_input():
    value = copy.deepcopy(GOOD)
    validate(value, RECORD)
    assert value == GOOD


def test_record_summary_of_three_lines_is_refused():
    err = _fail({**GOOD, "summary": "a\nb\nc"}, RECORD)
    assert err.path == "summary"


def test_record_summary_of_no_lines_is_refused():
    assert _fail({**GOOD, "summary": " \n "}, RECORD).path == "summary"


def test_record_summary_line_over_300_chars_is_refused():
    assert _fail({**GOOD, "summary": "x" * 301}, RECORD).path == "summary[0]"


def test_record_summary_line_of_300_chars_is_kept():
    assert validate({**GOOD, "summary": "x" * 300}, RECORD)["summary"] == ["x" * 300]


def test_record_word_with_a_digit_is_refused():
    assert _fail({**GOOD, "word": "abc1"}, RECORD).path == "word"


def test_record_word_of_one_letter_is_refused():
    assert _fail({**GOOD, "word": "a"}, RECORD).path == "word"


def test_record_word_of_25_letters_is_refused():
    assert _fail({**GOOD, "word": "a" * 25}, RECORD).path == "word"


def test_record_word_of_24_letters_is_kept():
    assert validate({**GOOD, "word": "A" * 24}, RECORD)["word"] == "a" * 24


def test_record_word_with_a_hyphen_is_kept():
    assert validate({**GOOD, "word": "Well-Known"}, RECORD)["word"] == "well-known"


def test_record_31_facts_are_refused():
    assert _fail({**GOOD, "facts": ["x"] * 31}, RECORD).path == "facts"


def test_record_30_facts_are_kept():
    assert len(validate({**GOOD, "facts": ["x"] * 30}, RECORD)["facts"]) == 30


def test_record_empty_fact_is_refused_with_its_index():
    assert _fail({**GOOD, "facts": ["ok", "  "]}, RECORD).path == "facts[1]"


def test_record_long_action_item_is_refused_with_its_index():
    assert _fail({**GOOD, "action_items": ["x" * 301]}, RECORD).path == "action_items[0]"


def test_record_non_string_fact_is_refused():
    assert _fail({**GOOD, "facts": [3]}, RECORD).path == "facts[0]"


def test_record_missing_list_is_refused():
    value = {k: v for k, v in GOOD.items() if k != "facts"}
    assert _fail(value, RECORD).path == "facts"


def test_record_unknown_key_is_refused_with_its_path():
    assert _fail({**GOOD, "extra": 1}, RECORD).path == "extra"


# -- the error ---------------------------------------------------------------------------------


def test_error_str_joins_path_and_reason():
    assert str(ShapeError("a.b", "bad")) == "a.b: bad"


def test_error_at_the_root_has_an_empty_path():
    assert _fail(1, {"type": "text"}).path == ""


def test_error_str_at_the_root_has_no_path_prefix():
    assert str(ShapeError("", "bad")) == "bad"


def test_error_keeps_its_reason():
    assert _fail(1, {"type": "text"}).reason == "must be text"


def test_nested_object_path_uses_dots():
    spec = {"type": "object", "fields": {"a": {"type": "object", "fields": {"b": {"type": "int"}}}}}
    assert _fail({"a": {"b": "x"}}, spec).path == "a.b"


# -- required, default -------------------------------------------------------------------------


def test_required_missing_field_is_refused():
    spec = {"type": "object", "fields": {"a": {"type": "int", "required": True}}}
    assert _fail({}, spec).reason == "is required"


def test_optional_missing_field_is_left_out():
    spec = {"type": "object", "fields": {"a": {"type": "int"}}}
    assert validate({}, spec) == {}


def test_default_fills_a_missing_field():
    spec = {"type": "object", "fields": {"a": {"type": "int", "default": 7}}}
    assert validate({}, spec) == {"a": 7}


def test_default_does_not_replace_a_given_value():
    spec = {"type": "object", "fields": {"a": {"type": "int", "default": 7}}}
    assert validate({"a": 2}, spec) == {"a": 2}


def test_default_is_a_copy_not_the_spec_object():
    spec = {"type": "object", "fields": {"a": {"type": "list", "default": []}}}
    out = validate({}, spec)
    out["a"].append("x")
    assert spec["fields"]["a"]["default"] == []


def test_default_is_validated_when_the_spec_is_checked():
    spec = {"type": "object", "fields": {"a": {"type": "int", "max": 3, "default": 9}}}
    assert "default" in _bad_spec(spec).args[0]


def test_default_on_the_root_fills_none():
    assert validate(None, {"type": "int", "default": 4}) == 4


def test_none_without_a_default_is_refused():
    assert _fail(None, {"type": "int"}).reason == "must be an integer"


# -- types -------------------------------------------------------------------------------------


def test_text_is_kept_as_it_is_without_strip():
    assert validate("  a b ", {"type": "text"}) == "  a b "


def test_text_strip_trims_it():
    assert validate("  a b ", {"type": "text", "strip": True}) == "a b"


def test_text_lower_lowercases_it():
    assert validate("AbC", {"type": "text", "lower": True}) == "abc"


def test_text_is_not_lowercased_implicitly():
    assert validate("AbC", {"type": "text"}) == "AbC"


def test_text_refuses_a_number():
    assert _fail(3, {"type": "text"}).reason == "must be text"


def test_text_min_chars_is_checked_after_strip():
    assert _fail("  ", {"type": "text", "strip": True, "min_chars": 1}).reason == (
        "must be at least 1 characters"
    )


def test_text_max_chars_is_refused_over_the_bound():
    assert _fail("abcd", {"type": "text", "max_chars": 3}).reason == (
        "must be at most 3 characters"
    )


def test_text_max_chars_keeps_the_bound_itself():
    assert validate("abc", {"type": "text", "max_chars": 3}) == "abc"


def test_text_pattern_must_match():
    assert _fail("ab1", {"type": "text", "pattern": "^[a-z]+$"}).reason == (
        "does not match the required pattern"
    )


def test_text_pattern_match_is_kept():
    assert validate("ab", {"type": "text", "pattern": "^[a-z]+$"}) == "ab"


def test_text_enum_refuses_an_other_value():
    assert _fail("c", {"type": "text", "enum": ["a", "b"]}).reason == "must be one of: a, b"


def test_text_enum_keeps_a_listed_value():
    assert validate("a", {"type": "text", "enum": ["a", "b"]}) == "a"


def test_text_enum_is_compared_after_lower():
    assert validate("A", {"type": "text", "lower": True, "enum": ["a"]}) == "a"


def test_word_refuses_whitespace_inside():
    assert _fail("a b", {"type": "word"}).reason == "must be a single word"


def test_word_refuses_empty():
    assert _fail("", {"type": "word"}).reason == "must be a single word"


def test_word_keeps_a_single_token():
    assert validate("abc", {"type": "word"}) == "abc"


def test_lines_splits_strips_and_drops_empty_lines():
    assert validate(" a \n\n b\r\n", {"type": "lines"}) == ["a", "b"]


def test_lines_refuses_a_list():
    assert _fail(["a"], {"type": "lines"}).reason == "must be text"


def test_lines_min_counts_lines():
    assert _fail("a", {"type": "lines", "min": 2}).reason == "must have at least 2 lines"


def test_lines_max_counts_lines():
    assert _fail("a\nb", {"type": "lines", "max": 1}).reason == "must have at most 1 lines"


def test_lines_max_chars_is_per_line_and_indexed():
    assert _fail("a\nbbbb", {"type": "lines", "max_chars": 3}).path == "[1]"


def test_lines_pattern_is_per_line():
    assert _fail("ab\n1", {"type": "lines", "pattern": "^[a-z]+$"}).path == "[1]"


def test_lines_lower_applies_to_each_line():
    assert validate("A\nB", {"type": "lines", "lower": True}) == ["a", "b"]


def test_int_keeps_an_integer():
    assert validate(5, {"type": "int"}) == 5


def test_int_refuses_a_bool():
    assert _fail(True, {"type": "int"}).reason == "must be an integer"


def test_int_refuses_a_float():
    assert _fail(1.5, {"type": "int"}).reason == "must be an integer"


def test_int_refuses_a_numeric_string():
    assert _fail("5", {"type": "int"}).reason == "must be an integer"


def test_int_min_is_refused_below():
    assert _fail(0, {"type": "int", "min": 1}).reason == "must be at least 1"


def test_int_min_keeps_the_bound_itself():
    assert validate(1, {"type": "int", "min": 1}) == 1


def test_int_max_is_refused_above():
    assert _fail(4, {"type": "int", "max": 3}).reason == "must be at most 3"


def test_int_enum_refuses_an_other_value():
    assert _fail(3, {"type": "int", "enum": [1, 2]}).reason == "must be one of: 1, 2"


def test_bool_keeps_a_bool():
    assert validate(False, {"type": "bool"}) is False


def test_bool_refuses_an_int():
    assert _fail(1, {"type": "bool"}).reason == "must be true or false"


def test_list_refuses_text():
    assert _fail("a", {"type": "list"}).reason == "must be a list"


def test_list_without_items_keeps_any_json_value():
    assert validate([1, "a", None], {"type": "list"}) == [1, "a", None]


def test_list_validates_each_item_with_its_index():
    assert _fail([1, "x"], {"type": "list", "items": {"type": "int"}}).path == "[1]"


def test_list_returns_the_normalised_items():
    assert validate([" A "], {"type": "list", "items": {"type": "text", "strip": True}}) == ["A"]


def test_list_max_items_is_refused_over():
    assert _fail([1, 2], {"type": "list", "max_items": 1}).reason == "must have at most 1 items"


def test_list_min_items_is_refused_under():
    assert _fail([], {"type": "list", "min_items": 1}).reason == "must have at least 1 items"


def test_object_refuses_a_list():
    assert _fail([], {"type": "object", "fields": {}}).reason == "must be an object"


def test_object_unknown_key_reason():
    assert _fail({"z": 1}, {"type": "object", "fields": {}}).reason == "is not allowed"


def test_object_refuses_a_non_string_key_without_crashing():
    assert _fail({1: 1}, {"type": "object", "fields": {}}).reason == "is not allowed"


def test_object_in_a_list_has_a_combined_path():
    spec = {
        "type": "list",
        "items": {"type": "object", "fields": {"n": {"type": "int", "required": True}}},
    }
    assert _fail([{"n": 1}, {}], spec).path == "[1].n"


# -- the spec itself ---------------------------------------------------------------------------


def test_spec_is_plain_json_data():
    assert json.loads(json.dumps(RECORD)) == RECORD


def test_spec_without_a_type_is_refused():
    assert _bad_spec({}).args[0] == "spec: type is required"


def test_spec_with_an_unknown_type_is_refused():
    assert "unknown type" in _bad_spec({"type": "float"}).args[0]


def test_spec_with_an_unknown_key_is_refused():
    assert "unknown key" in _bad_spec({"type": "int", "bogus": 1}).args[0]


def test_spec_key_of_another_type_is_refused():
    assert "not valid for type" in _bad_spec({"type": "int", "pattern": "a"}).args[0]


def test_spec_that_is_not_an_object_is_refused():
    assert "must be an object" in _bad_spec("int").args[0]


def test_spec_with_a_bad_pattern_is_refused():
    assert "pattern" in _bad_spec({"type": "text", "pattern": "("}).args[0]


def test_spec_with_an_unsafe_pattern_is_refused():
    assert "unsafe" in _bad_spec({"type": "text", "pattern": "(a+)+b"}).args[0]


def test_spec_error_names_the_nested_spec_path():
    spec = {"type": "object", "fields": {"a": {"type": "nope"}}}
    assert _bad_spec(spec).args[0].startswith("spec.fields.a: ")


def test_spec_object_without_fields_is_refused():
    assert "fields" in _bad_spec({"type": "object"}).args[0]


def test_spec_negative_bound_is_refused():
    assert "non-negative" in _bad_spec({"type": "text", "max_chars": -1}).args[0]


def test_spec_bound_that_is_a_bool_is_refused():
    assert "integer" in _bad_spec({"type": "text", "max_chars": True}).args[0]


def test_spec_min_above_max_is_refused():
    assert "exceeds" in _bad_spec({"type": "int", "min": 5, "max": 1}).args[0]


def test_spec_enum_must_be_a_non_empty_list():
    assert "enum" in _bad_spec({"type": "text", "enum": []}).args[0]


def test_spec_flag_must_be_a_bool():
    assert "true or false" in _bad_spec({"type": "text", "lower": "yes"}).args[0]


def test_spec_error_is_a_value_error():
    assert issubclass(ShapeSpecError, ValueError)


def test_shape_error_is_a_value_error():
    assert issubclass(ShapeError, ValueError)


# -- extra keys on an object -------------------------------------------------------------------

_OPEN = {"type": "object", "fields": {"a": {"type": "int"}}}


def test_object_extra_refuse_is_the_default():
    assert _fail({"a": 1, "z": 2}, {**_OPEN, "extra": "refuse"}).path == "z"


def test_object_extra_drop_leaves_unknown_keys_out():
    assert validate({"a": 1, "z": 2}, {**_OPEN, "extra": "drop"}) == {"a": 1}


def test_object_extra_drop_still_checks_known_fields():
    assert _fail({"a": "x", "z": 2}, {**_OPEN, "extra": "drop"}).path == "a"


@pytest.mark.parametrize("extra", ["keep", True, None])
def test_object_extra_must_be_refuse_or_drop(extra):
    with pytest.raises(ShapeSpecError):
        validate({"a": 1}, {**_OPEN, "extra": extra})


def test_extra_is_not_valid_for_a_text_spec():
    with pytest.raises(ShapeSpecError):
        validate("x", {"type": "text", "extra": "drop"})
