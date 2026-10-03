"""Criterion 3 + seam obligation: every model round-trips to_json -> from_json identically."""

from __future__ import annotations

import json

import pytest

from culture_rules.model.action import Action
from culture_rules.model.actor import Actor
from culture_rules.model.common import SCHEMA_VERSION
from culture_rules.model.machine import Machine
from culture_rules.model.placement import Placement
from culture_rules.model.rule import Rule, Trigger
from culture_rules.model.serde import ModelParseError
from culture_rules.model.workflow import Step, Workflow
from tests.model.factories import (
    make_action,
    make_actor,
    make_machine,
    make_rule,
    make_step,
    make_workflow,
)

ALL = [
    pytest.param(make_rule(), id="rule"),
    pytest.param(make_workflow(), id="workflow"),
    pytest.param(make_step(), id="step"),
    pytest.param(make_action(), id="action"),
    pytest.param(make_actor(), id="actor"),
    pytest.param(make_machine(), id="machine"),
    pytest.param(Placement(machine="thor"), id="placement-machine"),
    pytest.param(Placement(actor="ori"), id="placement-actor"),
    pytest.param(Placement(requirement=("gpu", "cuda")), id="placement-requirement"),
    pytest.param(
        Rule(
            id="r-min", name="minimal", trigger=Trigger(kind="manual"), action=Action(kind="noop")
        ),
        id="rule-minimal",
    ),
    pytest.param(Workflow(id="wf-empty", name="empty"), id="workflow-minimal"),
    pytest.param(Actor(id="h", name="human", kind="human"), id="actor-minimal"),
    pytest.param(Machine(name="thor"), id="machine-minimal"),
]


@pytest.mark.parametrize("obj", ALL)
def test_roundtrip_object_identical(obj) -> None:
    cls = type(obj)
    text = obj.to_json()
    back = cls.from_json(text)
    assert back == obj
    assert back.to_json() == text


@pytest.mark.parametrize("obj", ALL)
def test_to_json_is_stable_sorted_compact(obj) -> None:
    text = obj.to_json()
    assert text == json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
    assert obj.to_json() == text  # deterministic


@pytest.mark.parametrize("obj", ALL)
def test_dict_roundtrip(obj) -> None:
    cls = type(obj)
    data = obj.to_dict()
    assert cls.from_dict(data) == obj
    # to_dict output is plain JSON (lists, not tuples)
    assert json.loads(json.dumps(data)) == data


def _keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            if k not in ("params", "config", "condition", "inputs", "default"):
                yield from _keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from _keys(v)


@pytest.mark.parametrize("obj", ALL)
def test_field_names_are_snake_case(obj) -> None:
    import re

    for key in _keys(obj.to_dict()):
        if isinstance(key, str) and not key.startswith("$"):
            assert re.fullmatch(r"[a-z][a-z0-9_]*", key), key


def test_top_level_documents_carry_schema_version() -> None:
    for obj in (make_rule(), make_workflow(), make_actor(), make_machine()):
        assert obj.to_dict()["schema_version"] == SCHEMA_VERSION


def test_from_json_missing_optional_fields_use_defaults() -> None:
    rule = Rule.from_json(
        '{"id":"r","name":"n","trigger":{"kind":"manual"},"action":{"kind":"noop"}}'
    )
    assert rule.condition is None
    assert rule.workflow is None
    assert rule.enabled is True
    assert rule.must_after == ()
    assert rule.schema_version == SCHEMA_VERSION


def test_from_json_unknown_field_is_a_parse_error() -> None:
    with pytest.raises(ModelParseError) as exc:
        Machine.from_json('{"name":"thor","colour":"red"}')
    assert exc.value.path == "colour"


def test_from_json_wrong_type_reports_path() -> None:
    with pytest.raises(ModelParseError) as exc:
        Workflow.from_json(
            '{"id":"w","name":"n","steps":[{"id":"s","kind":"code","timeout_s":"x"}]}'
        )
    assert exc.value.path == "steps[0].timeout_s"


def test_from_json_rejects_non_object() -> None:
    with pytest.raises(ModelParseError):
        Step.from_json("[1, 2]")
    with pytest.raises(ModelParseError):
        Step.from_json("{not json")


def test_int_accepted_for_float_field() -> None:
    step = Step.from_json('{"id":"s","name":"s","kind":"code","timeout_s":30}')
    assert step.timeout_s == 30.0 and isinstance(step.timeout_s, float)


def test_bool_not_accepted_for_int_field() -> None:
    with pytest.raises(ModelParseError):
        Rule.from_json(
            '{"id":"r","name":"n","trigger":{"kind":"manual"},"action":{"kind":"noop"},'
            '"priority":true}'
        )


def test_models_are_frozen(rule) -> None:
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        rule.enabled = False  # type: ignore[misc]
