"""The variables a rule references: condition {"var": n} and workflow inputs {"$var": n}."""

from __future__ import annotations

from culture_rules.model.variable_refs import condition_variable_refs, rule_variable_refs


def test_condition_refs_are_found_at_any_depth():
    allowed = {"op": "in", "value": {"field": "author"}, "items": {"var": "trusted_authors"}}
    limit = {"op": "compare", "cmp": "<", "left": {"field": "n"}, "right": {"var": "limit"}}
    tree = {"op": "and", "args": [{"op": "not", "arg": allowed}, limit]}
    assert condition_variable_refs(tree) == {
        "trusted_authors",
        "limit",
    }


def test_a_literal_that_merely_contains_a_var_key_is_not_a_reference():
    tree = {"op": "in", "value": {"literal": "x"}, "items": {"literal": [{"var": "x"}]}}
    assert condition_variable_refs(tree) == set()


def test_rule_document_refs_cover_condition_and_inputs():
    rule = {
        "condition": {
            "op": "in",
            "value": {"field": "author"},
            "items": {"var": "trusted_authors"},
        },
        "workflow": {
            "id": "fix",
            "inputs": {
                "allow": {"$var": "trusted_authors"},
                "cap": {"$var": "max_attempts"},
                "n": "trigger.data.number",
            },
        },
    }
    assert rule_variable_refs(rule) == {"trusted_authors", "max_attempts"}


def test_no_condition_and_no_workflow_reference_nothing():
    assert rule_variable_refs({"condition": None, "workflow": None}) == set()
