"""Criterion 1 (c10/h10): a rule placed on node B evaluates only on B, and a
spark -> thor -> spark2 workflow runs each step on its own host.

Three simulated hosts (spark, thor, spark2) - each its own Executor, event ingest,
triggers and heartbeat in its own thread, with its own store handle - share one store.
The event reaches every host through its own (fake) events-cli subscription.
"""

from __future__ import annotations

from culture_rules.engine.runs import ACTION_STEP, step_state
from culture_rules.model.placement import Placement
from tests.events.fakes import envelope
from tests.multihost.harness import HOSTS, event_rule, three_host_workflow


def test_rule_placed_on_thor_evaluates_only_on_thor_and_each_step_runs_on_its_host(cluster):
    cluster.define(three_host_workflow(), event_rule("on-thor", "wf", Placement(machine="thor")))
    cluster.start(*HOSTS)
    cluster.publish(envelope(1))

    run = cluster.wait_run("on-thor", "evt_1", timeout=30)

    assert run["status"] == "succeeded", run.get("error")
    assert cluster.evaluations("on-thor") == {"thor": ["evt_1"]}
    assert run["started_by"] == "engine@thor"
    assert [step_state(run, s)["host"] for s in ("s1", "s2", "s3")] == HOSTS
    assert cluster.ledger.hosts_by_step(run["id"]) == {
        "s1": ["spark"],
        "s2": ["thor"],
        "s3": ["spark2"],
        ACTION_STEP: [step_state(run, ACTION_STEP)["host"]],
    }
    assert run["outputs"] == {"msg": "got 3.0"}  # typed values crossed all three hosts
    assert cluster.fires("on-thor") == ["evt_1"]  # fired exactly once


def test_unplaced_rule_is_evaluated_once_per_event_by_some_host(cluster):
    cluster.define(event_rule("anywhere"))
    cluster.start(*HOSTS)
    for n in range(5):
        cluster.publish(envelope(n))

    runs = [cluster.wait_run("anywhere", f"evt_{n}", timeout=30) for n in range(5)]

    assert all(r["status"] == "succeeded" for r in runs)
    evaluated = cluster.evaluations("anywhere")
    assert sorted(e for events in evaluated.values() for e in events) == [
        f"evt_{n}" for n in range(5)
    ]
    assert sorted(cluster.fires("anywhere")) == [f"evt_{n}" for n in range(5)]


def test_rule_placed_on_a_stopped_host_waits_for_it_and_never_runs_elsewhere(cluster):
    cluster.define(event_rule("on-thor", placement=Placement(machine="thor")))
    cluster.start(*HOSTS)
    cluster.stop("thor")
    cluster.publish(envelope(7))

    cluster.settle(seconds=2.0)  # spark and spark2 see the event and must not evaluate it
    assert cluster.evaluations("on-thor") == {}
    assert cluster.run_for("on-thor", "evt_7") is None

    cluster.start("thor")  # restart: thor's trigger cursor resumes where it stopped
    run = cluster.wait_run("on-thor", "evt_7", timeout=30)
    assert run["status"] == "succeeded"
    assert cluster.evaluations("on-thor") == {"thor": ["evt_7"]}
    assert run["started_by"] == "engine@thor"
