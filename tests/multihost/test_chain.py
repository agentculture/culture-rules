"""c6 / h6: a must-run-after chain A -> B across hosts fires B exactly once per event.

A is placed on spark, B on thor. B is evaluated on thor only, waits for A's run for the
same event to succeed (on spark), then fires once, seeing only A's exported outputs. A
second variant leaves both rules unplaced across three hosts and several events.
"""

from __future__ import annotations

from culture_rules.engine.runs import ACTION_STEP, step_state
from culture_rules.model.action import Action
from culture_rules.model.placement import Placement
from culture_rules.model.rule import Rule
from culture_rules.model.workflow import Output
from tests.engine.run_helpers import port, step, workflow
from tests.events.fakes import envelope
from tests.multihost.harness import HOSTS, event_rule


def producer_workflow():
    return workflow(
        (step("s1", outputs=(port("n", "integer"),)),),
        id="wf-a",
        outputs=(Output(name="n", type="integer", source="steps.s1.outputs.n"),),
    )


def chain(placed: bool) -> tuple[Rule, Rule]:
    a = event_rule("a", "wf-a", Placement(machine="spark") if placed else None)
    b = event_rule("b", None, Placement(machine="thor") if placed else None)
    b = Rule.from_dict(
        {
            **b.to_dict(),
            "must_after": ["a"],
            "action": Action(kind="noop", params={"n": "rules.a.outputs.n"}).to_dict(),
        }
    )
    return a, b


def test_a_placed_chain_across_hosts_fires_the_dependant_once_on_its_host(cluster):
    cluster.define(producer_workflow(), *chain(placed=True))
    cluster.start(*HOSTS)
    cluster.publish(envelope(1))

    b = cluster.wait_run("b", "evt_1", timeout=30)
    a = cluster.run_for("a", "evt_1")

    assert a["status"] == "succeeded" and a["started_by"] == "engine@spark"
    assert b["status"] == "succeeded", b.get("error")
    assert b["started_by"] == "engine@thor"
    assert b["created_at"] >= a["finished_at"]
    assert b["upstream"] == {"a": {"n": 2}}
    assert step_state(b, ACTION_STEP)["inputs"] == {"n": 2}
    assert set(cluster.evaluations("b")) == {"thor"}
    cluster.settle(seconds=1.0)
    assert cluster.fires("b") == ["evt_1"]


def test_an_unplaced_chain_fires_each_dependant_once_per_event(cluster):
    cluster.define(producer_workflow(), *chain(placed=False))
    cluster.start(*HOSTS)
    for n in range(4):
        cluster.publish(envelope(n))

    runs = [cluster.wait_run("b", f"evt_{n}", timeout=30) for n in range(4)]

    assert all(r["status"] == "succeeded" and r["upstream"] == {"a": {"n": 2}} for r in runs)
    cluster.settle(seconds=1.0)
    assert sorted(cluster.fires("b")) == [f"evt_{n}" for n in range(4)]
    assert sorted(cluster.fires("a")) == [f"evt_{n}" for n in range(4)]
