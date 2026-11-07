"""The built-in ``list.filter``, and that it keeps what the coded newspaper filter kept."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from culture_rules.engine.actorport import InvocationContext
from culture_rules.extensions.newspaper.port import filter_results, validate_section
from culture_rules.node.actions.list_filter import (
    LIST_FILTER_BUILTIN,
    MAX_LIST_ITEMS,
    ListFilterPort,
)

DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)

# newspaper.filter's rules as configuration: the section's lists arrive as step inputs.
NEWSPAPER_CONFIG = {
    "host_from": "url",
    "host_into": "host",
    "dedupe_by": "url",
    "where": {
        "op": "and",
        "args": [
            {
                "op": "all",
                "items": {"var": "must"},
                "where": {
                    "op": "or",
                    "args": [
                        {"op": "contains", "value": {"field": "title"}, "needle": {"item": "."}},
                        {"op": "contains", "value": {"field": "snippet"}, "needle": {"item": "."}},
                    ],
                },
            },
            {
                "op": "not",
                "arg": {
                    "op": "any",
                    "items": {"var": "must_not"},
                    "where": {
                        "op": "or",
                        "args": [
                            {
                                "op": "contains",
                                "value": {"field": "title"},
                                "needle": {"item": "."},
                            },
                            {
                                "op": "contains",
                                "value": {"field": "snippet"},
                                "needle": {"item": "."},
                            },
                        ],
                    },
                },
            },
            {
                "op": "or",
                "args": [
                    {
                        "op": "compare",
                        "cmp": "==",
                        "left": {"var": "site_include"},
                        "right": {"literal": []},
                    },
                    {
                        "op": "any",
                        "items": {"var": "site_include"},
                        "where": {
                            "op": "host_suffix",
                            "value": {"field": "host"},
                            "suffix": {"item": "."},
                        },
                    },
                ],
            },
            {
                "op": "not",
                "arg": {
                    "op": "any",
                    "items": {"var": "site_exclude"},
                    "where": {
                        "op": "host_suffix",
                        "value": {"field": "host"},
                        "suffix": {"item": "."},
                    },
                },
            },
        ],
    },
}


def run(inputs, config):
    ctx = InvocationContext(
        "run-1", "wf[0]/v", "code", "spark2", config={"builtin": LIST_FILTER_BUILTIN, **config}
    )
    return ListFilterPort().invoke(inputs, "k", DEADLINE, context=ctx)


def kept(inputs, config):
    result = run(inputs, config)
    assert result.outcome == "completed", result
    return result.output


def result(url, title="", snippet=""):
    return {"url": url, "title": title, "snippet": snippet}


RESULTS = [
    result("https://example.com/a", "Rust news", "about rust"),
    result("https://example.com/a", "Rust news again", "duplicate url"),
    result("http://example.com/a", "Rust http", "different url string"),
    result("ftp://example.com/f", "Rust ftp", "refused scheme"),
    result("mailto:someone@example.com", "Rust mail", "refused"),
    result("javascript:alert(1)", "Rust js", "refused"),
    result("https://sub.example.com/b", "Rust sub", "subdomain"),
    result("https://evilexample.com/c", "Rust lookalike", "look-alike host"),
    result("https://EXAMPLE.org/D", "RUST upper", "mixed case host"),
    result("https://other.net/e", "Gardening", "rust in the snippet only"),
    result("https://other.net/f", "Rust", "sponsored content"),
    result("https://other.net/g", "Rust sponsored title", "plain"),
    result("https://other.net/h", None, None),
    result("https://other.net/i", "Rust", None),
    result("https://other.net/j", 42, "rust"),
    result("not a url"),
    result("https://bad host/x", "Rust"),
    result("https://exa\x01mple.com/x", "Rust"),
    result("https:///nohost", "Rust"),
    result(None, "Rust"),
    result(7, "Rust"),
    "a string, not an object",
    None,
    {"title": "Rust, no url"},
    result("https://one.org/1", "Rust one", "x"),
    result("https://two.org/2", "Rust two", "x"),
    result("https://three.org/3", "Rust three", "x"),
    result("https://four.org/4", "Rust four", "x"),
    result("https://five.org/5", "Rust five", "x"),
    result("https://six.org/6", "Rust six", "x"),
    result("https://six.org/6", "Rust six second", "x"),
]
# the first duplicate is rejected, the second would pass: the url is consumed either way
SHADOW = [
    result("https://shadow.org/1", "Go", "no match"),
    result("https://shadow.org/1", "Rust", "would match"),
]

SECTIONS = [
    {"name": "plain"},
    {"name": "must", "must": ["rust"]},
    {"name": "must two", "must": ["RUST", "news"]},
    {"name": "must in snippet", "must": ["rust in the"]},
    {"name": "must_not", "must": ["rust"], "must_not": ["sponsored"]},
    {"name": "must_not only", "must_not": ["RUST"]},
    {"name": "include", "must": ["rust"], "site_include": ["example.com"]},
    {"name": "include two", "must": ["rust"], "site_include": ["example.com", "example.org"]},
    {"name": "exclude", "must": ["rust"], "site_exclude": ["example.com"]},
    {
        "name": "both",
        "must": ["rust"],
        "site_include": ["example.com", "other.net"],
        "site_exclude": ["sub.example.com"],
    },
    {"name": "capped", "must": ["rust"], "count": 3},
    {"name": "capped one", "count": 1},
    {"name": "capped big", "count": 20},
    {"name": "nothing matches", "must": ["zebra"]},
]


def coded_vs_configured(results, raw_section):
    section = validate_section({"queries": ["q"], **raw_section}, "section")
    inputs = {
        "items": results,
        "must": section["must"],
        "must_not": section["must_not"],
        "site_include": section["site_include"],
        "site_exclude": section["site_exclude"],
        "limit": section["count"],
    }
    return filter_results(results, section), kept(inputs, NEWSPAPER_CONFIG)["items"]


@pytest.mark.parametrize("section", SECTIONS, ids=[s["name"] for s in SECTIONS])
def test_configured_list_filter_keeps_what_the_coded_filter_kept(section):
    expected, got = coded_vs_configured(RESULTS, section)
    assert got == expected


@pytest.mark.parametrize("section", SECTIONS, ids=[s["name"] for s in SECTIONS])
def test_the_recorded_cases_are_not_trivial(section):
    expected, _ = coded_vs_configured(RESULTS, section)
    assert len(expected) > 0 or section["name"] == "nothing matches"


def test_a_rejected_first_duplicate_still_shadows_the_second():
    expected, got = coded_vs_configured(SHADOW, {"name": "s", "must": ["rust"]})
    assert (expected, got) == ([], [])


def test_the_recorded_cases_cover_the_cap_and_a_dropped_tail():
    expected, got = coded_vs_configured(RESULTS, {"name": "c", "count": 3})
    assert [r["url"] for r in got] == [r["url"] for r in expected][:3]


@pytest.mark.parametrize("results", [[], None, "text", {"url": "https://a.org/"}, 5])
def test_empty_and_non_list_results_agree(results):
    section = validate_section({"name": "e", "queries": ["q"]}, "section")
    expected = filter_results(results, section)
    items = results if isinstance(results, list) else []
    got = kept(
        {"items": items, "must": [], "must_not": [], "site_include": [], "site_exclude": []},
        NEWSPAPER_CONFIG,
    )
    assert (expected, got["items"]) == ([], [])


def test_the_kept_items_are_the_originals_without_the_derived_host():
    out = kept({"items": [result("https://a.org/", "t")]}, {"host_from": "url", "host_into": "h"})
    assert out["items"] == [result("https://a.org/", "t")]


def test_the_counts_say_what_each_rule_dropped():
    items = [
        result("https://a.org/1"),
        result("https://a.org/1"),
        result("ftp://a.org/2"),
        result("https://b.org/3"),
        result("https://c.org/4"),
        result("https://d.org/5"),
    ]
    cfg = {
        "host_from": "url",
        "host_into": "host",
        "dedupe_by": "url",
        "where": {
            "op": "not",
            "arg": {"op": "host_suffix", "value": {"item": "host"}, "suffix": {"literal": "b.org"}},
        },
        "limit": 2,
    }
    out = kept({"items": items}, cfg)
    assert [
        out["count"],
        out["dropped_host"],
        out["dropped_duplicate"],
        out["dropped_where"],
        out["dropped_limit"],
    ] == [2, 1, 1, 1, 1]


def test_a_limit_from_an_input_wins_over_the_config():
    out = kept({"items": [1, 2, 3], "limit": 1}, {"limit": 3})
    assert out["items"] == [1]


def test_a_limit_of_zero_keeps_nothing():
    assert kept({"items": [1, 2], "limit": 0}, {})["items"] == []


def test_without_rules_everything_is_kept_in_order():
    assert kept({"items": [3, 1, 2]}, {})["items"] == [3, 1, 2]


def test_dedupe_is_exact_unless_casefold_is_configured():
    items = [{"k": "A"}, {"k": "a"}, {"k": "A"}]
    exact = kept({"items": items}, {"dedupe_by": "k"})["items"]
    folded = kept({"items": items}, {"dedupe_by": "k", "dedupe_casefold": True})["items"]
    assert (len(exact), len(folded)) == (2, 1)


def test_items_without_a_comparable_value_are_kept_by_dedupe():
    items = [{"k": None}, {"k": None}, {"x": 1}, {"x": 1}, {"k": [1]}, {"k": [1]}]
    assert len(kept({"items": items}, {"dedupe_by": "k"})["items"]) == 6


def test_dedupe_by_a_nested_path_and_a_number():
    items = [{"a": {"id": 1}}, {"a": {"id": 1}}, {"a": {"id": 2}}]
    assert len(kept({"items": items}, {"dedupe_by": "a.id"})["items"]) == 2


def test_where_reads_the_item_and_the_context_values():
    where = {"op": "compare", "cmp": ">=", "left": {"item": "n"}, "right": {"var": "floor"}}
    out = kept({"items": [{"n": 1}, {"n": 5}, {"n": 9}], "floor": 5}, {"where": where})
    assert [i["n"] for i in out["items"]] == [5, 9]


@pytest.mark.parametrize(
    "config",
    [
        {"where": {"op": "nope"}},
        {"where": "x"},
        {"dedupe_by": ""},
        {"dedupe_by": 3},
        {"dedupe_casefold": "yes"},
        {"host_from": ["url"]},
        {"host_into": "h"},
        {"host_from": "url", "host_into": "a.b"},
        {"limit": -1},
        {"limit": 1.5},
        {"limit": True},
        {"surprise": 1},
    ],
)
def test_bad_config_fails_bad_params(config):
    out = run({"items": [1]}, config)
    assert (out.outcome, out.retryable, str(out.error).startswith("bad_params")) == (
        "failed",
        False,
        True,
    )


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"items": "x"},
        {"items": {"a": 1}},
        {"items": [1], "limit": -1},
        {"items": [1], "limit": "2"},
    ],
)
def test_bad_input_fails_bad_input(inputs):
    out = run(inputs, {})
    assert (out.outcome, out.retryable, str(out.error).startswith("bad_input")) == (
        "failed",
        False,
        True,
    )


def test_a_list_over_the_cap_is_refused():
    out = run({"items": [0] * (MAX_LIST_ITEMS + 1)}, {})
    assert str(out.error).startswith("bad_input")


def test_a_list_at_the_cap_is_taken():
    assert kept({"items": [0] * MAX_LIST_ITEMS}, {})["count"] == MAX_LIST_ITEMS
