"""Save-time rule-set validation (t9): supersede/predecessor cycles, exported-output references."""

from __future__ import annotations

from culture_rules.engine.ruleset import validate_rule_set
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.model.workflow import Output, Workflow

WF = {
    "wf-up": Workflow(id="wf-up", name="up", outputs=(Output(name="summary", source="vars.s"),)),
}


def rule(rid: str, **kw) -> Rule:
    base = dict(
        id=rid,
        name=rid,
        trigger=Trigger(kind="event", params={"type": "t"}),
        action=Action(kind="mesh.message"),
    )
    base.update(kw)
    return Rule(**base)


def codes(errors) -> list[str]:
    return [e.code for e in errors]


def test_valid_rule_set_has_no_errors():
    rules = [
        rule("up", workflow=WorkflowRef(id="wf-up")),
        rule(
            "down",
            must_after=("up",),
            action=Action(kind="mesh.message", params={"text": "{{ rules.up.outputs.summary }}"}),
        ),
        rule("A", supersedes=("B",)),
        rule("B", supersedes=("C",)),
        rule("C"),
    ]
    assert validate_rule_set(rules, WF) == []


def test_supersede_cycle_is_rejected():
    rules = [
        rule("A", supersedes=("B",)),
        rule("B", supersedes=("C",)),
        rule("C", supersedes=("A",)),
    ]
    errors = validate_rule_set(rules, WF)
    assert codes(errors) == ["supersede_cycle"]
    assert "A -> B -> C -> A" in errors[0].message


def test_must_after_cycle_is_rejected():
    rules = [rule("A", must_after=("B",)), rule("B", must_after=("A",))]
    assert codes(validate_rule_set(rules, WF)) == ["predecessor_cycle"]


def test_reference_to_unexported_output_fails_validation():
    rules = [
        rule("up", workflow=WorkflowRef(id="wf-up")),
        rule(
            "down",
            must_after=("up",),
            action=Action(kind="mesh.message", params={"text": "rules.up.outputs.secret"}),
        ),
    ]
    errors = validate_rule_set(rules, WF)
    assert codes(errors) == ["unexported_output"]
    assert errors[0].path == "rules[1].action.params.text"


def test_reference_from_workflow_inputs_is_checked_too():
    rules = [
        rule("up"),  # no workflow -> exports nothing
        rule(
            "down",
            may_after=("up",),
            workflow=WorkflowRef(id="wf-up", inputs={"x": "rules.up.outputs.summary"}),
        ),
    ]
    errors = validate_rule_set(rules, WF)
    assert codes(errors) == ["unexported_output"]
    assert errors[0].path == "rules[1].workflow.inputs.x"


def test_reference_to_non_predecessor_fails_validation():
    rules = [
        rule("up", workflow=WorkflowRef(id="wf-up")),
        rule("down", action=Action(kind="m", params={"t": ["rules.up.outputs.summary"]})),
    ]
    errors = validate_rule_set(rules, WF)
    assert codes(errors) == ["not_a_predecessor"]
    assert errors[0].path == "rules[1].action.params.t[0]"


def test_dangling_references_are_reported_as_unknown_rule():
    r = rule("a", must_after=("ghost1",), may_after=("ghost2", "ghost3"), supersedes=("ghost4",))
    got = {(e.path, e.code) for e in validate_rule_set([r], {})}
    assert got >= {
        ("rules[0].must_after[0]", "unknown_rule"),
        ("rules[0].may_after[0]", "unknown_rule"),
        ("rules[0].may_after[1]", "unknown_rule"),
        ("rules[0].supersedes[0]", "unknown_rule"),
    }


def test_a_rule_that_supersedes_and_must_run_after_the_same_rule_is_unrunnable():
    rules = [rule("A", supersedes=("B",), must_after=("B",)), rule("B")]
    errors = validate_rule_set(rules, WF)
    assert codes(errors) == ["unrunnable_relationship"]
    assert errors[0].path == "rules[0]"
    assert "'A'" in errors[0].message and "'B'" in errors[0].message


def test_the_transitive_unrunnable_relationship_is_rejected_too():
    # A supersedes B supersedes C; A must run after D must run after C: whenever A
    # matches, C is skipped, so D never succeeds and A can never fire.
    rules = [
        rule("A", supersedes=("B",), must_after=("D",)),
        rule("B", supersedes=("C",)),
        rule("C"),
        rule("D", must_after=("C",)),
    ]
    errors = validate_rule_set(rules, WF)
    assert codes(errors) == ["unrunnable_relationship"]
    assert "'C'" in errors[0].message


def test_may_after_and_supersedes_on_the_same_rule_is_fine():
    rules = [rule("A", supersedes=("B",), may_after=("B",)), rule("B")]
    assert validate_rule_set(rules, WF) == []
