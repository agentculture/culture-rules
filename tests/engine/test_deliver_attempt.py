"""``Executor.deliver(..., attempt=N)`` refuses a result for an attempt the step moved past
(pr-fixer deviation d3): the check runs inside deliver's compare-and-set loop."""

from __future__ import annotations

from culture_rules.engine.actorport import InvocationResult
from culture_rules.engine.claims import idempotency_key
from culture_rules.engine.runs import Executor, step_state
from culture_rules.model.common import RetryPolicy
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, port, rule, step, workflow


def two_attempts():
    """A step whose attempt 1 was accepted then timed out, and attempt 2 is waiting."""
    store, clock = MemoryStore(), Clock()
    actor = FakeActor().on("s", ("accept",), ("accept",))
    ex = Executor(store, "spark", {"*": actor}, clock=clock)
    wf = workflow(
        (
            step(
                "s",
                outputs=(port("n", "any"),),
                timeout_s=60,
                retry=RetryPolicy(max_attempts=2, backoff_s=1),
            ),
        )
    )
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    clock.advance(61)
    ex.run_until_idle()
    clock.advance(2)
    ex.run_until_idle()
    st = step_state(ex.run(run["id"]), "s")
    assert (st["attempt"], st["status"]) == (2, "waiting")
    return ex, run["id"], idempotency_key(run["id"], "s")


def test_a_result_for_an_older_attempt_is_refused():
    ex, run_id, key = two_attempts()
    assert ex.deliver(key, InvocationResult.completed({"n": 1}), attempt=1) is False
    st = step_state(ex.run(run_id), "s")
    assert (st["attempt"], st["status"]) == (2, "waiting")


def test_a_result_for_the_current_attempt_is_delivered():
    ex, run_id, key = two_attempts()
    assert ex.deliver(key, InvocationResult.completed({"n": 2}), attempt=2) is True
    assert step_state(ex.run(run_id), "s")["outputs"] == {"n": 2}


def test_without_an_attempt_deliver_is_unchanged():
    ex, run_id, key = two_attempts()
    assert ex.deliver(key, InvocationResult.completed({"n": 3})) is True
    assert step_state(ex.run(run_id), "s")["status"] == "succeeded"
