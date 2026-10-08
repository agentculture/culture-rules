"""d20 engine support: a step's ``config.when`` skips it, ``retry_until``'s ``config.explain``.

``when`` is a condition tree over the step's own inputs (``field`` operands), evaluated
before the step is placed or dispatched: false skips the step (status ``skipped``, no actor
call, any node may do it); a tree that cannot be evaluated fails the step ``when_invalid``.
The fixer uses it so the reviewer agent runs only after a passing gate.

``explain`` names a field of a ``retry_until`` loop's last result; its text is appended to
the ``loop_max_exceeded`` message, so a hand-back says why the last attempt was refused.
"""

from __future__ import annotations

import pytest

from culture_rules.engine.runs import Executor, step_key, step_state
from culture_rules.model.placement import Placement
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, edge, port, rule, step, workflow

PASSING = {"op": "in", "value": {"field": "verdict"}, "items": {"literal": ["pass", "no_gate"]}}


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def clock():
    return Clock()


def run(store, clock, actor, wf, trigger=None):
    ex = Executor(store, "spark", {"*": actor}, clock=clock)
    started = ex.start(rule(workflow_inputs={"verdict": "trigger.verdict"}), wf, trigger=trigger)
    ex.run_until_idle()
    return ex.run(started["id"])


def _flat(when, *, review_placement=None):
    review = step(
        "review",
        "ai",
        inputs=(port("verdict", "string"), port("commit", "string")),
        outputs=(port("said", "string"),),
        config={"when": when},
        placement=review_placement,
    )
    after = step("after", inputs=(port("said", "string", required=False),))
    return workflow(
        (
            step("gate", outputs=(port("verdict", "string"), port("commit", required=False))),
            review,
            after,
        ),
        (
            edge("gate", "verdict", "review", "verdict"),
            edge("gate", "commit", "review", "commit"),
            edge("review", "said", "after", "said"),
        ),
    )


def test_a_false_when_skips_the_step_without_calling_its_actor(store, clock):
    a = FakeActor().on("gate", ("complete", {"verdict": "fail"}))  # no commit at all
    doc = run(store, clock, a, _flat(PASSING))
    assert doc["status"] == "succeeded", doc["steps"]
    assert step_state(doc, "review")["status"] == "skipped"
    assert a.calls_for("review") == []
    assert step_state(doc, "after")["status"] == "succeeded"  # downstream still runs


def test_a_true_when_runs_the_step(store, clock):
    a = FakeActor().on("gate", ("complete", {"verdict": "pass", "commit": "c"}))
    a.on("review", ("complete", {"said": "ok"}))
    doc = run(store, clock, a, _flat(PASSING))
    assert step_state(doc, "review")["status"] == "succeeded"
    assert len(a.calls_for("review")) == 1


def test_the_skip_needs_no_placement_on_this_host(store, clock):
    # placed on a machine that is not enrolled: dispatching would fail; skipping does not
    a = FakeActor().on("gate", ("complete", {"verdict": "guard"}))
    doc = run(store, clock, a, _flat(PASSING, review_placement=Placement(machine="elsewhere")))
    assert step_state(doc, "review")["status"] == "skipped"
    assert doc["status"] == "succeeded"


@pytest.mark.parametrize("when", ["verdict == pass", {"op": "bogus"}, {"op": "in"}, 3])
def test_a_pinned_when_that_cannot_be_evaluated_fails_the_step(store, clock, when):
    # save-time validation refuses these (below); a pinned copy that slipped past it still
    # never dispatches the step
    from culture_rules.engine.runs import RUNS_COLLECTION

    a = FakeActor().on("gate", ("complete", {"verdict": "pass", "commit": "c"}))
    ex = Executor(store, "spark", {"*": a}, clock=clock)
    started = ex.start(rule(), _flat(PASSING))
    doc = store.get(RUNS_COLLECTION, started["id"])
    for s in doc["workflow"]["definition"]["steps"]:
        if s["id"] == "review":
            s["config"]["when"] = when
    store.put(RUNS_COLLECTION, doc)
    ex.run_until_idle()
    doc = ex.run(started["id"])
    assert doc["status"] == "failed"
    assert step_state(doc, "review")["error"]["code"] == "when_invalid"
    assert a.calls_for("review") == []


def test_when_in_a_loop_body_skips_only_that_iteration(store, clock):
    a = FakeActor()
    a.on(step_key("fix", 0, "gate"), ("complete", {"verdict": "fail"}))
    a.on(step_key("fix", 0, "last"), ("complete", {"verdict": "fail", "ok": False}))
    a.on(step_key("fix", 1, "gate"), ("complete", {"verdict": "pass"}))
    a.on(step_key("fix", 1, "review"), ("complete", {"said": "fine"}))
    a.on(step_key("fix", 1, "last"), ("complete", {"verdict": "pass", "ok": True}))
    loop = step(
        "fix",
        "retry_until",
        outputs=(port("ok", "boolean"),),
        max_iterations=3,
        config={
            "until": {
                "op": "compare",
                "cmp": "==",
                "left": {"field": "ok"},
                "right": {"literal": True},
            }
        },
        body=(
            step("gate", outputs=(port("verdict", "string"),)),
            step(
                "review",
                "ai",
                inputs=(port("verdict", "string"),),
                outputs=(port("said", "string"),),
                config={"when": PASSING},
            ),
            step("last", inputs=(port("said", required=False),), outputs=(port("ok", "boolean"),)),
        ),
    )
    wf = workflow(
        (loop,),
        (
            edge("gate", "verdict", "review", "verdict"),
            edge("review", "said", "last", "said"),
        ),
    )
    doc = run(store, clock, a, wf)
    assert doc["status"] == "succeeded", doc["steps"]
    assert step_state(doc, step_key("fix", 0, "review"))["status"] == "skipped"
    assert step_state(doc, step_key("fix", 1, "review"))["status"] == "succeeded"
    assert a.calls_for(step_key("fix", 1, "last"))[0][1]["said"] == "fine"
    assert "said" not in a.calls_for(step_key("fix", 0, "last"))[0][1]


def _explained(explain):
    config = {
        "until": {"op": "compare", "cmp": "==", "left": {"field": "ok"}, "right": {"literal": True}}
    }
    if explain is not None:
        config["explain"] = explain
    return workflow(
        (
            step(
                "fix",
                "retry_until",
                outputs=(port("ok", "boolean"),),
                max_iterations=2,
                config=config,
                body=(step("try", outputs=(port("ok", "boolean"), port("why", required=False))),),
            ),
        )
    )


def test_explain_puts_the_last_attempts_text_in_the_loop_failure(store, clock):
    a = FakeActor()
    a.on(step_key("fix", 0, "try"), ("complete", {"ok": False, "why": "first reason"}))
    a.on(step_key("fix", 1, "try"), ("complete", {"ok": False, "why": "- [high] a.py:3 bad"}))
    doc = run(store, clock, a, _explained("why"))
    error = step_state(doc, "fix")["error"]
    assert error["code"] == "loop_max_exceeded"
    assert error["message"] == "until not met after 2 iterations; last: - [high] a.py:3 bad"


def test_explain_is_capped_and_absent_text_leaves_the_message_alone(store, clock):
    a = FakeActor(default=lambda inp, ctx: {"ok": False})
    a.on(step_key("fix", 1, "try"), ("complete", {"ok": False, "why": "x" * 5000}))
    doc = run(store, clock, a, _explained("why"))
    message = step_state(doc, "fix")["error"]["message"]
    assert message.startswith("until not met after 2 iterations; last: xxx")
    assert len(message) < 2200
    assert message.endswith("…")
    b = FakeActor(default=lambda inp, ctx: {"ok": False})
    doc = run(MemoryStore(), clock, b, _explained("why"))
    assert step_state(doc, "fix")["error"]["message"] == "until not met after 2 iterations"
    doc = run(MemoryStore(), clock, b, _explained(None))
    assert step_state(doc, "fix")["error"]["message"] == "until not met after 2 iterations"


# --------------------------------------------------------------------------- save-time checks


def _codes(wf) -> set[tuple[str, str]]:
    from culture_rules.model.validate import validate

    return {(e.path, e.code) for e in validate(wf)}


@pytest.mark.parametrize("when", ["verdict == pass", {"op": "bogus"}, {"op": "in"}, 3, None])
def test_a_malformed_when_is_refused_at_save(when):
    wf = workflow((step("s", "ai", config={"when": when}),))
    assert ("steps[0].config.when", "when_invalid") in _codes(wf)


def test_a_well_formed_when_passes_and_loops_and_waits_refuse_one():
    assert _codes(workflow((step("s", "ai", config={"when": PASSING}),))) == set()
    loop = step("l", "retry_until", max_iterations=2, config={"when": PASSING}, body=(step("b"),))
    assert ("steps[0].config.when", "not_allowed") in _codes(workflow((loop,)))
    wait = step("w", "wait", config={"seconds": 5, "when": PASSING})
    assert ("steps[0].config.when", "not_allowed") in _codes(workflow((wait,)))


@pytest.mark.parametrize("explain", ["", 3, None, ["why"]])
def test_explain_must_name_a_field_of_a_retry_until_loop(explain):
    loop = step(
        "l", "retry_until", max_iterations=2, config={"explain": explain}, body=(step("b"),)
    )
    assert ("steps[0].config.explain", "explain_invalid") in _codes(workflow((loop,)))
    ok = step("l", "retry_until", max_iterations=2, config={"explain": "why"}, body=(step("b"),))
    assert _codes(workflow((ok,))) == set()
    each = step("e", "for_each", max_iterations=2, config={"explain": "why"}, body=(step("b"),))
    assert ("steps[0].config.explain", "not_allowed") in _codes(workflow((each,)))
