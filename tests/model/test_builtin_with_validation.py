"""``config.with`` on a builtin code step is validated as an action step's params are (d8)."""

from __future__ import annotations

import pytest

from culture_rules.model.validate import validate
from tests.engine.run_helpers import port, step, workflow


def wf(config, kind="code"):
    s = step("s", kind, inputs=(port("item", "object"),), config=config)
    return workflow((s,))


def codes(w):
    return {(e.path, e.code) for e in validate(w)}


def test_with_of_input_references_and_literals_is_accepted():
    cfg = {"builtin": "markdown.render", "with": {"text": "inputs.item.content", "title": "Hi"}}
    assert validate(wf(cfg)) == []


def test_a_reference_to_an_undeclared_port_is_refused():
    found = codes(wf({"builtin": "markdown.render", "with": {"text": "inputs.nope.content"}}))
    assert ("steps[0].config.with.text", "builtin_with_ref") in found


@pytest.mark.parametrize("ref", ["trigger.data.content", "{{ run.id }}"])
def test_a_reference_outside_the_steps_inputs_is_refused(ref):
    found = codes(wf({"builtin": "markdown.render", "with": {"text": ref}}))
    assert ("steps[0].config.with.text", "builtin_with_ref") in found


@pytest.mark.parametrize("value", ["text", ["x"], 3])
def test_with_must_be_an_object(value):
    found = codes(wf({"builtin": "markdown.render", "with": value}))
    assert ("steps[0].config.with", "type") in found


def test_with_belongs_to_a_builtin_code_step_only():
    found = codes(wf({"with": {"text": "inputs.item.content"}}))
    assert ("steps[0].config.with", "not_allowed") in found
    action = {"builtin": "action", "action": {"kind": "noop", "params": {}}, "with": {"a": "b"}}
    assert ("steps[0].config.with", "not_allowed") in codes(wf(action))
