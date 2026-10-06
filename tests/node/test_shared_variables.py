"""t7: the engine resolves shared variables in conditions and workflow inputs.

* c25 / h17: changing a variable changes the next event's outcome for every rule that
  references it, with no rule edited; ``{"$var": name}`` inputs get the current value;
* c34 / h28: a node that does not advertise the ``variables`` capability never evaluates a
  variable reference as missing - the rule fires nothing and records an error.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from culture_rules.engine.claims import firing_key
from culture_rules.engine.decisions import RULE_DECISIONS, decision_key
from culture_rules.engine.matching import VARIABLE_UNDEFINED, VARIABLES_UNSUPPORTED
from culture_rules.engine.runs import RUNS_COLLECTION, RunError
from culture_rules.engine.variables import VARIABLES_CAPABILITY
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION
from culture_rules.model.action import Action
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.node.firing import RULE_FIRES
from tests.engine.run_helpers import edge, port, step, workflow
from tests.events.fakes import envelope
from tests.node.test_node import BEATS, EVENT_TYPE, Cluster

ADMIN = "alice"


def author_in(var: str, *, negate: bool = False) -> dict:
    """``trigger.source in vars.<var>`` (or its negation)."""
    tree = {"op": "in", "value": {"field": "source"}, "items": {"var": var}}
    return {"op": "not", "arg": tree} if negate else tree


def var_rule(id: str, condition: dict | None = None, **kw) -> Rule:
    return Rule(
        id=id,
        name=id,
        trigger=Trigger(kind="event", params={"type": EVENT_TYPE}),
        condition=condition,
        action=Action(kind="noop"),
        **kw,
    )


def fired(c: Cluster, rule_id: str, event_id: str) -> bool:
    return c.base.get(RULE_FIRES, firing_key(rule_id, event_id)) is not None


# --------------------------------------------------------------------------- c25 / h17


def test_changing_a_variable_changes_the_next_outcome_of_every_referencing_rule():
    c = Cluster("spark")
    c.base.put_variable("trusted", ["agent://someone-else"], updated_by=ADMIN)
    c.define(
        var_rule("allow", author_in("trusted")),
        var_rule("deny", author_in("trusted", negate=True)),
    )
    c.start()

    c.publish(envelope(1))  # source agent://tester is not trusted yet
    c.cycle()
    assert not fired(c, "allow", "evt_1")
    assert fired(c, "deny", "evt_1")

    c.base.put_variable("trusted", ["agent://tester"], updated_by=ADMIN)  # no rule edited
    c.publish(envelope(2))
    c.cycle()
    assert fired(c, "allow", "evt_2")
    assert not fired(c, "deny", "evt_2")
    assert c.base.find(RULE_DECISIONS) == []  # plain condition outcomes, nothing refused


def test_workflow_inputs_mapped_with_var_receive_the_value_current_at_firing_time():
    c = Cluster("spark")
    c.base.put_variable("trusted", ["a", "b"], updated_by=ADMIN)
    wf = workflow(
        (step("s1", inputs=(port("authors"),)),),
        (edge("inputs", "authors", "s1", "authors"),),
        inputs=(port("authors"),),
    )
    c.define(
        wf,
        var_rule("r", workflow=WorkflowRef(id="wf", inputs={"authors": {"$var": "trusted"}})),
    )
    c.start()

    c.publish(envelope(1))
    c.cycle()
    run = c.run("r", "evt_1")
    assert run["inputs"] == {"authors": ["a", "b"]}
    (intent,) = c.base.find(RULE_FIRES, {"rule_id": "r"})
    assert intent["variables"] == {"trusted": ["a", "b"]}

    c.base.put_variable("trusted", ["c"], updated_by=ADMIN)
    c.publish(envelope(2))
    c.cycle()
    assert c.run("r", "evt_2")["inputs"] == {"authors": ["c"]}
    assert c.run("r", "evt_1")["inputs"] == {"authors": ["a", "b"]}  # pinned at its firing


# --------------------------------------------------------------------------- c34 / h28


def test_a_node_advertises_the_variables_capability_on_its_heartbeat():
    c = Cluster("spark")
    c.nodes["spark"].start()
    beat = c.base.get(HEARTBEAT_COLLECTION, "spark")
    assert VARIABLES_CAPABILITY in beat["capabilities"]


def test_a_node_without_the_capability_fires_nothing_and_records_an_error():
    c = Cluster("spark")
    c.nodes["spark"] = c.node("spark", heartbeat_options=replace(BEATS, capabilities=()))
    c.base.put_variable("trusted", ["agent://someone-else"], updated_by=ADMIN)
    c.define(
        var_rule("deny", author_in("trusted", negate=True)),
        var_rule("plain"),  # references no variable: unaffected
    )
    c.start()
    assert VARIABLES_CAPABILITY not in c.base.get(HEARTBEAT_COLLECTION, "spark")["capabilities"]

    c.publish(envelope(1))
    report = c.nodes["spark"].run_once()

    assert report.errors == []
    assert not fired(c, "deny", "evt_1")
    assert c.base.find(RUNS_COLLECTION, {"rule_id": "deny"}) == []
    record = c.base.get(RULE_DECISIONS, decision_key("deny", "evt_1"))
    assert record["reason"] == VARIABLES_UNSUPPORTED
    assert record["fire"] is False
    assert "variables" in record["message"]
    assert record["host"] == "spark"
    assert fired(c, "plain", "evt_1")


def test_an_undefined_variable_at_evaluation_fails_closed_too():
    # Saved before the variable existed (stored directly, bypassing the save-time check).
    c = Cluster("spark")
    c.define(var_rule("deny", author_in("ghost", negate=True)))
    c.start()
    c.publish(envelope(1))
    c.cycle()
    assert not fired(c, "deny", "evt_1")
    record = c.base.get(RULE_DECISIONS, decision_key("deny", "evt_1"))
    assert record["reason"] == VARIABLE_UNDEFINED
    assert "ghost" in record["message"]


def test_an_intent_fired_without_variable_support_is_not_started():
    """An intent for a variable rule that carries no variable snapshot was written by a node
    that did not resolve variables (an older binary): a current node refuses to start it."""
    c = Cluster("spark")
    c.base.put_variable("trusted", ["x"], updated_by=ADMIN)
    c.define(var_rule("deny", author_in("trusted", negate=True)))
    c.start()
    c.base.insert(
        RULE_FIRES,
        {
            "id": firing_key("deny", "evt_9"),
            "rule_id": "deny",
            "event_id": "evt_9",
            "run_id": "run-old",
            "host": "old-node",
            "placed": False,
            "status": "pending",
            "trigger": envelope(9),
            "upstream": {},
        },
    )
    c.cycle()
    intent = c.base.get(RULE_FIRES, firing_key("deny", "evt_9"))
    assert intent["status"] == "failed"
    assert intent["error"] == VARIABLES_UNSUPPORTED
    assert c.base.get(RUNS_COLLECTION, "run-old") is None


# --------------------------------------------------------------------------- review: direct runs


def _var_input_cluster() -> Cluster:
    c = Cluster("spark")
    wf = workflow(
        (step("s1", inputs=(port("authors", required=False),)),),
        (edge("inputs", "authors", "s1", "authors"),),
        inputs=(port("authors", required=False),),
    )
    c.define(
        wf,
        var_rule("r", workflow=WorkflowRef(id="wf", inputs={"authors": {"$var": "trusted"}})),
    )
    return c


def test_a_direct_run_with_an_undefined_var_input_is_refused():
    c = _var_input_cluster()
    with pytest.raises(RunError) as exc:
        c.nodes["spark"].executor.start_from_store("r", trigger=envelope(1), run_id="run-x")
    assert exc.value.code == "variable_undefined"
    assert "trusted" in str(exc.value)
    assert c.base.get(RUNS_COLLECTION, "run-x") is None


def test_a_direct_run_reads_the_current_variable_value():
    c = _var_input_cluster()
    c.base.put_variable("trusted", ["a"], updated_by=ADMIN)
    run = c.nodes["spark"].executor.start_from_store("r", trigger=envelope(1), run_id="run-y")
    assert run["inputs"] == {"authors": ["a"]}
