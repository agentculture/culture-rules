"""contains, host_suffix, any / all and evaluate_item (plan no-flow-specific-code, t12)."""

import pytest

from culture_rules.model import condition as c


def F(p):
    return {"field": p}


def L(v):
    return {"literal": v}


def IT(p):
    return {"item": p}


CTX = {
    "trigger": {
        "title": "Fix the Login Bug",
        "labels": ["Bug", "ui", 3],
        "host": "Docs.Example.com.",
        "links": [{"host": "a.example.com", "n": 1}, {"host": "evilexample.com", "n": 2}],
        "none": [],
    },
    "variables": {"suffix": "example.com"},
}


def contains(value, needle):
    return {"op": "contains", "value": value, "needle": needle}


def suffix(value, suf):
    return {"op": "host_suffix", "value": value, "suffix": suf}


def ev(tree):
    return c.evaluate(tree, CTX)


@pytest.mark.parametrize(
    "tree",
    [
        contains(F("title"), L("login")),
        contains(F("title"), L("FIX THE")),
        contains(F("labels"), L("BUG")),
        contains(L("straße"), L("STRASSE")),
    ],
)
def test_contains_true(tree):
    assert ev(tree) is True


@pytest.mark.parametrize(
    "tree",
    [
        contains(F("title"), L("logout")),
        contains(F("title"), L("")),
        contains(F("title"), L(5)),
        contains(F("labels"), L("3")),
        contains(F("labels"), L("ug")),
        contains(F("missing"), L("x")),
        contains(F("title"), F("missing")),
        contains(L(12), L("1")),
    ],
)
def test_contains_false(tree):
    assert ev(tree) is False


@pytest.mark.parametrize(
    "host, suf",
    [
        ("example.com", "example.com"),
        ("docs.example.com", "example.com"),
        ("a.b.example.com", "example.com"),
        ("DOCS.Example.COM", "example.com"),
        ("docs.example.com.", "example.com"),
        ("docs.example.com", "Example.com."),
        ("docs.example.com", ".example.com"),
    ],
)
def test_host_suffix_true(host, suf):
    assert ev(suffix(L(host), L(suf))) is True


@pytest.mark.parametrize(
    "host, suf",
    [
        ("evilexample.com", "example.com"),
        ("example.com.evil.org", "example.com"),
        ("com", "example.com"),
        ("example.com", ""),
        ("", "example.com"),
        ("https://example.com/x", "example.com"),
        (5, "example.com"),
        ("example.com", 5),
    ],
)
def test_host_suffix_false(host, suf):
    assert ev(suffix(L(host), L(suf))) is False


def test_host_suffix_reads_fields_and_vars():
    assert ev(suffix(F("host"), {"var": "suffix"})) is True
    assert ev(suffix(F("missing"), {"var": "suffix"})) is False


def per_item(op, where, items=None):
    return {"op": op, "items": items or F("links"), "where": where}


def test_any_true_and_false():
    where_a = {"op": "compare", "cmp": "==", "left": IT("n"), "right": L(2)}
    where_z = {"op": "compare", "cmp": "==", "left": IT("n"), "right": L(9)}
    assert ev(per_item("any", where_a)) is True
    assert ev(per_item("any", where_z)) is False


def test_all_true_and_false():
    positive = {"op": "compare", "cmp": ">", "left": IT("n"), "right": L(0)}
    one = {"op": "compare", "cmp": "==", "left": IT("n"), "right": L(1)}
    assert ev(per_item("all", positive)) is True
    assert ev(per_item("all", one)) is False


def test_subdomain_matching_per_item():
    where = suffix(IT("host"), {"var": "suffix"})
    assert ev(per_item("any", where)) is True
    assert ev(per_item("all", where)) is False


def test_all_over_empty_list_is_true():
    assert ev(per_item("all", contains(IT("."), L("x")), F("none"))) is True


def test_any_over_empty_list_is_false():
    assert ev(per_item("any", contains(IT("."), L("x")), F("none"))) is False


@pytest.mark.parametrize("op", ["any", "all"])
def test_per_item_over_missing_or_non_list_is_false(op):
    where = contains(IT("."), L("x"))
    assert ev(per_item(op, where, F("missing"))) is False
    assert ev(per_item(op, where, F("title"))) is False


@pytest.mark.parametrize("op", ["any", "all"])
def test_per_item_over_too_long_a_list_is_false(op):
    where = {"op": "exists", "arg": IT(".")}
    assert c.evaluate(per_item(op, where, L([1] * c.MAX_ITEMS)), {}) is True
    assert c.evaluate(per_item(op, where, L([1] * (c.MAX_ITEMS + 1))), {}) is False


def test_item_dot_is_the_element_itself():
    where = {"op": "compare", "cmp": "==", "left": IT("."), "right": L("ui")}
    assert ev(per_item("any", where, F("labels"))) is True


def test_where_can_reach_the_trigger_and_vars():
    where = suffix(IT("host"), {"var": "suffix"})
    both = {"op": "and", "args": [where, contains(F("title"), L("bug"))]}
    assert ev(per_item("any", both)) is True


def test_inner_item_shadows_outer():
    ctx = {"trigger": {"rows": [{"cells": [1, 2]}, {"cells": [3]}]}}
    inner = {"op": "compare", "cmp": ">", "left": IT("."), "right": L(2)}
    tree = per_item("any", per_item("all", inner, IT("cells")), F("rows"))
    assert c.evaluate(tree, ctx) is True


def test_missing_item_path_makes_comparison_false():
    where = {"op": "compare", "cmp": "==", "left": IT("nope"), "right": L(1)}
    assert ev(per_item("any", where)) is False


def test_item_operand_outside_per_item_is_missing():
    assert ev({"op": "exists", "arg": IT("n")}) is False


def test_evaluate_item_binds_the_item():
    tree = suffix(IT("host"), L("example.com"))
    assert c.evaluate_item(tree, {"host": "a.example.com"}) is True


def test_evaluate_item_rejects_other_hosts():
    tree = suffix(IT("host"), L("example.com"))
    assert c.evaluate_item(tree, {"host": "evilexample.com"}) is False


def test_evaluate_item_reads_the_context():
    tree = suffix(IT("host"), {"var": "suffix"})
    assert c.evaluate_item(tree, {"host": "x.example.com"}, CTX) is True


def test_evaluate_item_does_not_mutate_the_context():
    ctx = {"variables": {"a": 1}}
    c.evaluate_item({"op": "exists", "arg": IT(".")}, 1, ctx)
    assert ctx == {"variables": {"a": 1}}


def test_evaluate_item_validates():
    with pytest.raises(c.ConditionError):
        c.evaluate_item({"op": "nope"}, 1)


@pytest.mark.parametrize(
    "tree, path",
    [
        ({"op": "contains", "value": L("a")}, "$.needle"),
        ({"op": "contains", "needle": L("a")}, "$.value"),
        ({"op": "host_suffix", "value": L("a")}, "$.suffix"),
        ({"op": "host_suffix", "value": {"x": 1}, "suffix": L("a")}, "$.value"),
        ({"op": "any", "where": {"op": "exists", "arg": L(1)}}, "$.items"),
        ({"op": "all", "items": L([])}, "$"),
        ({"op": "all", "items": L([]), "where": {"op": "zzz"}}, "$.where"),
        (
            {"op": "any", "items": L([]), "where": {"op": "exists", "arg": {"item": ""}}},
            "$.where.arg",
        ),
    ],
)
def test_invalid_new_ops_report_their_path(tree, path):
    with pytest.raises(c.ConditionError) as exc:
        c.validate(tree)
    assert exc.value.path == path


def test_nesting_limit_is_enforced():
    leaf = {"op": "exists", "arg": IT(".")}
    tree = leaf
    for _ in range(c.MAX_ITEM_NESTING + 1):
        tree = {"op": "any", "items": IT("."), "where": tree}
    with pytest.raises(c.ConditionError, match="nested more than"):
        c.validate(tree)


def test_nesting_limit_allows_the_maximum():
    tree = {"op": "exists", "arg": IT(".")}
    for _ in range(c.MAX_ITEM_NESTING):
        tree = {"op": "any", "items": IT("."), "where": tree}
    c.validate(tree)


def test_nesting_counts_through_and_not():
    leaf = {"op": "exists", "arg": IT(".")}
    tree = {"op": "any", "items": IT("."), "where": {"op": "not", "arg": leaf}}
    for _ in range(c.MAX_ITEM_NESTING - 1):
        tree = {"op": "any", "items": IT("."), "where": {"op": "and", "args": [tree, leaf]}}
    c.validate(tree)
    deeper = {"op": "any", "items": IT("."), "where": {"op": "or", "args": [tree, leaf]}}
    with pytest.raises(c.ConditionError, match="nested more than"):
        c.validate(deeper)


@pytest.mark.parametrize(
    "tree",
    [contains(F("a"), L("x")), suffix(F("a"), L("x")), per_item("any", suffix(IT("h"), L("x")))],
)
def test_new_ops_have_no_text_form(tree):
    with pytest.raises(c.ConditionError, match="no text form"):
        c.to_text(tree)


def test_item_operand_has_no_text_form():
    with pytest.raises(c.ConditionError, match="no text form"):
        c.to_text({"op": "exists", "arg": IT("a")})
