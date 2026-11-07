"""The condition evaluator's work bound, prepare(), its public helpers and ASCII host matching
(PR #142 review findings #2, #4, #8, #9)."""

import pytest

from culture_rules.model import condition as c


def _any(items, where):
    return {"op": "any", "items": items, "where": where}


NEVER = {"op": "compare", "cmp": "==", "left": {"item": "."}, "right": {"literal": -1}}


# -- the budget ---------------------------------------------------------------------------


def test_an_evaluation_over_its_budget_fails():
    tree = {"op": "and", "args": [{"op": "exists", "arg": {"literal": 1}}] * 10}
    with pytest.raises(c.EvaluationBudgetError):
        c.evaluate(tree, {}, c.Budget(5))


def test_an_evaluation_within_its_budget_holds():
    tree = {"op": "and", "args": [{"op": "exists", "arg": {"literal": 1}}] * 10}
    assert c.evaluate(tree, {}, c.Budget(11)) is True


def test_the_default_budget_bounds_nested_any_over_long_lists():
    big = list(range(c.MAX_ITEMS))
    tree = _any({"var": "big"}, _any({"var": "big"}, NEVER))
    with pytest.raises(c.EvaluationBudgetError):
        c.evaluate(tree, {"variables": {"big": big}})


def test_in_spends_one_step_per_member():
    tree = {"op": "in", "value": {"literal": "x"}, "items": {"var": "big"}}
    with pytest.raises(c.EvaluationBudgetError):
        c.evaluate(tree, {"variables": {"big": [0] * (c.MAX_EVAL_STEPS + 1)}})


def test_contains_over_a_list_spends_one_step_per_member():
    tree = {"op": "contains", "value": {"var": "big"}, "needle": {"literal": "x"}}
    with pytest.raises(c.EvaluationBudgetError):
        c.evaluate(tree, {"variables": {"big": ["y"] * (c.MAX_EVAL_STEPS + 1)}})


def test_a_budget_error_is_a_condition_error():
    """The engine catches ConditionError: a rule does not fire, a when fails when_invalid."""
    assert issubclass(c.EvaluationBudgetError, c.ConditionError)


def test_the_budget_error_has_its_own_code():
    assert c.EvaluationBudgetError("x").to_dict()["code"] == "budget_exceeded"


def test_a_shared_budget_spans_evaluations():
    prepared = c.prepare({"op": "exists", "arg": {"item": "."}})
    budget = c.Budget(2)
    prepared.evaluate_item(1, None, budget)
    prepared.evaluate_item(2, None, budget)
    with pytest.raises(c.EvaluationBudgetError):
        prepared.evaluate_item(3, None, budget)


def test_a_rule_condition_over_budget_does_not_fire():
    from culture_rules.engine import matching

    big = list(range(c.MAX_ITEMS))

    always = {"op": "exists", "arg": {"item": "."}}
    every = {"op": "all", "items": {"var": "big"}, "where": always}

    class Rule:  # holds without a bound: every pair of members exists
        condition = {"op": "all", "items": {"var": "big"}, "where": every}

    assert "evaluation steps" in matching._condition(Rule(), {}, {"big": big})


# -- prepare -------------------------------------------------------------------------------


def test_prepare_validates_once_for_many_evaluations(monkeypatch):
    calls = []
    real = c.validate
    monkeypatch.setattr(c, "validate", lambda tree: calls.append(1) or real(tree))
    prepared = c.prepare({"op": "exists", "arg": {"item": "a"}})
    for item in ({"a": 1}, {}, {"a": 2}):
        prepared.evaluate_item(item)
    assert len(calls) == 1


def test_prepare_refuses_an_invalid_tree():
    with pytest.raises(c.ConditionError):
        c.prepare({"op": "nope"})


def test_a_prepared_condition_is_built_only_by_prepare():
    with pytest.raises(TypeError):
        c.PreparedCondition({"op": "exists", "arg": {"literal": 1}})


# -- public helpers ------------------------------------------------------------------------


def test_walk_path_follows_a_dotted_path():
    assert c.walk_path({"a": {"b": 1}}, "a.b") == 1


def test_walk_path_gives_missing_for_an_absent_path():
    assert c.walk_path({"a": {}}, "a.b") is c.MISSING


def test_walk_path_dot_is_the_value_itself():
    assert c.walk_path([1], ".") == [1]


# -- host_suffix compares ASCII hosts only ----------------------------------------------------


@pytest.mark.parametrize(
    "host, suffix",
    [
        ("straße.de", "strasse.de"),
        ("strasse.de", "straße.de"),
        ("Ka.com", "ka.com"),  # KELVIN SIGN lower-cases to k
        ("ｅxample.com", "example.com"),
        ("example.com", "ｅxample.com"),
    ],
)
def test_host_suffix_never_folds_non_ascii(host, suffix):
    assert c.host_matches_suffix(host, suffix) is False


def test_host_suffix_still_ignores_ascii_case():
    assert c.host_matches_suffix("Docs.EXAMPLE.com", "example.COM") is True


def test_host_suffix_matches_a_punycode_name():
    assert c.host_matches_suffix("www.xn--strae-oqa.de", "xn--strae-oqa.de") is True


def test_host_suffix_dot_only_suffix_is_false():
    assert c.host_matches_suffix("example.com", ".") is False
