"""d21 phase 2 (D): an action marked ``only_at_chain_end`` runs only where a chain ends.

A chain of rules (fix -> review -> publish) must post one comment, from the stage that ends
it for good. A rule action (or ``on_failure``) with ``only_at_chain_end: true`` is skipped
(``chain_continues``) when a live rule would continue the run's chain - fire on the event the
run is about to emit, on the same concurrency key - and runs otherwise.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from culture_rules.engine.runs import ACTION_STEP, FAILURE_STEP, step_state
from culture_rules.model.action import Action
from culture_rules.model.describe import describe_rule, render
from culture_rules.model.rule import Rule
from culture_rules.model.validate import validate
from culture_rules.node.run_events import run_event_id
from tests.node.test_run_events import FAILED, KEY, cluster, cycles
from tests.node.test_run_events import fix_rule as _fix_rule
from tests.node.test_run_events import follow_rule as _follow_rule
from tests.node.test_run_events import rule_is, settle


def _with(rule, action=None, on_failure=None):
    changes = {}
    if action is not None:
        changes["action"] = action
    if on_failure is not None:
        changes["on_failure"] = on_failure
    return replace(rule, **changes)


def fix_rule(*, action=None, on_failure=None, **kw):
    return _with(_fix_rule(**kw), action, on_failure)


def follow_rule(*, action=None, on_failure=None, **kw):
    return _with(_follow_rule(**kw), action, on_failure)


COMMENT = Action(kind="noop", params={"say": "done"}, only_at_chain_end=True)


def review(**kw):
    return follow_rule(
        condition=rule_is("fix"), concurrency_key=KEY, counts_toward_budget=False, **kw
    )


def action_calls(c, run_id):
    return [
        call
        for call in c.actor.calls
        if call[2].run_id == run_id
        and call[2].step_id
        in (
            ACTION_STEP,
            FAILURE_STEP,
        )
    ]


def test_a_continued_run_skips_its_chain_end_action_and_the_chain_end_runs_its_own():
    c = cluster(fix_rule(concurrency_key=KEY, action=COMMENT), review(action=COMMENT))
    settle(c)
    cycles(c, 4)
    fix = c.run("fix", "evt_1")
    assert fix["status"] == "succeeded"
    st = step_state(fix, ACTION_STEP)
    assert st["status"] == "skipped"
    assert st["outputs"] == {"chain_continues": ["review"]}
    assert action_calls(c, fix["id"]) == []
    rev = c.run("review", run_event_id(fix["id"]))
    assert rev["status"] == "succeeded"
    assert step_state(rev, ACTION_STEP)["status"] == "succeeded"  # nothing continues it
    assert len(action_calls(c, rev["id"])) == 1


def test_with_the_continuation_disabled_the_run_posts_its_own_action():
    c = cluster(fix_rule(concurrency_key=KEY, action=COMMENT), review(enabled=False))
    settle(c)
    cycles(c, 4)
    fix = c.run("fix", "evt_1")
    assert step_state(fix, ACTION_STEP)["status"] == "succeeded"
    assert len(action_calls(c, fix["id"])) == 1


def test_an_unflagged_action_always_runs():
    c = cluster(fix_rule(concurrency_key=KEY), review())
    settle(c)
    cycles(c, 4)
    fix = c.run("fix", "evt_1")
    assert step_state(fix, ACTION_STEP)["status"] == "succeeded"


def test_on_failure_is_skipped_only_when_a_rule_continues_the_failed_run():
    on_fail = Action(kind="noop", params={"say": "handed back"}, only_at_chain_end=True)
    c = cluster(fix_rule(concurrency_key=KEY, on_failure=on_fail), review())
    c.actor.on("s1", ("fail", "broken", False))
    settle(c)
    cycles(c, 4)
    fix = c.run("fix", "evt_1")
    assert fix["status"] == "failed"
    assert step_state(fix, FAILURE_STEP)["status"] == "succeeded"  # nothing on .failed
    c2 = cluster(
        fix_rule(concurrency_key=KEY, on_failure=on_fail),
        review(type=FAILED),
    )
    c2.actor.on("s1", ("fail", "broken", False))
    settle(c2)
    cycles(c2, 4)
    fix = c2.run("fix", "evt_1")
    assert fix["status"] == "failed"
    assert fix["error"]["message"] == "broken"
    assert step_state(fix, FAILURE_STEP)["status"] == "skipped"
    assert c2.run("review", run_event_id(fix["id"])) is not None


def test_a_continuation_on_another_key_does_not_count():
    c = cluster(
        fix_rule(concurrency_key=KEY, action=COMMENT),
        follow_rule(condition=rule_is("fix"), concurrency_key="other:{trigger.data.number}"),
    )
    settle(c)
    cycles(c, 4)
    fix = c.run("fix", "evt_1")
    assert step_state(fix, ACTION_STEP)["status"] == "succeeded"


def test_the_flag_round_trips_validates_and_reads_in_the_description():
    rule = fix_rule(concurrency_key=KEY, action=COMMENT)
    again = Rule.from_dict(rule.to_dict())
    assert again.action.only_at_chain_end is True
    assert validate(again) == []
    assert Action(kind="noop").only_at_chain_end is False
    lines = render(describe_rule(again))
    assert "Then noop (only where its chain ends)" in lines, lines


def test_a_non_boolean_flag_is_refused():
    doc = fix_rule(concurrency_key=KEY, action=COMMENT).to_dict()
    doc["action"]["only_at_chain_end"] = "yes"
    with pytest.raises(ValueError):
        Rule.from_dict(doc)
