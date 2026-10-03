"""Rule chains across a global engine pause (c88 / h175 with c6 / c97).

A dependant waiting for its predecessor (``blocked_by_predecessor``) must not be finalized
as a ``paused`` skip when the predecessor settles while the engine is paused: the chain
re-evaluation is deferred (its change-feed cursor stays before the settle) and runs once the
pause lifts, so the dependant fires exactly once - also across a node restart during the
pause. A *new* trigger event that arrives while paused is still dropped, by design (h175:
"after global pause, a matching event fires nothing").
"""

from __future__ import annotations

from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.claims import idempotency_key
from culture_rules.engine.decisions import RULE_DECISIONS, decision_key, settle_decision
from culture_rules.engine.matching import BLOCKED_BY_PREDECESSOR, PAUSED, Decision
from culture_rules.engine.runs import ACTION_STEP, RUNS_COLLECTION, Containment
from culture_rules.events.triggers import FIRES_COLLECTION
from culture_rules.store.memory import MemoryStore
from tests.events.fakes import envelope
from tests.node.test_chain import (
    cluster,
    consumer,
    cycles,
    decisions,
    fires,
    producer,
    producer_workflow,
)

OPS = "ops@test"


def _waiting_dependant(**chain):
    """b waits on a (whose step a1 is accepted, long-running) for evt_1."""
    c = cluster("spark")
    c.actor.on("a1", ("accept",))
    c.define(producer_workflow(), producer(), consumer(**chain))
    c.start()
    c.publish(envelope(1))
    cycles(c, 2)
    (waiting,) = decisions(c, "b")
    assert waiting["reason"] == "blocked_by_predecessor"
    return c, Containment(c.base, clock=c.clock)


def _restart(c, host: str = "spark") -> None:
    c.nodes[host].stop()
    c.nodes[host] = c.node(host)
    c.nodes[host].start()


def _assert_fired_once_after_waiting(c) -> None:
    assert fires(c, "b") == ["evt_1"]
    b_runs = [r for r in c.base.find(RUNS_COLLECTION) if r["rule"]["id"] == "b"]
    assert len(b_runs) == 1
    assert b_runs[0]["status"] == "succeeded"
    (doc,) = decisions(c, "b")
    assert doc["reason"] == "matched"
    assert doc["fire"] is True
    assert [h["reason"] for h in doc["superseded"]] == ["blocked_by_predecessor"]


def _may_after_settles_while_paused(*, restart: bool):
    c, ops = _waiting_dependant(may_after=("a",))
    ops.pause(OPS)
    ops.cancel(c.run("a", "evt_1")["id"], OPS)  # the predecessor settles during the pause
    cycles(c, 3)
    (still,) = decisions(c, "b")
    assert still["reason"] == "blocked_by_predecessor"  # not finalized as a 'paused' skip
    assert fires(c, "b") == []
    if restart:
        _restart(c)
        cycles(c, 2)
        assert fires(c, "b") == []
    ops.resume(OPS)
    cycles(c, 4)
    return c


def test_may_after_dependant_waits_out_a_pause_and_fires_once_on_resume():
    c = _may_after_settles_while_paused(restart=False)
    _assert_fired_once_after_waiting(c)
    assert c.run("a", "evt_1")["status"] == "cancelled"


def test_may_after_dependant_survives_a_node_restart_during_the_pause():
    c = _may_after_settles_while_paused(restart=True)
    _assert_fired_once_after_waiting(c)


def _must_after_succeeds_while_paused(*, restart: bool):
    c = cluster("spark")
    c.actor.on(ACTION_STEP, ("accept",))  # a's action is long-running (b's completes)
    c.define(producer_workflow(), producer(), consumer(must_after=("a",)))
    c.start()
    c.publish(envelope(1))
    cycles(c, 2)
    (waiting,) = decisions(c, "b")
    assert waiting["reason"] == "blocked_by_predecessor"
    ops = Containment(c.base, clock=c.clock)
    ops.pause(OPS)
    a_run = c.run("a", "evt_1")["id"]
    assert c.nodes["spark"].deliver(
        idempotency_key(a_run, ACTION_STEP), InvocationResult.completed({})
    )
    cycles(c, 3)
    assert c.run("a", "evt_1")["status"] == "succeeded"  # settled during the pause
    (still,) = decisions(c, "b")
    assert still["reason"] == "blocked_by_predecessor"
    if restart:
        _restart(c)
        cycles(c, 2)
    assert fires(c, "b") == []
    ops.resume(OPS)
    cycles(c, 4)
    return c


def test_must_after_dependant_fires_once_after_a_pause_with_the_predecessor_outputs():
    c = _must_after_succeeds_while_paused(restart=False)
    _assert_fired_once_after_waiting(c)
    assert c.run("b", "evt_1")["upstream"] == {"a": {"n": 7}}


def test_must_after_dependant_survives_a_node_restart_during_the_pause():
    c = _must_after_succeeds_while_paused(restart=True)
    _assert_fired_once_after_waiting(c)


def test_the_deferred_chain_reevaluation_is_reported_while_paused():
    c, ops = _waiting_dependant(may_after=("a",))
    ops.pause(OPS)
    ops.cancel(c.run("a", "evt_1")["id"], OPS)
    report = c.cycle()["spark"]
    assert [d["rule"] for d in report.deferred] == ["b"]
    assert all("paused" in d["reason"] for d in report.deferred)


def test_an_event_arriving_while_paused_is_dropped_by_design():
    """h175: a matching event during a global pause fires nothing, also after resume."""
    c = cluster("spark")
    c.define(producer_workflow(), producer())
    c.start()
    ops = Containment(c.base, clock=c.clock)
    ops.pause(OPS)
    c.publish(envelope(2))
    cycles(c, 2)
    markers = sorted(m["id"] for m in c.base.find(FIRES_COLLECTION) if m["id"].endswith("evt_2"))
    assert markers == ["triggers/evt_2", "triggers@spark/evt_2"]  # consumed, not deferred
    ops.resume(OPS)
    cycles(c, 3)
    assert fires(c, "a") == []
    assert c.run("a", "evt_2") is None


def test_a_paused_decision_never_finalizes_a_waiting_record():
    store = MemoryStore()
    waiting = Decision(rule_id="b", fire=False, reason=BLOCKED_BY_PREDECESSOR, by=("a",))
    paused = Decision(rule_id="b", fire=False, reason=PAUSED)
    with store.transaction() as tx:
        settle_decision(tx, waiting, event_id="evt_1", host="spark", at="t0")
    with store.transaction() as tx:
        settle_decision(tx, paused, event_id="evt_1", host="spark", at="t1", always=True)
    doc = store.get(RULE_DECISIONS, decision_key("b", "evt_1"))
    assert doc["reason"] == BLOCKED_BY_PREDECESSOR
    assert doc["by"] == ["a"]
    assert "superseded" not in doc
