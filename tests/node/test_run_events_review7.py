"""d21 phase 1, Codex confirmation on dfc2017: an unrecoverable chain continuation."""

from __future__ import annotations

from culture_rules.engine.decisions import RULE_DECISIONS, decision_key
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.events.triggers import FIRES_COLLECTION
from tests.node.test_run_events import cycles
from tests.node.test_run_events_review6 import _chain, _restore, _runs, cluster

BIG = "x" * 70_000  # over the 64 KiB decision trigger snapshot


def _big_chain_restore(drop_a_run: bool = False):
    src = cluster(*_chain())
    src.actor.on("@action", ("fail", "broken", False))
    from tests.events.fakes import envelope
    from tests.node.test_run_events import PR, SETTLED

    src.publish(envelope(1, type=SETTLED, data={**PR, "big": BIG}))
    cycles(src, 1)
    assert _runs(src, "a")[0]["status"] == "failed"
    if drop_a_run:
        # no durable reference left: the run (and its intent) of the predecessor are gone
        for run in _runs(src, "a"):
            src.base.delete(RUNS_COLLECTION, run["id"])
        for intent in src.base.find("rule_fires", {"rule_id": "a"}):
            src.base.delete("rule_fires", intent["id"])
    return src


def decision(c, rule_id):
    return c.base.get(RULE_DECISIONS, decision_key(rule_id, "evt_1"))


def test_an_oversized_trigger_is_recovered_from_the_predecessors_run():
    src = _big_chain_restore()
    c, report = _restore(src)
    c.start()
    cycles(c, 5)
    b = decision(c, "b")
    assert b["reason"] == "predecessor_failed" and b.get("trigger_omitted") is True
    assert len(_runs(c, "c")) == 1  # c continued from the envelope a's run stored
    assert c.base.find("chain_needs_review") == []


def test_an_unrecoverable_continuation_is_recorded_and_not_marked_handled():
    from culture_rules.engine.decisions import settle_decision
    from culture_rules.engine.matching import PREDECESSOR_FAILED, Decision

    src = _big_chain_restore()
    # b settles predecessor_failed without a snapshot, and nothing durable names the event
    with src.base.transaction() as tx:
        settle_decision(
            tx,
            Decision(rule_id="b", fire=False, reason=PREDECESSOR_FAILED, detail="a failed"),
            event_id="evt_1",
            host="spark",
            at="2026-10-03T12:00:00+00:00",
            trigger={"id": "evt_1", "data": {"big": BIG}},
        )
    for run in _runs(src, "a"):
        src.base.delete(RUNS_COLLECTION, run["id"])
    for intent in src.base.find("rule_fires", {"rule_id": "a"}):
        src.base.delete("rule_fires", intent["id"])
    c, report = _restore(src)
    assert report.needs_review == 1  # surfaced at restore already
    c.start()
    cycles(c, 2)
    # an operator re-drives it anyway (touches the decision): the node cannot recover it
    b = decision(c, "b")
    c.base.update_if(RULE_DECISIONS, b["id"], {"reason": b["reason"]}, {"touched": True})
    cycles(c, 3)
    assert _runs(c, "c") == []
    (review,) = c.base.find("chain_needs_review")
    assert (review["rule_id"], review["event_id"]) == ("b", "evt_1")
    assert review["dependants"] == ["c"]
    # not marked handled: no chain marker for b's decision
    marker = f"chain/{RULE_DECISIONS}/{decision_key('b', 'evt_1')}"
    assert c.base.get(FIRES_COLLECTION, marker) is None
    from culture_rules.ops.health import health_status

    health = health_status(c.base, c.clock(), "spark")
    assert health["chain_needs_review"] == 1
    # the operator restores the event and touches the decision again: unhandled, so retried
    from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
    from tests.events.fakes import envelope
    from tests.node.test_run_events import PR, SETTLED

    original = envelope(1, type=SETTLED, data={**PR, "big": BIG})
    c.base.put(EVENTS_COLLECTION, event_document(original, host="operator"))
    b = decision(c, "b")
    c.base.update_if(RULE_DECISIONS, b["id"], {"reason": b["reason"]}, {"touched": 2})
    cycles(c, 4)
    assert len(_runs(c, "c")) == 1
    # the successful continuation resolved its review record
    assert c.base.find("chain_needs_review") == []
    assert health_status(c.base, c.clock(), "spark")["chain_needs_review"] == 0


def test_reconcile_recovers_a_snapshotless_decision_from_its_predecessors_run():
    from culture_rules.engine.decisions import settle_decision
    from culture_rules.engine.matching import PREDECESSOR_FAILED, Decision

    src = _big_chain_restore()
    with src.base.transaction() as tx:
        settle_decision(
            tx,
            Decision(rule_id="b", fire=False, reason=PREDECESSOR_FAILED, detail="a failed"),
            event_id="evt_1",
            host="spark",
            at="2026-10-03T12:00:00+00:00",
            trigger={"id": "evt_1", "data": {"big": BIG}},
        )
    c, report = _restore(src)
    assert report.needs_review == 0 and report.chains_redriven >= 1
    c.start()
    cycles(c, 5)
    assert len(_runs(c, "c")) == 1


def test_recovery_walks_transitive_predecessors_down_a_four_rule_chain():
    from tests.events.fakes import envelope
    from tests.node.test_run_events import PR, SETTLED
    from tests.node.test_run_events_review6 import _rule

    # a fails; b must after a, c must after b (both settle predecessor_failed on an
    # oversized trigger, so neither keeps a snapshot); d may after c
    rules = (
        _rule("a"),
        _rule("b", must_after=("a",)),
        _rule("c", must_after=("b",)),
        _rule("d", may_after=("c",)),
    )
    src = cluster(*rules)
    src.actor.on("@action", ("fail", "broken", False))
    src.publish(envelope(1, type=SETTLED, data={**PR, "big": BIG}))
    cycles(src, 1)
    c, report = _restore(src)
    c.start()
    cycles(c, 6)
    assert decision(c, "c")["reason"] == "predecessor_failed"
    assert len(_runs(c, "d")) == 1
    assert c.base.find("chain_needs_review") == []
