"""D7 engine proof (h8/h15): adding a stepless wrapper changes only workflow_id."""

from dataclasses import replace

import pytest

from culture_rules.engine.matching import CONDITION_FALSE, fired, match
from culture_rules.engine.run_completions import RUN_COMPLETIONS
from culture_rules.engine.runs import ACTION_STEP, Executor
from culture_rules.model.action import Action
from culture_rules.model.rule import Trigger, WorkflowRef
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, ports_for, rule, workflow

EVENT = {
    "id": "event-stepless",
    "kind": "event",
    "type": "example.ready",
    "data": {"number": 7, "text": "ready"},
}


@pytest.fixture
def paired_runs():
    plain = replace(
        rule(
            workflow_id=None,
            action=Action(
                kind="noop",
                params={
                    "event_id": "trigger.id",
                    "number": {"$ref": "trigger.data.number"},
                    "nested": ["trigger.data.text"],
                    "message": "event={{ trigger.id }}",
                    "literal": {"$literal": "trigger.id"},
                },
            ),
        ),
        trigger=Trigger(kind="event", params={"type": EVENT["type"]}),
    )
    wrapper = workflow((), id="stepless-wrapper")
    wrapped = replace(plain, workflow=WorkflowRef(id=wrapper.id))
    results = []
    for candidate, wf in ((plain, None), (wrapped, wrapper)):
        # Isolated stores permit the same run identity; a fixed clock removes time
        # differences without stripping any fields from the emitted envelopes.
        clock = Clock()
        store = MemoryStore(clock=clock)
        actor = FakeActor()
        executor = Executor(store, "spark", ports_for(actor), clock=clock)
        assert fired(match(EVENT, [candidate])) == {plain.id}
        started = executor.start(candidate, wf, trigger=EVENT, run_id="run-stepless-proof")
        executor.run_until_idle()
        completed = executor.run(started["id"])
        completion = store.get(RUN_COMPLETIONS, started["id"])
        assert completed is not None
        assert completion is not None
        results.append((completed, actor, completion["envelope"]))
    return results


def test_stepless_workflow_preserves_action_params_and_run_status(paired_runs):
    (plain, plain_actor, _), (wrapped, wrapped_actor, _) = paired_runs
    assert plain["status"] == wrapped["status"] == "succeeded"
    assert plain["workflow_id"] is None
    assert wrapped["workflow_id"] == "stepless-wrapper"
    assert len(plain_actor.calls) == len(wrapped_actor.calls) == 1
    [plain_call] = plain_actor.calls_for(ACTION_STEP)
    [wrapped_call] = wrapped_actor.calls_for(ACTION_STEP)
    assert plain_call[2].config["kind"] == wrapped_call[2].config["kind"] == "noop"
    assert plain_call[1] == wrapped_call[1] == {
        "event_id": "event-stepless",
        "number": 7,
        "nested": ["ready"],
        "message": "event=event-stepless",
        "literal": "trigger.id",
    }


def _assert_same_fields(before, after, path=()):
    if path == ("data", "workflow_id"):
        assert before is None
        assert after == "stepless-wrapper"
    elif isinstance(before, dict):
        assert isinstance(after, dict), path
        assert before.keys() == after.keys(), path
        for key in before:
            _assert_same_fields(before[key], after[key], (*path, key))
    else:
        assert before == after, ".".join(path)


def test_run_events_differ_only_in_workflow_id_field_by_field(paired_runs):
    (_, _, plain_event), (_, _, wrapped_event) = paired_runs
    assert plain_event["type"] == wrapped_event["type"] == "rules.run.succeeded"
    _assert_same_fields(plain_event, wrapped_event)


def test_other_workflow_continuation_does_not_fire_on_wrapper_event(paired_runs):
    (_, _, plain_event), (_, _, wrapped_event) = paired_runs
    listener = replace(
        rule(id="continuation", workflow_id=None),
        trigger=Trigger(kind="event", params={"type": "rules.run.succeeded"}),
        condition={
            "op": "compare",
            "cmp": "==",
            "left": {"field": "data.workflow_id"},
            "right": {"literal": "existing-predecessor"},
        },
    )
    for event in (plain_event, wrapped_event):
        [decision] = match(event, [listener])
        assert not decision.fire
        assert decision.reason == CONDITION_FALSE
    # Positive controls: the same listener fires for its predecessor, and an
    # unfiltered listener already fires both before and after wrapping.
    matching_event = {
        **wrapped_event,
        "data": {**wrapped_event["data"], "workflow_id": "existing-predecessor"},
    }
    assert fired(match(matching_event, [listener])) == {listener.id}
    for event in (plain_event, wrapped_event):
        assert fired(match(event, [replace(listener, condition=None)])) == {listener.id}
