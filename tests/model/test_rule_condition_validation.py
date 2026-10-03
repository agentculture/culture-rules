"""A Rule's condition is validated with the condition module (spec c3; Qwen/t9 finding)."""

from __future__ import annotations

from culture_rules.model.validate import validate
from tests.model.factories import make_rule


def test_factory_rule_condition_is_a_valid_condition_tree() -> None:
    assert validate(make_rule()) == []


def test_rule_with_malformed_condition_tree_is_rejected() -> None:
    rule = make_rule(condition={"==": [{"var": "trigger.data.base"}, "main"]})
    errors = validate(rule)
    assert any(e.code == "condition_invalid" and e.path.startswith("condition") for e in errors)


def test_rule_without_condition_still_validates() -> None:
    assert validate(make_rule(condition=None)) == []
