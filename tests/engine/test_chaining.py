"""Pure unit tests of :mod:`culture_rules.engine.chaining` (#7)."""

from __future__ import annotations

from culture_rules.engine.chaining import sequence
from culture_rules.engine.matching import (
    BLOCKED_BY_PREDECESSOR,
    PREDECESSOR_FAILED,
    Decision,
)
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger


def rule(rid: str, **kw) -> Rule:
    return Rule(
        id=rid,
        name=rid,
        trigger=Trigger(kind="event", params={"type": "t"}),
        action=Action(kind="noop", params={}),
        **kw,
    )


def waiting(rid: str, *by: str) -> Decision:
    return Decision(rule_id=rid, fire=False, reason=BLOCKED_BY_PREDECESSOR, by=by)


def test_three_deep_must_after_chain_with_a_failed_root_skips_the_leaf():
    rules = [rule("a"), rule("b", must_after=("a",)), rule("c", must_after=("b",))]
    decisions = [
        Decision(rule_id="a", fire=True, reason="fire"),
        waiting("b", "a"),
        waiting("c", "b"),
    ]
    out = {d.rule_id: d for d in sequence(decisions, rules, {"a": "failed"})}
    assert out["b"].reason == PREDECESSOR_FAILED
    assert out["b"].by == ("a",)
    assert out["c"].fire is False
    assert out["c"].reason == PREDECESSOR_FAILED
    assert out["c"].by == ("b",)
