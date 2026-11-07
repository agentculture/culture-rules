"""The built-in ``json.parse``, and its shape configured with a coded check's rules.

``SECTIONS_SHAPE`` below is test data: the rules of a list-of-records document (the coded
``parse_sections`` check of the newspaper extension) written as a generic shape. The case table
asserts that ``json.parse`` with that shape refuses exactly what the coded check refuses.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from culture_rules.engine.actorport import InvocationContext
from culture_rules.extensions.newspaper.port import SectionsError, parse_sections
from culture_rules.node.actions.json_parse import (
    JSON_PARSE_BUILTIN,
    MAX_BYTES,
    JsonParsePort,
)

DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)


def _str(limit):
    return {"type": "text", "strip": True, "min_chars": 1, "max_chars": limit}


_TERMS = {"type": "list", "max_items": 20, "items": _str(100), "default": []}
_HOST = {
    "type": "text",
    "strip": True,
    "lower": True,
    "min_chars": 1,
    "max_chars": 100,
    "pattern": "^[a-z0-9.-]+$",
    "forbid": ["^[.-]", "[.-]$", r"\.\.", r"\.-", r"-\.", "[^.]{64}"],
}
_HOSTS = {"type": "list", "max_items": 20, "items": _HOST, "default": []}
SECTIONS_SHAPE = {
    "type": "list",
    "min_items": 1,
    "max_items": 20,
    "items": {
        "type": "object",
        "ignore": ["query"],
        "fields": {
            "name": {**_str(80), "required": True},
            "queries": {
                "type": "list",
                "required": True,
                "min_items": 1,
                "max_items": 5,
                "items": _str(400),
            },
            "must": _TERMS,
            "must_not": _TERMS,
            "freshness": {
                "type": "text",
                "enum": ["pd", "pw", "pm", "py"],
                "default": None,
                "nullable": True,
            },
            "count": {"type": "int", "min": 1, "max": 20, "default": 5},
            "site_include": _HOSTS,
            "site_exclude": _HOSTS,
        },
    },
}


def invoke(inputs, config=None):
    cfg = {"builtin": JSON_PARSE_BUILTIN, **(config or {})}
    ctx = InvocationContext("run-1", "wf[0]/parse", "code", "spark2", config=cfg)
    return JsonParsePort().invoke(inputs, "k", DEADLINE, context=ctx)


def failed(res, code):
    assert res.outcome == "failed"
    assert res.error.startswith(f"{code}:"), res.error
    assert res.retryable is False


def sec(**kw):
    return {"name": "N", "queries": ["q"], **kw}


def _coded_accepts(raw):
    try:
        parse_sections(raw)
    except SectionsError:
        return False
    return True


# every case is the JSON text of a document; the table covers each rule of the coded check
CASES = {
    "minimal": [sec()],
    "all_keys": [
        sec(
            must=["a"],
            must_not=["b"],
            freshness="pw",
            count=20,
            site_include=["Example.com"],
            site_exclude=["a-b.example.org"],
        )
    ],
    "derived_query_ignored": [sec(query="q OR r")],
    "null_optionals_default": [sec(freshness=None, count=None, must=None, site_include=None)],
    "padded_strings_stripped": [{"name": "  N  ", "queries": [" q "], "must": [" t "]}],
    "twenty_sections": [sec() for _ in range(20)],
    "name_80": [sec(name="n" * 80)],
    "queries_5": [sec(queries=["q"] * 5)],
    "query_400": [sec(queries=["q" * 400])],
    "terms_20": [sec(must=["t"] * 20)],
    "term_100": [sec(must=["t" * 100])],
    "host_label_63": [sec(site_include=["a" * 63 + ".com"])],
    "host_single_label": [sec(site_include=["localhost"])],
    "host_digits": [sec(site_include=["1.2.3"])],
    "empty_list": [],
    "not_a_list": {"name": "N", "queries": ["q"]},
    "twenty_one_sections": [sec() for _ in range(21)],
    "section_not_object": ["N"],
    "unknown_key": [sec(extra=1)],
    "name_missing": [{"queries": ["q"]}],
    "name_null": [sec(name=None)],
    "name_blank": [sec(name="   ")],
    "name_number": [sec(name=5)],
    "name_81": [sec(name="n" * 81)],
    "queries_missing": [{"name": "N"}],
    "queries_empty": [sec(queries=[])],
    "queries_6": [sec(queries=["q"] * 6)],
    "queries_not_list": [sec(queries="q")],
    "query_blank": [sec(queries=[" "])],
    "query_not_string": [sec(queries=[1])],
    "query_401": [sec(queries=["q" * 401])],
    "must_not_list": [sec(must="a")],
    "must_21": [sec(must=["t"] * 21)],
    "must_blank_term": [sec(must=[""])],
    "must_not_term_101": [sec(must_not=["t" * 101])],
    "freshness_unknown": [sec(freshness="pz")],
    "freshness_number": [sec(freshness=1)],
    "count_zero": [sec(count=0)],
    "count_21": [sec(count=21)],
    "count_bool": [sec(count=True)],
    "count_float": [sec(count=2.5)],
    "count_text": [sec(count="3")],
    "host_has_slash": [sec(site_include=["a.com/x"])],
    "host_has_space": [sec(site_exclude=["a b.com"])],
    "host_leading_hyphen": [sec(site_include=["-a.com"])],
    "host_trailing_hyphen": [sec(site_include=["a-.com"])],
    "host_trailing_dot": [sec(site_include=["a.com."])],
    "host_leading_dot": [sec(site_include=[".a.com"])],
    "host_double_dot": [sec(site_include=["a..com"])],
    "host_hyphen_next_to_dot": [sec(site_include=["a.-b.com"])],
    "host_label_64": [sec(site_include=["a" * 64 + ".com"])],
    "host_underscore": [sec(site_include=["a_b.com"])],
    "host_too_many": [sec(site_exclude=["a.com"] * 21)],
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_shape_refuses_exactly_what_the_coded_check_refuses(case):
    raw = CASES[case]
    res = invoke({"text": json.dumps(raw)}, {"shape": SECTIONS_SHAPE})
    assert (res.outcome == "completed") == _coded_accepts(raw), res.error


@pytest.mark.parametrize("case", sorted(c for c in CASES if _coded_accepts(CASES[c])))
def test_accepted_documents_are_normalised_like_the_coded_check(case):
    expected = [{k: v for k, v in s.items()} for s in parse_sections(CASES[case])]
    res = invoke({"text": json.dumps(CASES[case])}, {"shape": SECTIONS_SHAPE})
    assert res.output["value"] == expected


def test_refusal_names_the_failing_path():
    res = invoke({"text": json.dumps([sec(), sec(queries=[])])}, {"shape": SECTIONS_SHAPE})
    failed(res, "shape_invalid")
    assert "[1].queries" in res.error


def test_shape_comes_from_the_input_when_given():
    res = invoke({"text": "5", "shape": {"type": "int", "max": 9}})
    assert res.output["value"] == 5


def test_bytes_are_parsed():
    res = invoke(
        {"text": b'{"a": 1}'}, {"shape": {"type": "object", "fields": {"a": {"type": "int"}}}}
    )
    assert res.output["value"] == {"a": 1}


def test_a_decoded_value_is_only_checked():
    res = invoke({"text": [1, 2]}, {"shape": {"type": "list", "items": {"type": "int"}}})
    assert res.output["value"] == [1, 2]


def test_invalid_json_fails_not_json():
    failed(invoke({"text": "{nope"}, {"shape": {"type": "object", "fields": {}}}), "not_json")


def test_missing_text_fails_not_json():
    failed(invoke({}, {"shape": {"type": "int"}}), "not_json")


def test_non_utf8_bytes_fail_not_json():
    failed(invoke({"text": b"\xff\xfe"}, {"shape": {"type": "int"}}), "not_json")


def test_text_over_the_cap_fails_not_json():
    failed(invoke({"text": "1" * (MAX_BYTES + 1)}, {"shape": {"type": "int"}}), "not_json")


def test_missing_shape_fails_bad_params():
    failed(invoke({"text": "1"}), "bad_params")


def test_malformed_shape_fails_bad_params():
    failed(invoke({"text": "1"}, {"shape": {"type": "nope"}}), "bad_params")


# -- the shape rules this step needed ----------------------------------------------------------


def test_nullable_keeps_none_when_a_default_is_null():
    res = invoke(
        {"text": "{}"},
        {
            "shape": {
                "type": "object",
                "fields": {"f": {"type": "text", "enum": ["a"], "default": None, "nullable": True}},
            }
        },
    )
    assert res.output["value"] == {"f": None}


def test_without_nullable_none_is_refused():
    failed(invoke({"text": "null"}, {"shape": {"type": "text"}}), "shape_invalid")


def test_forbid_refuses_a_text_that_matches():
    failed(
        invoke({"text": '"a..b"'}, {"shape": {"type": "text", "forbid": [r"\.\."]}}),
        "shape_invalid",
    )


def test_forbid_keeps_a_text_that_does_not_match():
    res = invoke({"text": '"a.b"'}, {"shape": {"type": "text", "forbid": [r"\.\."]}})
    assert res.output["value"] == "a.b"


def test_forbid_applies_to_each_line():
    failed(
        invoke({"text": '"ok\\nbad"'}, {"shape": {"type": "lines", "forbid": ["^bad"]}}),
        "shape_invalid",
    )


def test_unsafe_forbid_pattern_fails_bad_params():
    failed(invoke({"text": '"a"'}, {"shape": {"type": "text", "forbid": ["(a+)+"]}}), "bad_params")


def test_ignore_lets_a_key_in_and_leaves_it_out():
    shape = {"type": "object", "ignore": ["x"], "fields": {"a": {"type": "int"}}}
    assert invoke({"text": '{"a": 1, "x": 2}'}, {"shape": shape}).output["value"] == {"a": 1}


def test_unlisted_key_is_still_refused_with_ignore():
    shape = {"type": "object", "ignore": ["x"], "fields": {"a": {"type": "int"}}}
    failed(invoke({"text": '{"a": 1, "y": 2}'}, {"shape": shape}), "shape_invalid")


def test_lines_count_passes_inside_the_bounds():
    res = invoke({"text": '"a\\nb"'}, {"shape": {"type": "lines", "min": 1, "max": 2}})
    assert res.output["value"] == ["a", "b"]


def test_lines_count_fails_over_the_bound():
    failed(invoke({"text": '"a\\nb\\nc"'}, {"shape": {"type": "lines", "max": 2}}), "shape_invalid")
