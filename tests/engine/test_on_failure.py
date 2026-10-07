"""d16: a rule's optional ``on_failure`` action runs exactly once when its run ends failed.

Same shape, validation and routing as the terminal ``action``; its params may also read
``run.error.step`` / ``run.error.code`` / ``run.error.message`` (the failure that ended the
run) besides ``run.id``. Never on success, supersession or cancellation; its own failure
ends the run failed with the original error and never fires it again.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.runs import (
    ACTION_STEP,
    FAILURE_STEP,
    SUPERSEDED,
    Containment,
    Executor,
    step_state,
)
from culture_rules.model.action import Action
from culture_rules.model.common import RetryPolicy
from culture_rules.model.rule import Rule
from culture_rules.model.validate import validate
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, Crash, FakeActor, edge, port, rule, step, workflow
from tests.engine.test_wait_step import SHA_A, SHA_B, Heads, guard_config

BODY = "handed back: {{ run.error.step }} {{ run.error.code }} {{ run.error.message }} {{ run.id }}"
HAND_BACK = Action(kind="noop", name="hand back", params={"body": BODY, "ref": "run.error.code"})


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock) -> MemoryStore:
    return MemoryStore(clock=clock)


def failing_rule(**kw) -> Rule:
    return replace(rule(), on_failure=HAND_BACK, **kw)


def one_step():
    return workflow((step("s1", outputs=(port("n", "integer", False),)),))


def calls(actor: FakeActor, key: str) -> list[dict]:
    return [c[1] for c in actor.calls if c[2].step_id == key]


def run(store, clock, actor, r, wf, *, heads=None, trigger=None):
    ex = Executor(store, "spark", {"*": actor}, clock=clock, head_lookup=heads)
    doc = ex.start(r, wf, trigger=trigger)
    ex.run_until_idle()
    return ex, ex.run(doc["id"])


# ------------------------------------------------------------------ model


def test_on_failure_round_trips_and_defaults_to_none():
    r = failing_rule()
    assert Rule.from_dict(r.to_dict()) == r
    assert rule().on_failure is None
    assert Rule.from_dict({k: v for k, v in rule().to_dict().items() if k != "on_failure"})


def test_on_failure_gets_the_action_kind_param_checks():
    bad = replace(rule(), on_failure=Action(kind="github.comment", params={"actor": "gh"}))
    found = {(e.path, e.code) for e in validate(bad)}
    assert ("on_failure.params.body", "action_param_required") in found
    unknown = replace(rule(), on_failure=Action(kind="nope"))
    assert "action_kind_unknown" in {e.code for e in validate(unknown)}


def test_run_error_is_readable_only_from_on_failure():
    assert validate(failing_rule()) == []
    for params in (
        {"x": "run.error.code"},
        {"x": "see {{ run.error.message }}"},
        {"x": {"$ref": "run.error.step"}},
    ):
        errs = validate(replace(rule(), action=Action(kind="noop", params=params)))
        assert [e.code for e in errs] == ["invalid_reference"]
        assert errs[0].path.startswith("action.params.x")


@pytest.mark.parametrize("ref", ["run.status", "run.error.trace", "run.error.code.x"])
def test_other_run_fields_are_refused(ref):
    r = replace(rule(), on_failure=Action(kind="noop", params={"x": {"$ref": ref}}))
    assert "invalid_reference" in {e.code for e in validate(r)}


# ------------------------------------------------------------------ executor


def test_a_failed_step_runs_on_failure_once_with_the_error_and_the_run_id(store, clock):
    actor = FakeActor().on("s1", ("fail", "boom", False))
    _ex, doc = run(store, clock, actor, failing_rule(), one_step())
    assert doc["status"] == "failed"
    assert doc["error"]["step"] == "s1" and doc["error"]["message"] == "boom"
    (inp,) = calls(actor, FAILURE_STEP)
    code = doc["error"]["code"]
    assert inp["body"] == f"handed back: s1 {code} boom {doc['id']}"
    assert inp["ref"] == code
    assert step_state(doc, FAILURE_STEP)["status"] == "succeeded"
    assert calls(actor, ACTION_STEP) == []  # the success action never runs


def test_a_failed_terminal_action_also_hands_back(store, clock):
    actor = FakeActor().on(ACTION_STEP, ("fail", "no comment", False))
    _ex, doc = run(store, clock, actor, failing_rule(), one_step())
    assert doc["status"] == "failed" and doc["error"]["step"] == ACTION_STEP
    (inp,) = calls(actor, FAILURE_STEP)
    assert "no comment" in inp["body"]


def test_a_successful_run_never_runs_on_failure(store, clock):
    actor = FakeActor()
    _ex, doc = run(store, clock, actor, failing_rule(), one_step())
    assert doc["status"] == "succeeded"
    assert len(calls(actor, ACTION_STEP)) == 1
    assert calls(actor, FAILURE_STEP) == [] and step_state(doc, FAILURE_STEP) is None


def test_a_superseded_run_never_runs_on_failure(store, clock):
    wf = workflow(
        (
            step("w", "wait", config=guard_config(), outputs=(port("done", "any", False),)),
            step("after", inputs=(port("done", "any", False),)),
        ),
        (edge("w", "done", "after", "done"),),
        inputs=(port("head_sha", "string", required=False),),
    )
    r = replace(
        failing_rule(), workflow=replace(rule().workflow, inputs={"head_sha": "trigger.data.h"})
    )
    actor = FakeActor()
    ex, doc = run(
        store,
        clock,
        actor,
        r,
        wf,
        heads=Heads(SHA_B),
        trigger={"kind": "manual", "data": {"h": SHA_A}},
    )
    clock.advance(400)
    ex.run_until_idle()
    doc = ex.run(doc["id"])
    assert doc["status"] == SUPERSEDED
    assert calls(actor, FAILURE_STEP) == []


def test_a_cancelled_run_never_runs_on_failure(store, clock):
    actor = FakeActor().on("s1", ("accept",))
    ex, doc = run(store, clock, actor, failing_rule(), one_step())
    Containment(store, clock=clock).cancel(doc["id"], identity="op@test")
    ex.run_until_idle()
    assert ex.run(doc["id"])["status"] == "cancelled"
    assert calls(actor, FAILURE_STEP) == []


def test_a_failing_on_failure_ends_the_run_with_the_original_error(store, clock):
    actor = (
        FakeActor()
        .on("s1", ("fail", "boom", False))
        .on(FAILURE_STEP, ("fail", "github down", True), ("fail", "github down", True))
    )
    r = failing_rule()
    r = replace(r, on_failure=replace(HAND_BACK, retry=RetryPolicy(max_attempts=2, backoff_s=1)))
    ex, doc = run(store, clock, actor, r, one_step())
    for _ in range(5):  # time passes: the retry happens once, then nothing more
        clock.advance(60)
        ex.run_until_idle()
    doc = ex.run(doc["id"])
    assert doc["status"] == "failed"
    assert doc["error"]["step"] == "s1" and doc["error"]["message"] == "boom"
    assert len(calls(actor, FAILURE_STEP)) == 2  # its own retry policy, then done
    assert step_state(doc, FAILURE_STEP)["status"] == "failed"


def test_without_on_failure_a_failed_run_is_unchanged(store, clock):
    actor = FakeActor().on("s1", ("fail", "boom", False))
    _ex, doc = run(store, clock, actor, rule(), one_step())
    assert doc["status"] == "failed" and doc["error"]["step"] == "s1"
    assert step_state(doc, FAILURE_STEP) is None


# ------------------------------------------------------------------ entry paths (Codex P2)


def handlers(doc) -> list[dict]:
    return [s for s in doc["steps"] if s["key"] == FAILURE_STEP]


def drive(ex, clock, rounds=5, step_s=60):
    for _ in range(rounds):
        clock.advance(step_s)
        ex.run_until_idle()


def test_a_mistyped_workflow_output_hands_back_exactly_once(store, clock):
    from culture_rules.model.workflow import Output  # noqa: PLC0415

    wf = replace(
        one_step(), outputs=(Output(name="n", type="string", source="steps.s1.outputs.n"),)
    )
    actor = FakeActor().on("s1", ("complete", {"n": 3}))
    ex, doc = run(store, clock, actor, failing_rule(), wf)
    drive(ex, clock)
    doc = ex.run(doc["id"])
    assert doc["status"] == "failed"
    assert doc["error"]["code"] == "output_type_mismatch" and doc["error"]["step"] is None
    (h,) = handlers(doc)
    assert h["status"] == "succeeded"
    (inp,) = calls(actor, FAILURE_STEP)
    assert "output_type_mismatch" in inp["body"]
    assert calls(actor, ACTION_STEP) == [] and step_state(doc, ACTION_STEP) is None


def test_a_failed_rule_action_hands_back_exactly_once(store, clock):
    actor = FakeActor().on(ACTION_STEP, ("fail", "no comment", False))
    ex, doc = run(store, clock, actor, failing_rule(), one_step())
    drive(ex, clock)
    doc = ex.run(doc["id"])
    assert len(handlers(doc)) == 1 and len(calls(actor, FAILURE_STEP)) == 1
    assert doc["error"]["step"] == ACTION_STEP


def test_a_failed_step_beside_accepted_work_hands_back_once_and_ignores_late_results(store, clock):
    wf = workflow((step("slow"), step("bad")))
    actor = FakeActor().on("slow", ("accept",)).on("bad", ("fail", "boom", False))
    ex, doc = run(store, clock, actor, failing_rule(), wf)
    doc = ex.run(doc["id"])
    assert step_state(doc, "slow")["status"] == "cancelled"
    slow_key = actor.calls_for("slow")[0][0]
    assert ex.deliver(slow_key, InvocationResult.completed({})) is False  # stays cancelled
    drive(ex, clock)
    doc = ex.run(doc["id"])
    assert doc["status"] == "failed" and doc["error"]["step"] == "bad"
    assert len(handlers(doc)) == 1 and len(calls(actor, FAILURE_STEP)) == 1


def test_an_accepted_handler_completes_by_delivery_once(store, clock):
    actor = FakeActor().on("s1", ("fail", "boom", False)).on(FAILURE_STEP, ("accept",))
    ex, doc = run(store, clock, actor, failing_rule(), one_step())
    doc = ex.run(doc["id"])
    assert doc["status"] == "running" and handlers(doc)[0]["status"] == "waiting"
    drive(ex, clock, rounds=2, step_s=1)  # nothing re-adds or finishes it meanwhile
    assert len(handlers(ex.run(doc["id"]))) == 1 and ex.run(doc["id"])["status"] == "running"
    key = actor.calls_for(FAILURE_STEP)[0][0]
    assert ex.deliver(key, InvocationResult.completed({})) is True
    ex.run_until_idle()
    doc = ex.run(doc["id"])
    assert doc["status"] == "failed" and doc["error"]["message"] == "boom"
    assert len(handlers(doc)) == 1 and len(calls(actor, FAILURE_STEP)) == 1


def test_a_handler_crash_resumes_on_another_node_with_one_side_effect(store, clock):
    actor = FakeActor().on("s1", ("fail", "boom", False)).on(FAILURE_STEP, ("crash", {}))
    ex = Executor(store, "spark", {"*": actor}, clock=clock, lease=timedelta(seconds=30))
    doc = ex.start(failing_rule(), one_step())
    with pytest.raises(Crash):
        ex.run_until_idle()
    assert handlers(ex.run(doc["id"]))[0]["status"] == "dispatching"
    other = Executor(store, "thor", {"*": actor}, clock=clock, lease=timedelta(seconds=30))
    other.run_until_idle()
    clock.advance(31)
    other.run_until_idle()
    doc = other.run(doc["id"])
    assert doc["status"] == "failed" and doc["error"]["message"] == "boom"
    assert len(handlers(doc)) == 1
    assert actor.effects_for(FAILURE_STEP) == 1  # the same key, deduplicated


def test_a_handler_whose_lease_and_deadline_lapse_ends_the_run_without_a_second_handler(
    store, clock
):
    # The target cannot deduplicate: an unknown outcome is never blindly retried.
    actor = FakeActor(idempotent=False).on("s1", ("fail", "boom", False))
    actor.on(FAILURE_STEP, ("accept",))
    r = replace(failing_rule(), on_failure=replace(HAND_BACK, timeout_s=30.0))
    ex = Executor(store, "spark", {"*": actor}, clock=clock, lease=timedelta(seconds=10))
    doc = ex.start(r, one_step())
    ex.run_until_idle()
    other = Executor(store, "thor", {"*": actor}, clock=clock, lease=timedelta(seconds=10))
    for e in (ex, other) * 3:
        clock.advance(20)
        e.run_until_idle()
    doc = other.run(doc["id"])
    assert doc["status"] == "failed" and doc["error"]["step"] == "s1"
    (h,) = handlers(doc)
    assert h["status"] == "failed"
    assert len(calls(actor, FAILURE_STEP)) == 1
