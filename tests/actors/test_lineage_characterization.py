"""Characterization tests (Sonar S3776 split of d21 code): pin ``final_gate``'s refusals
exactly as they behave, so splitting it into helpers is provably behaviour-preserving."""

from __future__ import annotations

import pytest

from culture_rules.actors.lineage import LineageError, final_gate

GATE = {"id": "gate", "kind": "code", "config": {"builtin": "gate"}}
AGENT = {"id": "agent", "kind": "ai"}


def _run(steps, states):
    return {"id": "run-1", "workflow": {"definition": {"steps": steps}}, "steps": states}


def _loop(body, id="fix"):
    return {"id": id, "kind": "retry_until", "body": body}


OK_STATES = [
    {"key": "fix", "status": "succeeded", "iteration": 1},
    {"key": "fix[1]/gate", "status": "succeeded", "outputs": {"verdict": "pass"}},
]


def _refusal(run):
    with pytest.raises(LineageError) as exc:
        final_gate(run)
    return exc.value.code, exc.value.detail


def test_the_final_gate_is_the_loops_last_iteration():
    fg = final_gate(_run(["junk", {"id": "x", "kind": "code"}, _loop([AGENT, GATE])], OK_STATES))
    assert (fg.parent, fg.iteration, fg.gate_id) == ("fix", 1, "gate")
    assert fg.outputs == {"verdict": "pass"}
    assert sorted(fg.body) == ["agent", "gate"]


def test_a_loop_without_a_gate_is_not_counted():
    fg = final_gate(_run([_loop([AGENT], id="other"), _loop([AGENT, GATE])], OK_STATES))
    assert fg.parent == "fix"


@pytest.mark.parametrize(
    "steps",
    [
        [],
        [_loop([AGENT])],
        [_loop([GATE]), _loop([GATE], id="fix2")],
        [_loop([GATE, {**GATE, "id": "gate2"}])],
    ],
)
def test_not_exactly_one_gate_in_one_loop_is_bad_config(steps):
    assert _refusal(_run(steps, OK_STATES)) == (
        "bad_config",
        "the fix run has not exactly one gate in one loop",
    )


def test_a_gate_placed_on_an_actor_is_bad_config():
    gate = {**GATE, "placement": {"actor": "x"}}
    assert _refusal(_run([_loop([gate])], OK_STATES)) == (
        "bad_config",
        "the gate must be the actor-less built-in gate",
    )


def test_a_non_mapping_gate_placement_is_no_actor():
    gate = {**GATE, "placement": "x"}
    assert final_gate(_run([_loop([gate])], OK_STATES)).gate_id == "gate"


@pytest.mark.parametrize(
    "loop_state",
    [
        None,
        {"key": "fix", "status": "failed", "iteration": 1},
        {"key": "fix", "status": "succeeded"},
        {"key": "fix", "status": "succeeded", "iteration": "1"},
    ],
)
def test_a_loop_that_did_not_succeed_is_gate_missing(loop_state):
    states = [s for s in (loop_state, OK_STATES[1]) if s]
    assert _refusal(_run([_loop([GATE])], states)) == (
        "gate_missing",
        "the fix run's loop did not succeed",
    )


@pytest.mark.parametrize(
    "gate_state",
    [None, {"key": "fix[1]/gate", "status": "failed"}, {"key": "fix[0]/gate", "status": "x"}],
)
def test_a_last_gate_that_did_not_succeed_is_gate_missing(gate_state):
    states = [s for s in (OK_STATES[0], gate_state) if s]
    assert _refusal(_run([_loop([GATE])], states)) == (
        "gate_missing",
        "the fix run's last gate did not succeed",
    )
