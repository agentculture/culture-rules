"""d21 phase 1, Codex round 6: each test reproduces a finding on c918008."""

from __future__ import annotations

from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, budget_id
from culture_rules.engine.decisions import RULE_DECISIONS, decision_key
from culture_rules.engine.runs import ACTION_STEP, RUNS_COLLECTION
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger
from culture_rules.ops.backup import RUN_COLLECTIONS
from tests.node.test_node import Cluster
from tests.node.test_run_events import SETTLED, cluster, cycles, settle

KEY = "pr:{trigger.data.repository}#{trigger.data.number}"


def _restore(source: Cluster):
    from culture_rules.ops.reconcile import reconcile_restored

    c = Cluster("spark")
    for coll in ("rules", "workflows", "machines", *RUN_COLLECTIONS):
        for doc in source.base.find(coll):
            c.base.put(coll, doc)
    return c, reconcile_restored(c.base)


def _rule(id, **kw):
    return Rule(
        id=id,
        name=id,
        trigger=Trigger(kind="event", params={"type": SETTLED}),
        action=Action(kind="noop"),
        **kw,
    )


def _runs(c, rule_id):
    return c.base.find(RUNS_COLLECTION, {"rule_id": rule_id})


def _chain():
    # A fails; B must run after A (so B settles predecessor_failed); C may run after B
    return _rule("a"), _rule("b", must_after=("a",)), _rule("c", may_after=("b",))


def _a_fails(c):
    c.actor.on(ACTION_STEP, ("fail", "broken", False))
    settle(c)
    cycles(c, 1)  # a fired and failed; b and c wait; no chain consumer has run yet
    assert _runs(c, "a")[0]["status"] == "failed"


# #1 + run-driven case ----------------------------------------------------------


def test_run_driven_restore_propagates_down_a_three_rule_chain():
    src = cluster(*_chain())
    _a_fails(src)
    assert decision(src, "b")["reason"] == "blocked_by_predecessor"
    c, report = _restore(src)
    assert report.chains_redriven >= 1
    c.start()
    cycles(c, 5)
    assert decision(c, "b")["reason"] == "predecessor_failed"
    assert len(_runs(c, "c")) == 1  # may-after: fires without b
    assert _runs(c, "b") == []


# #2 decision-driven case --------------------------------------------------------


def test_decision_driven_restore_propagates_a_final_skip_to_its_dependant():
    src = cluster(*_chain())
    _a_fails(src)
    chain = src.nodes["spark"].firing.chain_shared
    runs_source = next(s for s in chain.sources if s.collection == RUNS_COLLECTION)
    chain._poll_source(runs_source)  # b settles predecessor_failed; c has not consumed it
    assert decision(src, "b")["reason"] == "predecessor_failed"
    assert decision(src, "c")["reason"] == "blocked_by_predecessor"
    c, report = _restore(src)
    assert report.chains_redriven >= 1
    c.start()
    cycles(c, 5)
    assert len(_runs(c, "c")) == 1


def test_a_decision_without_a_trigger_snapshot_is_reported_for_review_not_guessed():
    src = cluster(*_chain())
    _a_fails(src)
    chain = src.nodes["spark"].firing.chain_shared
    runs_source = next(s for s in chain.sources if s.collection == RUNS_COLLECTION)
    chain._poll_source(runs_source)
    old = dict(decision(src, "b"))
    old.pop("trigger", None)  # written by an older build
    src.base.put(RULE_DECISIONS, old)
    c, report = _restore(src)
    assert report.needs_review == 1
    c.start()
    cycles(c, 5)
    assert _runs(c, "c") == []  # never guessed


# #3 a failed intent's attempt is refunded when its reservation is dropped -------


def test_dropping_a_failed_intents_reservation_refunds_its_attempt():
    src = cluster(_rule("keyed", concurrency_key=KEY, max_attempts=1))
    src.base.put(
        "rule_fires",
        {"id": "intent-x", "rule_id": "keyed", "status": "failed", "run_id": "run-x"},
    )
    src.base.put(
        RULE_ATTEMPT_BUDGETS,
        {
            "id": budget_id("pr:org/repo#7"),
            "key": "pr:org/repo#7",
            "rule_id": "keyed",
            "run_id": "run-x",
            "intent_id": "intent-x",
            "count": 1,
            "counted": True,
            "revision": 1,
        },
    )
    c, report = _restore(src)
    assert report.reservations_dropped == 1
    assert c.base.get(RULE_ATTEMPT_BUDGETS, budget_id("pr:org/repo#7"))["count"] == 0
    c.start()
    settle(c, 2)
    cycles(c, 3)
    assert len(_runs(c, "keyed")) == 1


def test_dropping_a_reservation_of_a_finished_run_keeps_its_attempt():
    src = cluster(_rule("keyed", concurrency_key=KEY, max_attempts=1))
    settle(src)
    cycles(src, 2)
    assert len(_runs(src, "keyed")) == 1
    c, _ = _restore(src)
    assert c.base.get(RULE_ATTEMPT_BUDGETS, budget_id("pr:org/repo#7"))["count"] == 1


def decision(c, rule_id):
    return c.base.get(RULE_DECISIONS, decision_key(rule_id, "evt_1"))
