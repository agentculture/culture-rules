"""Criteria 2 + 3: structured validation of every model (never raises)."""

from __future__ import annotations

import dataclasses

import pytest

from culture_rules.model.action import Action
from culture_rules.model.actor import ACTOR_KINDS, Actor
from culture_rules.model.common import RetryPolicy
from culture_rules.model.machine import Machine
from culture_rules.model.placement import Placement
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.model.validate import ValidationError, validate, validate_data
from culture_rules.model.workflow import STEP_KINDS, Edge, Output, Port, Step, Variable, Workflow
from tests.model.factories import (
    make_action,
    make_actor,
    make_machine,
    make_rule,
    make_step,
    make_workflow,
)


def codes(errors: list[ValidationError]) -> set[str]:
    return {e.code for e in errors}


def paths(errors: list[ValidationError]) -> set[str]:
    return {e.path for e in errors}


# --- fixtures are valid ---------------------------------------------------


@pytest.mark.parametrize(
    "obj",
    [make_rule(), make_workflow(), make_step(), make_action(), make_actor(), make_machine()],
    ids=["rule", "workflow", "step", "action", "actor", "machine"],
)
def test_full_fixtures_validate(obj) -> None:
    assert validate(obj) == []


def test_validation_error_is_structured() -> None:
    err = validate(make_rule(action=None))[0]
    assert isinstance(err, ValidationError)
    assert err.to_dict() == {"path": err.path, "code": err.code, "message": err.message}


# --- rules ----------------------------------------------------------------


def test_rule_without_action_rejected() -> None:
    errors = validate(make_rule(action=None))
    assert "required" in codes(errors)
    assert "action" in paths(errors)


def test_rule_without_action_rejected_from_data() -> None:
    obj, errors = validate_data(Rule, {"id": "r", "name": "n", "trigger": {"kind": "manual"}})
    assert obj is not None
    assert ("action", "required") in {(e.path, e.code) for e in errors}


def test_rule_without_condition_and_workflow_validates() -> None:
    rule = Rule(id="r", name="n", trigger=Trigger(kind="event"), action=Action(kind="noop"))
    assert rule.condition is None and rule.workflow is None
    assert validate(rule) == []


def test_rule_without_trigger_rejected() -> None:
    assert ("trigger", "required") in {(e.path, e.code) for e in validate(make_rule(trigger=None))}


def test_rule_requires_id_and_name() -> None:
    errors = validate(make_rule(id="", name=""))
    assert {"id", "name"} <= paths(errors)


def test_rule_cannot_relate_to_itself() -> None:
    errors = validate(make_rule(must_after=("r-review",), supersedes=("r-review",)))
    assert "self_reference" in codes(errors)
    assert {"must_after[0]", "supersedes[0]"} <= paths(errors)


def test_rule_duplicate_relationship_rejected() -> None:
    errors = validate(make_rule(must_after=("a", "a")))
    assert "duplicate" in codes(errors)


def test_rule_condition_must_be_object() -> None:
    errors = validate(make_rule(condition=["not", "a", "dict"]))
    assert ("condition", "type") in {(e.path, e.code) for e in errors}


def test_rule_priority_without_group_is_fine_and_group_must_be_nonempty() -> None:
    assert validate(make_rule(exclusive_group=None, priority=5)) == []
    assert "exclusive_group" in paths(validate(make_rule(exclusive_group="")))


def test_rule_workflow_ref_requires_id() -> None:
    errors = validate(make_rule(workflow=WorkflowRef(id="")))
    assert "workflow.id" in paths(errors)


def test_newer_major_schema_version_rejected() -> None:
    errors = validate(make_rule(schema_version="2.0"))
    assert "schema_version" in codes(errors)
    assert validate(make_rule(schema_version="1.7")) == []
    assert "schema_version" in codes(validate(make_rule(schema_version="garbage")))


# --- actions --------------------------------------------------------------


def test_action_requires_kind_and_positive_timeout() -> None:
    errors = validate(make_action(kind="", timeout_s=0.0))
    assert {"kind", "timeout_s"} <= paths(errors)


def test_retry_policy_bounds() -> None:
    errors = validate(make_action(retry=RetryPolicy(max_attempts=0, backoff_s=-1.0)))
    assert {"retry.max_attempts", "retry.backoff_s"} <= paths(errors)


# --- workflows ------------------------------------------------------------


def test_workflow_dataclass_has_no_trigger_field() -> None:
    names = {f.name for f in dataclasses.fields(Workflow)} | {
        f.name for f in dataclasses.fields(Step)
    }
    assert not any("trigger" in n for n in names)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda wf: dataclasses.replace(
            wf, outputs=(Output(name="o", type="string", source="trigger.data.title"),)
        ),
        lambda wf: dataclasses.replace(
            wf,
            steps=(make_step(config={"prompt": "Summarise {{ trigger.data.body }}"}),),
            edges=(),
        ),
        lambda wf: dataclasses.replace(
            wf, steps=(make_step(config={"nested": [{"trigger": "github.pr.merged"}]}),), edges=()
        ),
        lambda wf: dataclasses.replace(
            wf, variables=(Variable(name="v", type="string", default="${trigger.id}"),)
        ),
        lambda wf: dataclasses.replace(
            wf,
            steps=(
                make_step(
                    id="loop",
                    kind="for_each",
                    max_iterations=3,
                    inputs=(),
                    outputs=(),
                    body=(make_step(id="inner", config={"x": "trigger"}),),
                ),
            ),
            edges=(),
        ),
    ],
    ids=["output-source", "template", "config-key", "variable-default", "loop-body"],
)
def test_workflow_with_trigger_reference_rejected(mutate) -> None:
    errors = validate(mutate(make_workflow()))
    assert "trigger_reference" in codes(errors)


def test_prose_mentioning_trigger_is_not_a_reference() -> None:
    wf = make_workflow(description="Runs whatever the trigger was")
    assert validate(wf) == []


def test_workflow_data_with_trigger_key_rejected() -> None:
    _, errors = validate_data(Workflow, {"id": "w", "name": "n", "trigger": {"kind": "event"}})
    assert "unknown_field" in codes(errors)


@pytest.mark.parametrize("kind", ["for_each", "retry_until"])
def test_loop_without_max_rejected(kind) -> None:
    wf = make_workflow(
        steps=(make_step(id="loop", kind=kind, max_iterations=None, inputs=(), outputs=()),),
        edges=(),
        outputs=(),
    )
    errors = validate(wf)
    assert ("steps[0].max_iterations", "loop_max_required") in {(e.path, e.code) for e in errors}


@pytest.mark.parametrize("bad", [0, -1])
def test_loop_max_must_be_positive(bad) -> None:
    step = make_step(kind="for_each", max_iterations=bad)
    assert "max_iterations" in paths(validate(step))


def test_nested_loop_without_max_rejected() -> None:
    inner = make_step(id="inner", kind="retry_until", max_iterations=None)
    outer = make_step(id="outer", kind="for_each", max_iterations=5, body=(inner,))
    errors = validate(outer)
    assert ("body[0].max_iterations", "loop_max_required") in {(e.path, e.code) for e in errors}


def test_non_loop_step_cannot_carry_max_or_body() -> None:
    errors = validate(make_step(kind="code", max_iterations=3, body=(make_step(id="x"),)))
    assert {"max_iterations", "body"} <= paths(errors)


def test_all_six_step_kinds_known() -> None:
    assert set(STEP_KINDS) == {"logic", "ai", "code", "actor_task", "for_each", "retry_until"}
    assert "kind" in paths(validate(make_step(kind="shell")))


def test_duplicate_step_ids_rejected_including_nested() -> None:
    loop = make_step(id="loop", kind="for_each", max_iterations=2, body=(make_step(id="a"),))
    wf = make_workflow(steps=(make_step(id="a"), loop), edges=(), outputs=())
    assert "duplicate" in codes(validate(wf))


def test_edge_to_unknown_step_or_port_rejected() -> None:
    wf = make_workflow(
        edges=(
            Edge(source="nope", source_port="x", target="fetch", target_port="pr"),
            Edge(source="fetch", source_port="diff", target="summarise", target_port="missing"),
        )
    )
    errors = validate(wf)
    assert {"edges[0].source", "edges[1].target_port"} <= paths(errors)


def test_edge_port_type_mismatch_rejected() -> None:
    wf = make_workflow(
        edges=(Edge(source="inputs", source_port="pr", target="summarise", target_port="diff"),)
    )
    assert "port_type_mismatch" in codes(validate(wf))


def test_edge_cycle_rejected() -> None:
    a = make_step(
        id="a", inputs=(Port(name="i", type="string"),), outputs=(Port(name="o", type="string"),)
    )
    b = make_step(
        id="b", inputs=(Port(name="i", type="string"),), outputs=(Port(name="o", type="string"),)
    )
    wf = make_workflow(
        steps=(a, b),
        outputs=(),
        edges=(
            Edge(source="a", source_port="o", target="b", target_port="i"),
            Edge(source="b", source_port="o", target="a", target_port="i"),
        ),
    )
    assert "cycle" in codes(validate(wf))


def test_unknown_port_type_rejected() -> None:
    assert "inputs[0].type" in paths(validate(make_step(inputs=(Port(name="x", type="blob"),))))


def test_workflow_version_must_be_positive() -> None:
    assert "version" in paths(validate(make_workflow(version=0)))


def test_output_source_must_reference_known_step() -> None:
    wf = make_workflow(outputs=(Output(name="o", type="string", source="steps.ghost.outputs.x"),))
    assert "outputs[0].source" in paths(validate(wf))


# --- placement ------------------------------------------------------------


@pytest.mark.parametrize(
    "placement",
    [
        Placement(),
        Placement(machine="spark", actor="ori"),
        Placement(machine="spark", requirement=("gpu",)),
        Placement(machine="spark", actor="ori", requirement=("gpu",)),
        Placement(requirement=()),
        Placement(machine=""),
    ],
    ids=["none", "machine+actor", "machine+req", "all-three", "empty-req", "empty-machine"],
)
def test_placement_not_exactly_one_form_rejected(placement) -> None:
    errors = validate(placement)
    assert "placement_form" in codes(errors)


@pytest.mark.parametrize(
    "placement",
    [Placement(machine="spark"), Placement(actor="ori"), Placement(requirement=("gpu",))],
    ids=["machine", "actor", "requirement"],
)
def test_placement_each_single_form_validates(placement) -> None:
    assert validate(placement) == []
    assert placement.form in ("machine", "actor", "requirement")


def test_step_with_bad_placement_reports_nested_path() -> None:
    errors = validate(make_step(placement=Placement(machine="a", actor="b")))
    assert ("placement", "placement_form") in {(e.path, e.code) for e in errors}


# --- actors ---------------------------------------------------------------


def test_actor_six_kinds() -> None:
    assert set(ACTOR_KINDS) == {"agent", "human", "service", "daemon", "runner", "robot"}


@pytest.mark.parametrize("kind", ["agent", "human", "service", "daemon", "runner", "robot"])
def test_each_actor_kind_validates(kind) -> None:
    assert validate(make_actor(kind=kind)) == []


@pytest.mark.parametrize("kind", ["bot", "", "Agent", "code"])
def test_actor_kind_outside_six_rejected(kind) -> None:
    errors = validate(make_actor(kind=kind))
    assert ("kind", "invalid_kind") in {(e.path, e.code) for e in errors}


def test_actor_config_source() -> None:
    assert "config_source" in paths(validate(make_actor(config_source="file")))
    assert "repo" in paths(validate(make_actor(config_source="repo", repo=None)))
    assert validate(make_actor(config_source="db", repo=None)) == []


def test_actor_harness_and_model_optional() -> None:
    assert validate(Actor(id="a", name="a", kind="agent")) == []


# --- machines -------------------------------------------------------------


def test_machine_roles_and_name() -> None:
    errors = validate(make_machine(name="", roles=("store_member", "janitor")))
    assert {"name", "roles[1]"} <= paths(errors)


# --- validate_data --------------------------------------------------------


def test_validate_data_parse_error_is_structured_not_raised() -> None:
    obj, errors = validate_data(Machine, {"name": 7})
    assert obj is None
    assert errors and errors[0].path == "name" and errors[0].code == "type"


def test_validate_data_accepts_json_text() -> None:
    obj, errors = validate_data(Actor, '{"id":"a","name":"a","kind":"robot"}')
    assert errors == [] and obj == Actor(id="a", name="a", kind="robot")


def test_validate_rejects_non_model() -> None:
    errors = validate(object())
    assert errors and errors[0].code == "type"


def test_step_kind_list_and_actor_kind_list_are_tuples() -> None:
    assert isinstance(STEP_KINDS, tuple) and isinstance(ACTOR_KINDS, tuple)
