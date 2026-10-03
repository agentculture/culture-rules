"""Rule matching (t9): all-fire, exclusive groups, supersede, must/may-after, exports, pause."""

from __future__ import annotations

import copy

import pytest

from culture_rules.engine.matching import (
    BLOCKED_BY_PREDECESSOR,
    CONDITION_FALSE,
    DISABLED,
    FIRE,
    GROUP_LOST,
    PAUSED,
    SUPERSEDED_BY,
    Decision,
    RuleOutcome,
    RunFacts,
    exported_outputs,
    fired,
    match,
)
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.model.workflow import Output, Workflow

EVENT = {"kind": "event", "type": "github.pr.merged", "data": {"base": "main", "number": 7}}

TRUE = {"op": "compare", "cmp": "==", "left": {"field": "data.base"}, "right": {"literal": "main"}}
FALSE = {"op": "compare", "cmp": "==", "left": {"field": "data.base"}, "right": {"literal": "dev"}}


def rule(rid: str, **kw) -> Rule:
    base = dict(
        id=rid,
        name=rid,
        trigger=Trigger(kind="event", params={"type": "github.pr.merged"}),
        action=Action(kind="mesh.message"),
    )
    base.update(kw)
    return Rule(**base)


def by_id(decisions) -> dict[str, Decision]:
    return {d.rule_id: d for d in decisions}


# --- criterion 1: every enabled match fires; exclusive group -> highest priority -----------


def test_every_enabled_match_fires_by_default():
    rules = [rule("a"), rule("b", condition=TRUE), rule("c")]
    decisions = match(EVENT, rules)
    assert fired(decisions) == frozenset({"a", "b", "c"})
    assert all(d.fire and d.reason == FIRE for d in decisions)


def test_rules_whose_trigger_does_not_match_are_not_candidates():
    other = rule("x", trigger=Trigger(kind="event", params={"type": "github.issue.opened"}))
    manual = rule("m", trigger=Trigger(kind="manual"))
    decisions = match(EVENT, [rule("a"), other, manual])
    assert [d.rule_id for d in decisions] == ["a"]


def test_condition_false_is_skipped_with_reason():
    d = by_id(match(EVENT, [rule("a", condition=FALSE)]))["a"]
    assert not d.fire
    assert d.reason == CONDITION_FALSE


def test_exclusive_group_only_highest_priority_fires():
    rules = [
        rule("low", exclusive_group="g", priority=1),
        rule("high", exclusive_group="g", priority=5),
        rule("mid", exclusive_group="g", priority=3),
        rule("free"),
    ]
    d = by_id(match(EVENT, rules))
    assert fired(d.values()) == frozenset({"high", "free"})
    assert d["low"].reason == GROUP_LOST
    assert d["low"].by == ("high",)
    assert d["mid"].reason == GROUP_LOST
    assert d["mid"].by == ("high",)


def test_exclusive_group_considers_only_matching_rules():
    rules = [
        rule("high", exclusive_group="g", priority=5, condition=FALSE),
        rule("low", exclusive_group="g", priority=1),
    ]
    d = by_id(match(EVENT, rules))
    assert d["low"].fire
    assert d["high"].reason == CONDITION_FALSE


def test_exclusive_group_tie_is_deterministic_by_rule_id():
    rules = [rule("b", exclusive_group="g"), rule("a", exclusive_group="g")]
    assert fired(match(EVENT, rules)) == frozenset({"a"})
    assert fired(match(EVENT, list(reversed(rules)))) == frozenset({"a"})


# --- criterion 2: supersede ---------------------------------------------------------------


def test_superseded_rule_skipped_when_superseder_matched():
    rules = [rule("A", supersedes=("B",)), rule("B")]
    d = by_id(match(EVENT, rules))
    assert d["A"].fire
    assert not d["B"].fire
    assert d["B"].reason == SUPERSEDED_BY
    assert d["B"].by == ("A",)
    assert d["B"].message == "superseded by A"
    assert d["B"].to_dict()["reason"] == "superseded_by"
    assert d["B"].to_dict()["by"] == ["A"]


def test_superseder_condition_false_lets_superseded_fire():
    rules = [rule("A", supersedes=("B",), condition=FALSE), rule("B")]
    d = by_id(match(EVENT, rules))
    assert d["A"].reason == CONDITION_FALSE
    assert d["B"].fire


def test_disabled_superseder_does_not_supersede():
    rules = [rule("A", supersedes=("B",), enabled=False), rule("B")]
    assert by_id(match(EVENT, rules))["B"].fire


def test_supersede_is_per_event():
    a = rule("A", supersedes=("B",), condition=TRUE)
    b = rule("B")
    other_event = {**EVENT, "data": {"base": "dev"}}
    assert fired(match(EVENT, [a, b])) == frozenset({"A"})
    assert fired(match(other_event, [a, b])) == frozenset({"B"})


def test_supersede_chain_is_transitive():
    rules = [
        rule("A", supersedes=("B",)),
        rule("B", supersedes=("C",), condition=FALSE),
        rule("C"),
    ]
    d = by_id(match(EVENT, rules))
    assert d["A"].fire
    assert d["C"].reason == SUPERSEDED_BY
    assert d["C"].by == ("A",)


def test_supersede_chain_all_matching_names_every_superseder():
    rules = [rule("A", supersedes=("B",)), rule("B", supersedes=("C",)), rule("C")]
    d = by_id(match(EVENT, rules))
    assert fired(d.values()) == frozenset({"A"})
    assert d["B"].by == ("A",)
    assert d["C"].by == ("A", "B")


def test_superseded_rule_does_not_win_its_exclusive_group():
    rules = [
        rule("A", supersedes=("hi",)),
        rule("hi", exclusive_group="g", priority=9),
        rule("lo", exclusive_group="g", priority=1),
    ]
    d = by_id(match(EVENT, rules))
    assert d["hi"].reason == SUPERSEDED_BY
    assert d["lo"].fire


# --- criterion 3: must_after / may_after / exports -----------------------------------------


WF = {
    "wf-up": Workflow(
        id="wf-up",
        name="up",
        outputs=(Output(name="summary", source="vars.s"),),
    )
}


def up_rule() -> Rule:
    return rule("up", workflow=WorkflowRef(id="wf-up"))


def test_must_after_blocks_until_predecessor_succeeded():
    rules = [up_rule(), rule("down", must_after=("up",))]
    for facts in (
        None,
        RunFacts(outcomes={"up": RuleOutcome(status="running")}),
        RunFacts(outcomes={"up": RuleOutcome(status="failed")}),
    ):
        d = by_id(match(EVENT, rules, facts, workflows=WF))["down"]
        assert not d.fire
        assert d.reason == BLOCKED_BY_PREDECESSOR
        assert d.by == ("up",)
    ok = RunFacts(outcomes={"up": RuleOutcome(status="succeeded")})
    assert by_id(match(EVENT, rules, ok, workflows=WF))["down"].fire


def test_downstream_sees_only_explicitly_exported_outputs():
    rules = [up_rule(), rule("down", must_after=("up",)), rule("maybe", may_after=("up",))]
    facts = RunFacts(
        outcomes={"up": RuleOutcome(status="succeeded", outputs={"summary": "ok", "secret": 1})}
    )
    d = by_id(match(EVENT, rules, facts, workflows=WF))
    assert d["down"].upstream == {"up": {"summary": "ok"}}
    assert d["maybe"].upstream == {"up": {"summary": "ok"}}
    # an unrelated rule sees nothing upstream
    assert d["up"].upstream == {}


def test_may_after_never_blocks_and_hides_unfinished_predecessor():
    rules = [up_rule(), rule("maybe", may_after=("up",))]
    facts = RunFacts(outcomes={"up": RuleOutcome(status="running", outputs={"summary": "x"})})
    d = by_id(match(EVENT, rules, facts, workflows=WF))["maybe"]
    assert d.fire
    assert d.upstream == {}


def test_exported_outputs_come_from_the_rules_workflow():
    assert exported_outputs(up_rule(), WF) == frozenset({"summary"})
    assert exported_outputs(rule("plain"), WF) == frozenset()


# --- criterion 4: disabled and global pause -------------------------------------------------


def test_disabled_rule_fires_nothing():
    d = by_id(match(EVENT, [rule("a", enabled=False)]))["a"]
    assert not d.fire
    assert d.reason == DISABLED


def test_global_pause_fires_nothing():
    rules = [rule("a"), rule("b", exclusive_group="g"), rule("c", enabled=False)]
    decisions = match(EVENT, rules, paused=True)
    assert fired(decisions) == frozenset()
    assert {d.reason for d in decisions} == {PAUSED}
    assert len(decisions) == 3


# --- purity (seam obligation) ---------------------------------------------------------------


def test_matching_is_pure_and_deterministic():
    rules = [
        up_rule(),
        rule("A", supersedes=("B",)),
        rule("B"),
        rule("g1", exclusive_group="g", priority=2),
        rule("g2", exclusive_group="g", priority=1),
        rule("down", must_after=("up",)),
        rule("off", enabled=False),
        rule("no", condition=FALSE),
    ]
    facts = RunFacts(outcomes={"up": RuleOutcome(status="succeeded", outputs={"summary": "s"})})
    event = copy.deepcopy(EVENT)
    first = match(event, rules, facts, workflows=WF)
    second = match(event, list(reversed(rules)), facts, workflows=WF)
    assert first == second
    assert event == EVENT
    assert [d.rule_id for d in first] == sorted(d.rule_id for d in first)
    reasons = {d.rule_id: d.reason for d in first}
    assert reasons == {
        "up": FIRE,
        "A": FIRE,
        "B": SUPERSEDED_BY,
        "g1": FIRE,
        "g2": GROUP_LOST,
        "down": FIRE,
        "off": DISABLED,
        "no": CONDITION_FALSE,
    }


def test_decision_to_dict_is_json_ready():
    d = by_id(
        match(EVENT, [rule("a", exclusive_group="g", priority=2), rule("b", exclusive_group="g")])
    )
    assert d["b"].to_dict() == {
        "rule_id": "b",
        "fire": False,
        "reason": "group_lost",
        "by": ["a"],
        "message": "lost exclusive group g to a",
        "upstream": {},
    }


@pytest.mark.parametrize("bad", [{"op": "nope"}, {"op": "compare"}])
def test_malformed_condition_does_not_fire(bad):
    d = by_id(match(EVENT, [rule("a", condition=bad)]))["a"]
    assert not d.fire
    assert d.reason == CONDITION_FALSE
    assert "condition error" in d.message


# --- h78 / c97: supersede composed with must_after / may_after -----------------------------
# These pin what matching does *today*. Supersession is decided on "matched" (trigger and
# condition hold) before predecessor gating, so a superseder that is itself blocked still
# skips the rule it supersedes. See the report on h78 for the spec reading.


def _up_ok() -> RunFacts:
    return RunFacts(outcomes={"X": RuleOutcome(status="succeeded")})


def test_superseder_that_must_run_after_x_fires_once_x_succeeded():
    rules = [rule("X"), rule("A", supersedes=("B",), must_after=("X",)), rule("B")]
    d = by_id(match(EVENT, rules, _up_ok()))
    assert d["A"].fire
    assert d["B"].reason == SUPERSEDED_BY
    assert d["B"].by == ("A",)


def test_blocked_superseder_still_supersedes_so_neither_fires():
    rules = [rule("X"), rule("A", supersedes=("B",), must_after=("X",)), rule("B")]
    for facts in (None, RunFacts(outcomes={"X": RuleOutcome(status="running")})):
        d = by_id(match(EVENT, rules, facts))
        assert d["A"].reason == BLOCKED_BY_PREDECESSOR
        assert d["A"].by == ("X",)
        assert d["B"].reason == SUPERSEDED_BY
        assert d["B"].by == ("A",)
        assert not d["A"].fire
        assert not d["B"].fire


def test_may_after_superseder_fires_and_supersedes_whatever_its_predecessor_did():
    rules = [rule("X"), rule("A", supersedes=("B",), may_after=("X",)), rule("B")]
    facts = RunFacts(outcomes={"X": RuleOutcome(status="failed")})
    d = by_id(match(EVENT, rules, facts))
    assert d["A"].fire
    assert d["A"].upstream == {}
    assert d["B"].reason == SUPERSEDED_BY


def test_superseded_rule_reports_superseded_not_its_own_blocked_predecessor():
    rules = [rule("Y"), rule("A", supersedes=("B",)), rule("B", must_after=("Y",))]
    for facts in (None, RunFacts(outcomes={"Y": RuleOutcome(status="succeeded")})):
        d = by_id(match(EVENT, rules, facts))
        assert d["B"].reason == SUPERSEDED_BY
        assert d["B"].by == ("A",)


def test_superseded_rules_own_predecessors_gate_it_when_the_superseder_does_not_match():
    rules = [rule("Y"), rule("A", supersedes=("B",), condition=FALSE), rule("B", must_after=("Y",))]
    d = by_id(match(EVENT, rules))
    assert d["B"].reason == BLOCKED_BY_PREDECESSOR
    assert d["B"].by == ("Y",)
    ok = RunFacts(outcomes={"Y": RuleOutcome(status="succeeded")})
    assert by_id(match(EVENT, rules, ok))["B"].fire


def test_superseder_that_must_run_after_the_rule_it_supersedes_never_fires():
    # A supersedes B *and* must run after B: B is skipped, so A waits on a run that never
    # comes. Save-time validation does not reject this pairing today.
    rules = [rule("A", supersedes=("B",), must_after=("B",)), rule("B")]
    d = by_id(match(EVENT, rules))
    assert d["B"].reason == SUPERSEDED_BY
    assert d["A"].reason == BLOCKED_BY_PREDECESSOR
    assert d["A"].by == ("B",)
    assert fired(d.values()) == frozenset()
