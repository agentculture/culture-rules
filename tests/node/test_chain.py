"""c6 / h6 / c97 / h78: rule chains on a real node (must run after / may run after).

A downstream rule sees only the outputs its predecessor explicitly exports, and "must run
after" blocks firing until the predecessor's run for the same event succeeded. The node
supplies run facts for the event to matching, and re-evaluates a waiting dependant when its
predecessor finishes (the ``runs`` change feed), exactly once per (rule, event) across
hosts and redelivery. MemoryStore plus fake actors.
"""

from __future__ import annotations

from culture_rules.engine.decisions import RULE_DECISIONS, decision_key
from culture_rules.engine.runs import ACTION_STEP, RUNS_COLLECTION, step_state
from culture_rules.model.action import Action
from culture_rules.model.placement import Placement
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.model.workflow import Output
from culture_rules.node import firing as firing_mod
from culture_rules.node.firing import RULE_FIRES
from tests.engine.run_helpers import port, step, workflow
from tests.events.fakes import envelope
from tests.node.test_node import EVENT_TYPE, Cluster

FLAG_SET = {"op": "compare", "cmp": "==", "left": {"field": "flag"}, "right": {"literal": "on"}}


def producer_workflow():
    """a1 produces n (exported) and hidden (not exported)."""
    return workflow(
        (step("a1", outputs=(port("n", "integer"), port("hidden", "string"))),),
        id="wf-a",
        outputs=(Output(name="n", type="integer", source="steps.a1.outputs.n"),),
    )


def rule(id: str, *, workflow_id: str | None = None, params=None, type=EVENT_TYPE, **kw) -> Rule:
    return Rule(
        id=id,
        name=id,
        trigger=Trigger(kind="event", params={"type": type}),
        workflow=WorkflowRef(id=workflow_id) if workflow_id else None,
        action=Action(kind="noop", params=params or {"event": "trigger.id"}),
        **kw,
    )


def producer(**kw) -> Rule:
    return rule("a", workflow_id="wf-a", **kw)


def consumer(**kw) -> Rule:
    params = {"n": "rules.a.outputs.n", "hidden": "rules.a.outputs.hidden"}
    return rule("b", params=params, **kw)


def cluster(*hosts: str) -> Cluster:
    c = Cluster(*hosts)
    c.actor.default = lambda inp, ctx: {"n": 7, "hidden": "s"} if ctx.step_id == "a1" else {}
    return c


def action_input(c: Cluster, run_id: str) -> dict:
    (call,) = [x for x in c.actor.calls_for(ACTION_STEP) if x[2].run_id == run_id]
    return call[1]


def fires(c: Cluster, rule_id: str) -> list[str]:
    return [d["event_id"] for d in c.base.find(RULE_FIRES, {"rule_id": rule_id})]


def decisions(c: Cluster, rule_id: str) -> list[dict]:
    return c.base.find(RULE_DECISIONS, {"rule_id": rule_id})


def cycles(c: Cluster, n: int, *hosts: str) -> None:
    for _ in range(n):
        c.cycle(*hosts)


# --------------------------------------------------------------------------- must run after


def test_must_after_fires_after_the_predecessor_succeeds_exactly_once_across_hosts():
    c = cluster("spark", "thor")
    c.define(producer_workflow(), producer(), consumer(must_after=("a",)))
    c.start()
    c.publish(envelope(1))

    c.cycle("spark")
    (waiting,) = decisions(c, "b")  # a ran in this cycle: b waits for the next one
    assert waiting["reason"] == "blocked_by_predecessor" and waiting["by"] == ["a"]
    cycles(c, 3)

    a, b = c.run("a", "evt_1"), c.run("b", "evt_1")
    assert a["status"] == "succeeded"
    assert b is not None and b["status"] == "succeeded", b
    assert b["created_at"] >= a["finished_at"]
    # only the explicitly exported output is visible downstream
    assert b["upstream"] == {"a": {"n": 7}}
    assert action_input(c, b["id"]) == {"n": 7, "hidden": None}
    assert fires(c, "b") == ["evt_1"]

    # redelivery: the event again on every subscription, and the runs feed replayed
    c.publish(envelope(1))
    for host in ("spark", "thor"):
        c.base.save_cursor(firing_mod.SHARED_CHAIN, RUNS_COLLECTION, "0")
        cycles(c, 2, host)
    assert fires(c, "b") == ["evt_1"]
    assert [r["id"] for r in c.base.find(RUNS_COLLECTION) if r["rule"]["id"] == "b"] == [b["id"]]
    assert len(c.actor.calls_for(ACTION_STEP)) == 2  # one action for a, one for b

    # the waiting decision is superseded by the eventual outcome, not left stale
    (doc,) = decisions(c, "b")
    assert doc["id"] == decision_key("b", "evt_1")
    assert doc["reason"] == "matched" and doc["fire"] is True
    assert doc["run_id"] == b["id"]
    assert [h["reason"] for h in doc["superseded"]] == ["blocked_by_predecessor"]


def test_must_after_with_a_failed_predecessor_never_fires_and_records_why():
    c = cluster("spark")
    c.actor.on("a1", ("fail", "boom", False))
    c.define(producer_workflow(), producer(), consumer(must_after=("a",)))
    c.start()
    c.publish(envelope(1))
    cycles(c, 4)

    assert c.run("a", "evt_1")["status"] == "failed"
    assert c.run("b", "evt_1") is None and fires(c, "b") == []
    (doc,) = decisions(c, "b")
    assert doc["reason"] == "predecessor_failed" and doc["by"] == ["a"]
    assert doc["fire"] is False
    assert "a failed" in doc["message"]
    assert [h["reason"] for h in doc["superseded"]] == ["blocked_by_predecessor"]


def test_must_after_with_a_predecessor_that_did_not_match_is_skipped_at_once():
    c = cluster("spark")
    c.define(producer_workflow(), producer(type="other"), consumer(must_after=("a",)))
    c.start()
    c.publish(envelope(1))
    cycles(c, 2)

    assert fires(c, "b") == [] and fires(c, "a") == []
    (doc,) = decisions(c, "b")
    assert doc["reason"] == "predecessor_failed" and doc["by"] == ["a"]
    assert "a did not run" in doc["message"]


def test_a_failed_skip_cascades_down_a_must_after_chain():
    c = cluster("spark")
    c.actor.on("a1", ("fail", "boom", False))
    c.define(
        producer_workflow(),
        producer(),
        consumer(must_after=("a",)),
        rule("c", must_after=("b",)),
    )
    c.start()
    c.publish(envelope(1))
    cycles(c, 5)

    assert fires(c, "b") == [] and fires(c, "c") == []
    (doc,) = decisions(c, "c")
    assert doc["reason"] == "predecessor_failed" and doc["by"] == ["b"]


def test_a_must_after_chain_of_three_runs_in_order():
    c = cluster("spark")
    c.define(
        producer_workflow(),
        producer(),
        consumer(must_after=("a",)),
        rule("c", must_after=("b",)),
    )
    c.start()
    c.publish(envelope(1))
    cycles(c, 5)

    a, b, cc = (c.run(r, "evt_1") for r in ("a", "b", "c"))
    assert a["status"] == b["status"] == cc["status"] == "succeeded"
    assert a["finished_at"] <= b["created_at"] and b["finished_at"] <= cc["created_at"]
    assert cc["upstream"] == {"b": {}}  # b exports nothing


# --------------------------------------------------------------------------- may run after


def test_may_after_waits_for_a_matching_predecessor_and_sees_its_exports():
    c = cluster("spark")
    c.define(producer_workflow(), producer(), consumer(may_after=("a",)))
    c.start()
    c.publish(envelope(1))

    c.cycle()
    assert c.run("b", "evt_1") is None  # a matched this event: b runs after it
    cycles(c, 3)

    a, b = c.run("a", "evt_1"), c.run("b", "evt_1")
    assert b["status"] == "succeeded" and b["created_at"] >= a["finished_at"]
    assert b["upstream"] == {"a": {"n": 7}}
    assert action_input(c, b["id"])["n"] == 7
    assert fires(c, "b") == ["evt_1"]


def test_may_after_runs_after_a_failed_predecessor_without_its_outputs():
    c = cluster("spark")
    c.actor.on("a1", ("fail", "boom", False))
    c.define(producer_workflow(), producer(), consumer(may_after=("a",)))
    c.start()
    c.publish(envelope(1))
    cycles(c, 4)

    b = c.run("b", "evt_1")
    assert c.run("a", "evt_1")["status"] == "failed"
    assert b["status"] == "succeeded" and b["upstream"] == {}
    assert action_input(c, b["id"])["n"] is None


def test_may_after_fires_at_once_when_the_predecessor_does_not_match():
    c = cluster("spark")
    c.define(producer_workflow(), producer(condition=FLAG_SET), consumer(may_after=("a",)))
    c.start()
    c.publish(envelope(1))

    c.cycle()
    b = c.run("b", "evt_1")
    assert b is not None and b["status"] == "succeeded"
    assert b["upstream"] == {} and fires(c, "a") == []
    assert decisions(c, "b") == []


# --------------------------------------------------------------------------- placement


def test_a_placed_dependant_is_re_evaluated_on_its_host_only():
    c = cluster("spark", "thor")
    c.define(
        producer_workflow(),
        producer(placement=Placement(machine="spark")),
        consumer(must_after=("a",), placement=Placement(machine="thor")),
    )
    c.start()
    c.publish(envelope(1))

    c.cycle("thor")  # thor evaluates b: a has not run yet, so b waits
    assert c.run("b", "evt_1") is None
    cycles(c, 3, "spark")  # a runs on spark; spark never evaluates b
    assert c.run("a", "evt_1")["status"] == "succeeded"
    assert c.run("b", "evt_1") is None and fires(c, "b") == []

    cycles(c, 2, "thor")
    b = c.run("b", "evt_1")
    assert b["status"] == "succeeded" and b["started_by"] == "engine@thor"
    assert b["upstream"] == {"a": {"n": 7}}
    assert {h for h, r, _ in c.evaluations if r == "b"} == {"thor"}
    assert fires(c, "b") == ["evt_1"]


# --------------------------------------------------------------------------- h78


def test_supersede_composes_with_must_after_without_changing_either():
    c = cluster("spark")
    c.define(
        producer_workflow(),
        producer(),
        consumer(must_after=("a",)),
        rule("s", supersedes=("b",), condition=FLAG_SET),
    )
    c.start()
    c.publish(envelope(1, flag="on"))  # s matches: b is superseded, whatever a does
    c.publish(envelope(2))  # s's condition is false: b runs after a
    cycles(c, 5)

    assert c.run("a", "evt_1")["status"] == "succeeded"
    assert c.run("b", "evt_1") is None
    sup = c.base.get(RULE_DECISIONS, decision_key("b", "evt_1"))
    assert sup["reason"] == "superseded_by" and sup["by"] == ["s"]
    assert "superseded" not in sup  # final from the start: nothing superseded it

    b2 = c.run("b", "evt_2")
    assert b2["status"] == "succeeded" and b2["upstream"] == {"a": {"n": 7}}
    assert c.run("s", "evt_2") is None
    assert sorted(fires(c, "b")) == ["evt_2"]


def test_a_rule_history_shows_the_run_not_a_fired_decision():
    c = cluster("spark")
    c.define(producer_workflow(), producer(), consumer(must_after=("a",)))
    c.start()
    c.publish(envelope(1))
    cycles(c, 3)
    b = c.run("b", "evt_1")
    assert step_state(b, ACTION_STEP)["status"] == "succeeded"

    from culture_rules.engine.decisions import decisions_for

    assert [d["reason"] for d in decisions_for(c.base, "b")] == ["matched"]
    assert decisions_for(c.base, "b", skips_only=True) == []


def test_a_may_after_cycle_does_not_deadlock():
    c = cluster("spark")
    c.define(rule("a", may_after=("b",)), rule("b", may_after=("a",)))
    c.start()
    c.publish(envelope(1))
    cycles(c, 4)

    a, b = c.run("a", "evt_1"), c.run("b", "evt_1")
    assert a["status"] == b["status"] == "succeeded"
    assert b["created_at"] >= a["finished_at"]  # the cycle is broken in decision order
    assert fires(c, "a") == fires(c, "b") == ["evt_1"]


def test_a_predecessor_whose_run_cannot_start_settles_its_dependants():
    c = cluster("spark")
    c.define(producer(), consumer(must_after=("a",)), rule("m", may_after=("a",)))  # no wf-a
    c.start()
    c.publish(envelope(1))
    cycles(c, 3)

    (intent,) = c.base.find(RULE_FIRES, {"rule_id": "a"})
    assert intent["status"] == "failed" and c.run("a", "evt_1") is None
    (doc,) = decisions(c, "b")
    assert doc["reason"] == "predecessor_failed" and "a could not start" in doc["message"]
    m = c.run("m", "evt_1")
    assert m["status"] == "succeeded" and m["upstream"] == {}
