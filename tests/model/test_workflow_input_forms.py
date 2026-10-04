"""t6 (#7): WorkflowRef.inputs accepts plain strings and the structured $ref/$literal forms."""

from __future__ import annotations

import pytest

from culture_rules.model.rule import Rule, WorkflowRef
from culture_rules.model.schema import json_schema
from culture_rules.model.validate import validate
from tests.model.factories import make_rule


def _rule(inputs: dict) -> Rule:
    return make_rule(workflow=WorkflowRef(id="wf", inputs=inputs))


def test_structured_forms_round_trip_and_validate() -> None:
    inputs = {"a": "trigger.data.n", "b": {"$ref": "trigger.data.n"}, "c": {"$literal": [1, "x"]}}
    rule = _rule(inputs)
    assert validate(rule) == []
    again = Rule.from_dict(rule.to_dict())
    assert again == rule
    assert again.workflow.inputs == inputs


def test_schema_allows_string_or_object_values() -> None:
    schema = json_schema(WorkflowRef)
    spec = schema["properties"]["inputs"]["additionalProperties"]
    assert {"type": "string"} in spec["anyOf"]
    assert {"type": "object"} in spec["anyOf"]


@pytest.mark.parametrize(
    "bad",
    [5, {"$ref": 3}, {"$ref": "a", "$literal": 1}, {"other": 1}, {}],
)
def test_malformed_input_mapping_is_path_coded(bad) -> None:
    errors = validate(_rule({"x": bad}))
    assert ("workflow.inputs.x", "invalid_input_mapping") in {(e.path, e.code) for e in errors}
