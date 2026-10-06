"""t7: variable references in rules - the model, matching and save-time checks (c25, h17, c34).

Pure-layer tests; the node-level behaviour lives in ``tests/node/test_shared_variables.py``.
"""

from __future__ import annotations

import json

import pytest

from culture_rules.engine.matching import (
    CONDITION_FALSE,
    FIRE,
    REASONS,
    VARIABLE_UNDEFINED,
    VARIABLES_UNSUPPORTED,
    match,
)
from culture_rules.model.action import Action
from culture_rules.model.refs import resolve_refs
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.model.validate import validate, variable_ref_errors
from culture_rules.server.service import Definitions, Invalid
from culture_rules.store.memory import MemoryStore

EVENT = {"id": "e1", "type": "pr.comment", "data": {"author": "qodo"}}
IN_X = {"op": "in", "value": {"field": "data.author"}, "items": {"var": "x"}}
NOT_IN_X = {"op": "not", "arg": IN_X}


def rule(id: str = "r", condition: dict | None = None, inputs: dict | None = None) -> Rule:
    return Rule(
        id=id,
        name=id,
        trigger=Trigger(kind="event", params={"type": "pr.comment"}),
        condition=condition,
        workflow=WorkflowRef(id="wf", inputs=inputs) if inputs is not None else None,
        action=Action(kind="noop"),
    )


# --------------------------------------------------------------------------- matching


def test_match_reads_variables_into_the_condition():
    (d,) = match(EVENT, [rule(condition=IN_X)], variables={"x": ["qodo"]})
    assert d.fire and d.reason == FIRE
    (d,) = match(EVENT, [rule(condition=IN_X)], variables={"x": ["someone"]})
    assert not d.fire and d.reason == CONDITION_FALSE


def test_without_variable_support_a_negated_reference_does_not_fire():
    (d,) = match(EVENT, [rule(condition=NOT_IN_X)], variables={}, variables_supported=False)
    assert not d.fire
    assert d.reason == VARIABLES_UNSUPPORTED
    assert "variables" in d.message


def test_an_undefined_variable_never_evaluates_as_missing():
    (d,) = match(EVENT, [rule(condition=NOT_IN_X)], variables={})
    assert not d.fire
    assert d.reason == VARIABLE_UNDEFINED
    assert "x" in d.message


def test_rules_without_references_are_unaffected_by_missing_support():
    (d,) = match(EVENT, [rule()], variables_supported=False)
    assert d.fire


def test_an_input_only_reference_needs_support_too():
    r = rule(inputs={"authors": {"$var": "x"}})
    (d,) = match(EVENT, [r], variables={"x": []}, variables_supported=False)
    assert d.reason == VARIABLES_UNSUPPORTED


def test_the_new_reasons_are_known():
    assert {VARIABLES_UNSUPPORTED, VARIABLE_UNDEFINED} <= set(REASONS)


# --------------------------------------------------------------------------- refs


def test_var_inputs_resolve_against_the_variables_namespace():
    ctx = {"trigger": {}, "variables": {"x": ["a"]}}
    assert resolve_refs({"$var": "x"}, ctx) == ["a"]
    assert resolve_refs({"$var": "missing"}, ctx) is None


def test_var_objects_pass_through_where_variables_are_not_resolved():
    # Action params have no variables namespace: the object stays as written.
    assert resolve_refs({"$var": "x"}, {"trigger": {}}) == {"$var": "x"}


def test_resolved_variable_values_are_copies():
    value = ["a"]
    out = resolve_refs({"$var": "x"}, {"variables": {"x": value}})
    out.append("b")
    assert value == ["a"]


# --------------------------------------------------------------------------- validate


def test_workflow_ref_inputs_accept_a_var_mapping():
    assert validate(rule(inputs={"authors": {"$var": "trusted_authors"}})) == []


@pytest.mark.parametrize("bad", [{"$var": ""}, {"$var": 3}, {"$var": "Bad-Name"}])
def test_a_malformed_var_mapping_is_refused(bad):
    errors = validate(rule(inputs={"authors": bad}))
    assert [e.code for e in errors] == ["invalid_input_mapping"]


def test_variable_ref_errors_name_each_undefined_variable():
    r = rule(condition=NOT_IN_X, inputs={"authors": {"$var": "y"}, "z": {"$var": "x"}})
    errors = variable_ref_errors(r, {"x"})
    assert [(e.path, e.code) for e in errors] == [("workflow.inputs.authors", "variable_undefined")]
    assert "'y'" in errors[0].message
    errors = variable_ref_errors(r, set())
    assert [e.path for e in errors] == [
        "condition",
        "workflow.inputs.authors",
        "workflow.inputs.z",
    ]


# --------------------------------------------------------------------------- save


def _body(condition: dict) -> dict:
    return rule("guarded", condition=condition).to_dict()


def test_saving_a_rule_that_references_an_undefined_variable_is_refused():
    store = MemoryStore()
    defs = Definitions(store)
    cond = {"op": "in", "value": {"field": "data.author"}, "items": {"var": "missing"}}
    with pytest.raises(Invalid) as exc:
        defs.create("rules", _body(cond), "alice")
    (err,) = exc.value.errors
    assert err["code"] == "variable_undefined"
    assert "missing" in err["message"]
    assert store.find("rules") == []

    store.put_variable("missing", ["qodo"], updated_by="alice")
    assert defs.create("rules", _body(cond), "alice")["id"] == "guarded"


def test_importing_a_rule_that_references_an_undefined_variable_is_refused():
    store = MemoryStore()
    defs = Definitions(store)
    files = {"rules/guarded.json": json.dumps(_body(NOT_IN_X))}
    with pytest.raises(Invalid) as exc:
        defs.import_files(files, "alice", apply=True)
    assert any(
        e["code"] == "variable_undefined" and "'x'" in e["message"] for e in exc.value.errors
    )
    assert store.find("rules") == []
