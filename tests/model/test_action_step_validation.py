"""d12 save-time checks: a built-in action step names a catalogued kind with valid params."""

from __future__ import annotations

from typing import Any

import pytest

from culture_rules.model.validate import validate, validate_data
from culture_rules.model.workflow import Workflow
from tests.engine.run_helpers import port, step, workflow


def action_wf(kind: Any, params: Any, *, inputs=(), in_loop: bool = False) -> Workflow:
    config = {"builtin": "action", "action": {"kind": kind, "params": params}}
    s = step("act", "code", inputs=inputs, config=config)
    if in_loop:
        loop = step(
            "loop",
            "for_each",
            inputs=(port("items", "array"),),
            max_iterations=5,
            body=(s,),
        )
        return workflow((loop,))
    return workflow((s,))


def codes(wf: Workflow, *, stored: bool = False) -> set[tuple[str, str]]:
    return {(e.path, e.code) for e in validate(wf, stored=stored)}


REPLY = {
    "actor": "gh-app",
    "repo": "acme/widgets",
    "number": 3,
    "comment_id": "inputs.item.comment_id",
    "body": "{{ inputs.item.body }}",
    "resolve": True,
}


def test_valid_action_step_in_a_loop_body_passes():
    wf = action_wf("github.review_reply", REPLY, inputs=(port("item", "object"),), in_loop=True)
    assert validate(wf) == []


def test_unknown_action_kind_is_refused():
    found = codes(action_wf("github.merge", {"actor": "gh-app"}))
    assert ("steps[0].config.action.kind", "action_kind_unknown") in found


@pytest.mark.parametrize(
    "params, path, code",
    [
        ({**REPLY, "comment_id": None}, "params.comment_id", "action_param_required"),
        ({**REPLY, "number": "three"}, "params.number", "action_param_type"),
        ({**REPLY, "resolve": "yes"}, "params.resolve", "action_param_type"),
        ({k: v for k, v in REPLY.items() if k != "actor"}, "params.actor", "action_param_required"),
    ],
)
def test_bad_params_get_the_rule_action_kind_param_checks(params, path, code):
    found = codes(action_wf("github.review_reply", params, inputs=(port("item", "object"),)))
    assert (f"steps[0].config.action.{path}", code) in found


def test_input_reference_to_an_undeclared_port_is_refused():
    found = codes(action_wf("github.review_reply", REPLY))  # no "item" input port
    assert ("steps[0].config.action.params.comment_id", "action_step_ref") in found
    assert ("steps[0].config.action.params.body", "action_step_ref") in found


@pytest.mark.parametrize(
    "value", ["trigger.data.number", "{{ trigger.data.body }}", {"$ref": "workflow.outputs.x"}]
)
def test_references_outside_the_step_inputs_are_refused(value):
    params = {**REPLY, "body": value}
    found = codes(action_wf("github.review_reply", params, inputs=(port("item", "object"),)))
    assert ("steps[0].config.action.params.body", "action_step_ref") in found


@pytest.mark.parametrize("body", ["see rules.yaml and trigger.sh", "inputs"])
def test_literal_strings_are_not_references(body):
    params = {**REPLY, "body": body}
    wf = action_wf("github.review_reply", params, inputs=(port("item", "object"),))
    assert codes(wf) == set()


@pytest.mark.parametrize(
    "action", [None, "github.push", {"kind": ""}, {"kind": "noop", "params": []}]
)
def test_malformed_action_config_is_refused(action):
    s = step("act", "code", config={"builtin": "action", "action": action})
    assert ("steps[0].config.action", "action_step_invalid") in codes(workflow((s,)))


def test_stored_mode_drops_only_the_catalog_codes():
    wf = action_wf("github.merge", {"x": "inputs.nope"})
    assert ("steps[0].config.action.kind", "action_kind_unknown") not in codes(wf, stored=True)
    assert ("steps[0].config.action.params.x", "action_step_ref") in codes(wf, stored=True)


def test_other_code_steps_are_untouched():
    assert validate(workflow((step("g", "code", config={"builtin": "gate"}),))) == []


def test_save_path_refuses_through_validate_data():
    body = action_wf("nope.kind", {}).to_dict()
    obj, errors = validate_data(Workflow, body)
    assert obj is None or errors
    assert any(e.code == "action_kind_unknown" for e in errors)
