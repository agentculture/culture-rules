"""#53 t8: a step's ``config.on_error: "skip"`` tolerates its own failure.

A dispatched step (not a loop or wait step) whose ``config.on_error`` is ``"skip"`` and that
ends ``failed`` - a non-retryable failure, its retries used up, a queue timeout - is turned
into ``skipped`` by housekeeping, keeping its ``error``, with the history event
``failure_tolerated``. ``skipped`` counts as done, so downstream steps run with the step's
outputs missing and a loop goes on with its next iteration. The daily newspaper uses it so
an unreachable summariser still sends the results-only issue.
"""

from __future__ import annotations

import pytest

from culture_rules.engine.runs import Executor, step_key, step_state
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, edge, port, rule, step, workflow

SKIP = {"on_error": "skip"}
DOWN = ("fail", "llm_unavailable: refused", False)
SKIPPED = "skipped"
SUMMARY = "summary"
SUMMARISE = "summarise"
SUCCEEDED = "succeeded"
STATUS = "status"
RENDER = "render"
ON_ERROR_PATH = "steps[0].config.on_error"
ITEMS = "items"
ARRAY = "array"


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def clock():
    return Clock()


def run(store, clock, actor, wf):
    ex = Executor(store, "spark", {"*": actor}, clock=clock)
    started = ex.start(rule(), wf)
    ex.run_until_idle()
    return ex.run(started["id"])


def _flat(config):
    return workflow(
        (
            step(SUMMARISE, outputs=(port(SUMMARY, "string"),), config=config),
            step(RENDER, inputs=(port(SUMMARY, "string", required=False),)),
        ),
        (edge(SUMMARISE, SUMMARY, RENDER, SUMMARY),),
    )


def test_a_tolerated_failure_is_skipped_and_downstream_runs(store, clock):
    a = FakeActor().on(SUMMARISE, DOWN)
    doc = run(store, clock, a, _flat(SKIP))
    assert doc[STATUS] == SUCCEEDED, doc["steps"]
    state = step_state(doc, SUMMARISE)
    assert state[STATUS] == SKIPPED
    assert state["error"]["message"].startswith("llm_unavailable")
    assert SUMMARY not in a.calls_for(RENDER)[0][1]


def test_the_history_records_the_tolerated_failure(store, clock):
    a = FakeActor().on(SUMMARISE, DOWN)
    doc = run(store, clock, a, _flat(SKIP))
    events = [h["event"] for h in doc["history"] if h.get("step") == SUMMARISE]
    assert events[-2:] == ["failed", "failure_tolerated"]


def test_without_on_error_the_failure_still_fails_the_run(store, clock):
    a = FakeActor().on(SUMMARISE, DOWN)
    doc = run(store, clock, a, _flat({}))
    assert doc[STATUS] == "failed"
    assert a.calls_for(RENDER) == []


def test_a_retryable_failure_is_tolerated_once_its_retries_are_used(store, clock):
    a = FakeActor().on(SUMMARISE, ("fail", "llm_unavailable: down", True))
    ex = Executor(store, "spark", {"*": a}, clock=clock)
    started = ex.start(rule(), _flat(SKIP))
    for _ in range(10):
        ex.run_until_idle()
        clock.advance(600)
    doc = ex.run(started["id"])
    assert step_state(doc, SUMMARISE)[STATUS] == SKIPPED
    assert doc[STATUS] == SUCCEEDED


def test_a_tolerated_failure_in_a_loop_body_ends_only_that_iteration(store, clock):
    a = FakeActor(default=lambda inp, ctx: {"text": f"about {inp.get('item')}"})
    a.on(step_key("each", 0, SUMMARISE), DOWN)
    loop = step(
        "each",
        "for_each",
        inputs=(port(ITEMS, ARRAY),),
        outputs=(port("text", ARRAY),),
        max_iterations=3,
        body=(step(SUMMARISE, inputs=(port("item"),), outputs=(port("text"),), config=SKIP),),
    )
    wf = workflow(
        (step("list", outputs=(port(ITEMS, ARRAY),)), loop),
        (edge("list", ITEMS, "each", ITEMS),),
    )
    a.on("list", ("complete", {ITEMS: ["a", "b"]}))
    doc = run(store, clock, a, wf)
    assert doc[STATUS] == SUCCEEDED, doc["steps"]
    assert step_state(doc, "each")["outputs"]["text"] == [None, "about b"]


# --------------------------------------------------------------------------- save-time checks


def _codes(wf) -> set[tuple[str, str]]:
    from culture_rules.model.validate import validate

    return {(e.path, e.code) for e in validate(wf)}


def test_on_error_skip_passes_validation():
    assert _codes(workflow((step("s", "ai", config=SKIP),))) == set()


@pytest.mark.parametrize("value", ["continue", "", None, True, ["skip"]])
def test_another_on_error_value_is_refused(value):
    wf = workflow((step("s", "ai", config={"on_error": value}),))
    assert (ON_ERROR_PATH, "on_error_invalid") in _codes(wf)


def test_loops_and_waits_take_no_on_error():
    loop = step("l", "for_each", max_iterations=2, config=SKIP, body=(step("b"),))
    assert (ON_ERROR_PATH, "not_allowed") in _codes(workflow((loop,)))
    wait = step("w", "wait", config={"seconds": 5, **SKIP})
    assert (ON_ERROR_PATH, "not_allowed") in _codes(workflow((wait,)))
