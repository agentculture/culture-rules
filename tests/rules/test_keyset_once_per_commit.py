"""A keyset pair configured on repository@head_sha posts once per commit (gpm t5, c11/h15).

No new code: the workflow is a fixture built here (never committed) from the generic
keyset.filter and keyset.add steps. The step's ``config.with`` builds the one item from
the trigger's repository and head sha through ``{{ }}`` templates, and ``key_field`` names
its ``key``. Two identical ``checks_settled`` successes: the first keeps the item, the
second's filter drops it (kept 0). Synthetic data only.
"""

from __future__ import annotations

from culture_rules import keysets
from culture_rules.engine.runs import RUNS_COLLECTION, step_state
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.node.actions.keyset import (
    KEYSET_ADD_BUILTIN,
    KEYSET_FILTER_BUILTIN,
    KeysetAddPort,
    KeysetFilterPort,
)
from culture_rules.node.runner import BuiltinCodePort
from tests.events.fakes import envelope
from tests.node.test_node import Cluster

SETTLED = "github.pr.checks_settled"
SET = "green-pr-once"
SHA = "a" * 40
KEYSET = {"set": SET, "key_field": "key", "normalise": {}, "window_days": 30}
ITEM = {"items": [{"key": "{{inputs.repository}}@{{inputs.head_sha}}"}]}


def _port(name, typ):
    return {"name": name, "type": typ, "required": True, "description": ""}


def _step(step_id, builtin, inputs, outputs, **config):
    return {
        "id": step_id,
        "name": step_id,
        "kind": "code",
        "inputs": inputs,
        "outputs": outputs,
        "config": {"builtin": builtin, **KEYSET, "with": ITEM, **config},
    }


def _edge(target, port):
    return {"source": "inputs", "source_port": port, "target": target, "target_port": port}


def workflow() -> dict:
    ins = [_port("repository", "string"), _port("head_sha", "string")]
    return {
        "id": "once-per-commit",
        "name": "Once per commit",
        "inputs": ins,
        "steps": [
            _step(
                "seen",
                KEYSET_FILTER_BUILTIN,
                ins,
                [_port("kept", "integer"), _port("dropped", "integer")],
            ),
            _step(
                "remember",
                KEYSET_ADD_BUILTIN,
                ins,
                [_port("added", "integer")],
                max_keys=10,
            ),
        ],
        "edges": [_edge(s, p) for s in ("seen", "remember") for p in ("repository", "head_sha")],
        "outputs": [{"name": "kept", "source": "steps.seen.outputs.kept", "type": "integer"}],
    }


def rule() -> Rule:
    return Rule(
        id="green-once",
        name="green once",
        trigger=Trigger(kind="event", params={"type": SETTLED}),
        workflow=WorkflowRef(
            id="once-per-commit",
            inputs={
                "repository": "trigger.data.repository",
                "head_sha": "trigger.data.head_sha",
            },
        ),
        action={"kind": "noop", "params": {}},
    )


def _run_two_successes():
    c = Cluster("spark")
    ports = {
        "code": BuiltinCodePort(
            {
                KEYSET_FILTER_BUILTIN: KeysetFilterPort(c.base, clock=c.clock),
                KEYSET_ADD_BUILTIN: KeysetAddPort(c.base, clock=c.clock),
            }
        ),
        "action:noop": c.actor,
    }
    c.nodes["spark"] = c.node("spark", actors=ports)
    c.base.put("workflows", workflow())
    c.base.put("rules", rule().to_dict())
    c.start()
    data = {"repository": "acme/widgets", "head_sha": SHA, "conclusion": "success", "number": 3}
    runs = []
    for n in (1, 2):
        c.publish(envelope(n, type=SETTLED, data=dict(data)))
        for _ in range(8):
            c.clock.advance(1)
            c.cycle()
        runs.append(c.run("green-once", f"evt_{n}"))
    return c, runs


def test_second_success_for_the_same_commit_is_dropped():
    c, runs = _run_two_successes()
    assert [r["status"] for r in runs] == ["succeeded", "succeeded"]
    first, second = (step_state(r, "seen")["outputs"] for r in runs)
    assert (first["kept"], first["dropped"]) == (1, 0)
    assert (second["kept"], second["dropped"]) == (0, 1)
    assert [d["key"] for d in keysets.show_set(c.base, SET, c.clock())] == [f"acme/widgets@{SHA}"]
    assert len(c.base.find(RUNS_COLLECTION, {"rule_id": "green-once"})) == 2


def test_a_new_head_commit_is_kept_again():
    c, _runs = _run_two_successes()
    other = {"repository": "acme/widgets", "head_sha": "b" * 40, "conclusion": "success"}
    c.publish(envelope(3, type=SETTLED, data=other))
    for _ in range(8):
        c.clock.advance(1)
        c.cycle()
    third = step_state(c.run("green-once", "evt_3"), "seen")["outputs"]
    assert (third["kept"], third["dropped"]) == (1, 0)
