"""Deviation d1 (Rule.placement) and obligation o3 (readers ignore unknown fields)."""

from __future__ import annotations

import json

import pytest

from culture_rules.model.placement import Placement
from culture_rules.model.rule import Rule
from culture_rules.model.schema import generate_all
from culture_rules.model.serde import ModelParseError
from culture_rules.model.validate import validate
from tests.model.factories import make_rule


@pytest.mark.parametrize(
    "placement",
    [Placement(machine="thor"), Placement(actor="claude-code"), Placement(requirement=("gpu",))],
)
def test_rule_placement_round_trips_and_validates(placement: Placement) -> None:
    rule = make_rule(placement=placement)
    assert Rule.from_json(rule.to_json()) == rule
    assert validate(rule) == []


def test_rule_placement_is_optional() -> None:
    data = json.loads(make_rule().to_json())
    data.pop("placement", None)
    assert Rule.from_dict(data).placement is None


def test_rule_with_two_placement_forms_rejected() -> None:
    rule = make_rule(placement=Placement(machine="thor", actor="codex"))
    errors = validate(rule)
    assert any(e.code == "placement_form" and e.path.startswith("placement") for e in errors)


def test_rule_schema_carries_optional_placement() -> None:
    schema = json.loads(generate_all()["rule.schema.json"])
    assert "placement" in schema["properties"]
    assert "placement" not in schema["required"]


def test_unknown_field_rejected_by_default() -> None:
    data = json.loads(make_rule().to_json())
    data["added_in_a_newer_minor"] = 1
    with pytest.raises(ModelParseError):
        Rule.from_dict(data)


def test_tolerant_read_ignores_unknown_fields_at_every_depth() -> None:
    data = json.loads(make_rule().to_json())
    data["added_in_a_newer_minor"] = 1
    data["action"]["also_new"] = {"x": 1}
    rule = Rule.from_dict(data, strict=False)
    assert rule == make_rule()
    assert Rule.from_json(json.dumps(data), strict=False) == make_rule()


def test_tolerant_flag_does_not_leak_into_later_strict_reads() -> None:
    data = json.loads(make_rule().to_json())
    data["new_field"] = True
    Rule.from_dict(data, strict=False)
    with pytest.raises(ModelParseError):
        Rule.from_dict(data)
