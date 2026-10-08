"""Variable references in action params and workflow-input mappings (finding #4).

A plain string is a reference only when its path fits a known namespace's shape
(``trigger.<envelope field or key the event has>...``, ``workflow.outputs.<name>``,
``rules.<id>.outputs.<name>``); anything else - ``rules.yaml``, ``workflow.md``,
``trigger.sh`` - is a literal. ``{"$ref": path}`` always references, ``{"$literal": v}``
never does. A ``$ref`` that can never resolve is refused by rule validation (save time).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from culture_rules.engine.ruleset import validate_rule_set
from culture_rules.engine.runs import ACTION_STEP, Executor, RunError, step_state
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.model.validate import validate
from culture_rules.model.workflow import Output, Port
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, port, rule, step, workflow

TRIGGER = {"id": "e1", "type": "t", "data": {"args": "-v", "sh": "data-sh"}}


@pytest.fixture
def ex() -> Executor:
    clock = Clock()
    return Executor(MemoryStore(clock=clock), "spark", {"*": FakeActor()}, clock=clock)


def action_inputs(ex: Executor, params: dict, trigger: dict | None = None) -> dict:
    r = rule(workflow_id=None, action=Action(kind="noop", params=params))
    run = ex.start(r, trigger=trigger if trigger is not None else TRIGGER)
    ex.run_until_idle()
    return step_state(ex.run(run["id"]), ACTION_STEP)["inputs"]


def test_literals_that_look_like_references_reach_the_port_unchanged(ex):
    params = {"file": "rules.yaml", "doc": "workflow.md", "hook": "trigger.sh"}
    assert action_inputs(ex, params) == params


def test_plain_and_structured_references_resolve(ex):
    got = action_inputs(ex, {"plain": "trigger.id", "ref": {"$ref": "trigger.id"}})
    assert got == {"plain": "e1", "ref": "e1"}


def test_a_literal_wrapper_keeps_a_string_that_would_resolve(ex):
    got = action_inputs(ex, {"lit": {"$literal": "trigger.id"}, "obj": {"$literal": {"a": 1}}})
    assert got == {"lit": "trigger.id", "obj": {"a": 1}}


def test_known_envelope_paths_still_reference_when_absent(ex):
    # trigger.data is part of every envelope, so a missing key is "no value", not a literal
    got = action_inputs(ex, {"missing": "trigger.data.nope", "args": "trigger.data.args"})
    assert got == {"missing": None, "args": "-v"}


def test_a_key_the_event_carries_is_a_reference(ex):
    got = action_inputs(ex, {"hook": "trigger.sh"}, trigger={"id": "e2", "sh": "yes"})
    assert got == {"hook": "yes"}


def test_structured_ref_resolves_nested_and_templates_still_work(ex):
    got = action_inputs(
        ex, {"nested": [{"$ref": "trigger.data.args"}], "msg": "id={{ trigger.id }}"}
    )
    assert got == {"nested": ["-v"], "msg": "id=e1"}


def test_workflow_input_mapping_keeps_a_literal_file_name(ex):
    wf = replace(
        workflow((step("a"),)),
        inputs=(Port(name="file", type="string"), Port(name="args", type="string")),
    )
    r = rule(workflow_inputs={"file": "workflow.md", "args": "trigger.data.args"})
    run = ex.start(r, wf, trigger=TRIGGER)
    assert ex.run(run["id"])["inputs"] == {"file": "workflow.md", "args": "-v"}


def test_workflow_input_mapping_to_an_absent_trigger_value_is_still_missing(ex):
    wf = replace(workflow((step("a"),)), inputs=(Port(name="args", type="string"),))
    r = rule(workflow_inputs={"args": "trigger.data.nope"})
    with pytest.raises(RunError) as err:
        ex.start(r, wf, trigger=TRIGGER)
    assert err.value.code == "input_missing"


def test_workflow_outputs_reference_and_lookalike_literal(ex):
    wf = replace(
        workflow((step("a", outputs=(port("n", "integer"),)),)),
        outputs=(Output(name="n", source="steps.a.outputs.n"),),
    )
    fake = FakeActor(default=lambda inp, ctx: {"n": 3})
    ex2 = Executor(ex._store, "spark", {"*": fake}, clock=ex._clock)
    act = Action(kind="noop", params={"n": "workflow.outputs.n", "doc": "workflow.md"})
    run = ex2.start(replace(rule(), action=act), wf, trigger=TRIGGER)
    ex2.run_until_idle()
    inputs = step_state(ex2.run(run["id"]), ACTION_STEP)["inputs"]
    assert inputs == {"n": 3, "doc": "workflow.md"}


# --------------------------------------------------------------------------- save time


def ref_rule(params: dict, *, workflow: WorkflowRef | None = None, **kw) -> Rule:
    return Rule(
        id=kw.pop("id", "r"),
        name="r",
        trigger=Trigger(kind="event", params={"type": "t"}),
        workflow=workflow,
        action=Action(kind="noop", params=params),
        **kw,
    )


def codes(errors) -> list[str]:
    return [e.code for e in errors]


def test_a_ref_to_an_unknown_namespace_is_refused():
    errors = validate(ref_rule({"x": {"$ref": "secrets.token"}}))
    assert codes(errors) == ["invalid_reference"]
    assert errors[0].path == "action.params.x"


@pytest.mark.parametrize(
    "ref", ["trigger", "rules.up", "rules.up.exports.x", "workflow.vars.x", "", 7]
)
def test_malformed_refs_are_refused(ref):
    assert codes(validate(ref_rule({"x": [{"$ref": ref}]}))) == ["invalid_reference"]


def test_workflow_outputs_ref_needs_a_workflow():
    errors = validate(ref_rule({"x": {"$ref": "workflow.outputs.n"}}))
    assert codes(errors) == ["invalid_reference"]
    with_wf = ref_rule({"x": {"$ref": "workflow.outputs.n"}}, workflow=WorkflowRef(id="wf"))
    assert validate(with_wf) == []


def test_valid_refs_and_literals_pass():
    params = {
        "a": {"$ref": "trigger.data.x"},
        "b": {"$literal": "rules.nobody.outputs.secret"},
        "c": "rules.yaml",
    }
    assert validate(ref_rule(params)) == []
    assert validate_rule_set([ref_rule(params)]) == []


def test_a_ref_to_a_rule_output_is_checked_like_a_plain_reference():
    errors = validate_rule_set([ref_rule({"x": {"$ref": "rules.up.outputs.s"}})])
    assert codes(errors) == ["not_a_predecessor"]


# --------------------------------------------------------------------------- run.id


def test_run_id_resolves_in_rule_action_params(ex):
    got = action_inputs(
        ex,
        {"plain": "run.id", "ref": {"$ref": "run.id"}, "body": "see /runs/{{ run.id }} now"},
    )
    run_id = got["plain"]
    assert isinstance(run_id, str)
    assert run_id.startswith("run-")
    assert got["ref"] == run_id
    assert got["body"] == f"see /runs/{run_id} now"
    assert ex.run(run_id)["id"] == run_id  # it is this run's own id


def test_run_lookalike_literals_stay_literal(ex):
    params = {"a": "run.sh", "b": "run.id.x", "c": "{{ run.other }}"}
    assert action_inputs(ex, params) == {"a": "run.sh", "b": "run.id.x", "c": "{{ run.other }}"}


def test_run_id_ref_is_valid_and_other_run_fields_are_refused():
    assert validate(ref_rule({"x": {"$ref": "run.id"}})) == []
    assert codes(validate(ref_rule({"x": {"$ref": "run.status"}}))) == ["invalid_reference"]
