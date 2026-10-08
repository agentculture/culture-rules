"""d21 phase 2 (E3): an agent step with ``require_commit`` fails at once when the agent made
no commit - no gate, no review, no retry - so the attempt ends and hands back once.

Live finding (culture-rules-tester#4): the agent made no commit, but the gate and the Codex
review still ran (Codex approved an empty change) and the push took its no-op path.
"""

from __future__ import annotations

import pytest

from culture_rules.actors.agent import RECORDED, record_bridge_event, redeliver_bridge
from culture_rules.engine.runs import step_state
from culture_rules.model.common import RetryPolicy
from culture_rules.store.memory import MemoryStore
from tests.actors.test_bridge_agent import (
    SHA,
    FakeBridge,
    bridge_result,
    completed_event,
    executor,
    invocation,
    make_actor,
    start,
    token_of,
)
from tests.engine.run_helpers import Clock


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


RETRY = RetryPolicy(max_attempts=3, backoff_s=1.0)


def finish(store, clock, ex, bridge, run_id, **result):
    doc = invocation(store)
    assert record_bridge_event(store, doc["id"], token_of(bridge), completed_event(**result)) == (
        RECORDED
    )
    redeliver_bridge(store, ex)
    ex.run_until_idle()
    return ex.run(run_id)


@pytest.mark.parametrize(
    "result",
    [
        {"status": "no_changes", "head_after": SHA, "commits": []},
        {"status": "uncommitted", "head_after": SHA, "commits": [], "dirty": True},
        {"status": "completed", "head_after": SHA},  # claims a commit, head did not move
        {"status": "completed", "head_after": None},
    ],
    ids=["no_changes", "uncommitted", "head_unmoved", "no_head_after"],
)
def test_no_commit_fails_the_step_at_once_without_a_retry(store, clock, result):
    bridge = FakeBridge()
    ex = executor(store, clock, make_actor(store, clock, bridge))
    run_id = start(ex, config={"require_commit": True}, retry=RETRY)
    run = finish(store, clock, ex, bridge, run_id, **result)
    fix = step_state(run, "fix")
    assert fix["status"] == "failed"
    assert fix["error"]["message"].startswith("no_changes: ")
    assert fix["attempt"] == 1 and len(bridge.requests) == 1  # never asked again
    assert run["status"] == "failed"
    assert invocation(store)["require_commit"] is True


def test_a_commit_passes(store, clock):
    bridge = FakeBridge()
    ex = executor(store, clock, make_actor(store, clock, bridge))
    run_id = start(ex, config={"require_commit": True})
    run = finish(store, clock, ex, bridge, run_id)
    assert step_state(run, "fix")["status"] == "succeeded"
    assert run["status"] == "succeeded"


def test_without_the_flag_no_changes_completes_as_before(store, clock):
    # the read-only reviewer legitimately changes nothing
    bridge = FakeBridge()
    ex = executor(store, clock, make_actor(store, clock, bridge))
    run_id = start(ex)
    run = finish(store, clock, ex, bridge, run_id, status="no_changes", head_after=SHA, commits=[])
    assert step_state(run, "fix")["status"] == "succeeded"


def test_a_synchronous_answer_is_held_to_it_too(store, clock):
    answer = {
        "invocation_id": "inv-1",
        "result": bridge_result(status="no_changes", head_after=SHA),
    }
    bridge = FakeBridge((200, answer))
    ex = executor(store, clock, make_actor(store, clock, bridge))
    run_id = start(ex, config={"require_commit": True})
    fix = step_state(ex.run(run_id), "fix")
    assert fix["status"] == "failed" and fix["error"]["message"].startswith("no_changes: ")


def test_a_non_boolean_flag_is_not_a_requirement_but_is_refused(store, clock):
    bridge = FakeBridge()
    ex = executor(store, clock, make_actor(store, clock, bridge))
    run_id = start(ex, config={"require_commit": "yes"})
    fix = step_state(ex.run(run_id), "fix")
    assert fix["status"] == "failed" and "require_commit" in fix["error"]["message"]
    assert bridge.requests == []
