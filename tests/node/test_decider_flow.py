"""decider-step-in-workflows (o11): a decider.assess step's picks decide whether a later step runs.

One node (spark) runs a small workflow on MemoryStore: ``ask`` (decider.assess on the committed
``decider`` actor, a fake gateway transport) exports ``answers`` and ``picks``; ``go`` runs only
when ``picks.action == run_as_usual`` and ``hold`` only when it is ``skip_tonight``; ``careful``
reads ``answers.risk.score``. Synthetic facts only.
"""

from __future__ import annotations

import copy
import json

import pytest

from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.io.exchange import read_bundle
from culture_rules.model.validate import validate
from culture_rules.model.workflow import Workflow
from culture_rules.node.actions.decider import DeciderAssessPort
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.node.daemon import Node
from culture_rules.node.runner import NoopAction
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, enrol_online, machine
from tests.node.test_decider_assess import ANSWERS, FakeTransport, _reply
from tests.node.test_node import BEATS
from tests.rules.test_decider_bundle import BUNDLE

QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": "What should happen tonight?",
        "criteria": {"run_as_usual": "run the copy", "skip_tonight": "skip the copy"},
    },
    "disk_ok": {"type": "noul", "instructions": "Is there enough free space?"},
    "risk": {
        "type": "score",
        "instructions": "How risky is tonight's copy?",
        "criteria": {"low": "little can go wrong", "medium": "it might", "high": "likely fails"},
    },
}


def _port(name, typ="object", required=True):
    return {"name": name, "type": typ, "required": required, "description": ""}


def _branch(step_id, when):
    return {
        "id": step_id,
        "name": step_id,
        "kind": "code",
        "inputs": [_port("picks", required=False), _port("answers", required=False)],
        "outputs": [],
        "config": {"builtin": "action", "action": {"kind": "noop", "params": {}}, "when": when},
    }


def _eq(field, value):
    return {"op": "compare", "cmp": "==", "left": {"field": field}, "right": {"literal": value}}


WORKFLOW = {
    "id": "decide-tonight",
    "name": "Decide tonight",
    "inputs": [_port("state", "string")],
    "steps": [
        {
            "id": "ask",
            "name": "Ask the decider",
            "kind": "code",
            "inputs": [_port("state", "string")],
            "outputs": [
                _port("answers"),
                _port("picks"),
                _port("host", "string"),
                _port("domain", "string"),
                _port("usage", required=False),
            ],
            "config": {
                "builtin": "action",
                "action": {
                    "kind": "decider.assess",
                    "params": {"actor": "decider", "state": "inputs.state", "questions": QUESTIONS},
                },
                "idempotent": True,
            },
        },
        _branch("go", _eq("picks.action", "run_as_usual")),
        _branch("hold", _eq("picks.action", "skip_tonight")),
        _branch(
            "careful",
            {
                "op": "compare",
                "cmp": ">=",
                "left": {"field": "answers.risk.score"},
                "right": {"literal": 1},
            },
        ),
    ],
    "edges": [
        {"source": "inputs", "source_port": "state", "target": "ask", "target_port": "state"},
        *[
            {"source": "ask", "source_port": port, "target": step, "target_port": port}
            for step in ("go", "hold", "careful")
            for port in ("picks", "answers")
        ],
    ],
    "outputs": [
        {"name": "picks", "source": "steps.ask.outputs.picks", "type": "object"},
        {"name": "host", "source": "steps.ask.outputs.host", "type": "string"},
    ],
}


class World:
    def __init__(self, transport: FakeTransport) -> None:
        self.clock = Clock()
        self.base = MemoryStore(clock=self.clock)
        enrol_online(self.base, self.clock, machine("spark"))
        (actor,) = read_bundle(BUNDLE).bundle.actors
        self.base.put(ACTORS_COLLECTION, copy.deepcopy(actor.to_dict()))
        self.base.put("workflows", WORKFLOW)
        self.transport = transport
        port = DeciderAssessPort(
            self.base, transport=transport, secret_runner=lambda _r: "k", clock=self.clock
        )
        self.node = Node(
            self.base.peer(),
            "spark",
            actors={"action:decider.assess": port, "action:noop": NoopAction()},
            clock=self.clock,
            heartbeat_options=BEATS,
        )
        self.node.start()

    def run(self, state: str = "A nightly copy of 2 GB to a disk with 500 GB free.") -> dict:
        started = self.node.executor.start_workflow("decide-tonight", {"state": state}, "test")
        for _ in range(10):
            self.clock.advance(1)
            report = self.node.run_once()
            assert report.errors == [], report.errors
        return self.base.get(RUNS_COLLECTION, started["id"])


def _status(run: dict) -> dict[str, str]:
    return {s["def"]: s["status"] for s in run["steps"] if not s["def"].startswith("@")}


def test_the_workflow_validates():
    assert validate(Workflow.from_dict(WORKFLOW)) == []


def test_the_pick_runs_the_matching_branch_and_skips_the_other():
    world = World(FakeTransport())
    run = world.run()
    assert run["status"] == "succeeded", (run.get("error"), run["steps"])
    assert _status(run) == {
        "ask": "succeeded",
        "go": "succeeded",
        "hold": "skipped",
        "careful": "skipped",
    }
    assert run["outputs"]["picks"] == {"action": "run_as_usual", "disk_ok": True, "risk": "low"}
    assert run["outputs"]["host"] == "local"
    (call,) = world.transport.calls
    assert call["headers"]["X-Lobes-Model"] == "decider:general"


def test_another_answer_takes_the_other_branch():
    answers = copy.deepcopy(ANSWERS)
    answers["action"].update(
        choice="skip_tonight", probabilities={"run_as_usual": 0.1, "skip_tonight": 0.9}
    )
    answers["risk"].update(score=1.6, probabilities={"0": 0.1, "1": 0.2, "2": 0.7}, confidence=0.7)
    run = World(FakeTransport(body=_reply(answers=answers))).run()
    assert _status(run) == {
        "ask": "succeeded",
        "go": "skipped",
        "hold": "succeeded",
        "careful": "succeeded",
    }
    assert run["outputs"]["picks"]["risk"] == "high"


@pytest.mark.parametrize("status", [404, 422])
def test_a_rejected_assessment_fails_the_run_and_no_branch_runs(status):
    body = json.dumps({"error": {"code": "model_not_found"}}).encode()
    run = World(FakeTransport(status=status, body=body)).run()
    assert run["status"] == "failed"
    assert "decider_rejected" in json.dumps(run.get("error"))
    assert _status(run).get("go") != "succeeded"


def test_the_state_text_is_not_kept_in_the_run_outputs():
    run = World(FakeTransport()).run(state="sentinel-state-words about the copy")
    assert "sentinel-state-words" not in json.dumps(run["outputs"])
    for step in run["steps"]:
        assert "sentinel-state-words" not in json.dumps(step.get("outputs") or {})
