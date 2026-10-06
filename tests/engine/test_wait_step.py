"""Wait step (t10): a persisted sleep with no worker held, plus the head_unchanged guard.

Covers c40/h32: N seconds parks the run, a restart mid-wait still resumes once, and a PR head
that moved during the wait ends the run ``superseded`` with no later step run.
"""

from __future__ import annotations

import pytest

from culture_rules.engine.runs import RUNS_COLLECTION, Executor, step_state
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import (
    Clock,
    FakeActor,
    edge,
    port,
    ports_for,
    rule,
    step,
    workflow,
)

SHA_A = "a" * 40
SHA_B = "b" * 40


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock) -> MemoryStore:
    return MemoryStore(clock=clock)


class Heads:
    """A scripted head lookup; records calls."""

    def __init__(self, sha: str | Exception = SHA_A) -> None:
        self.sha = sha
        self.calls: list[tuple] = []

    def __call__(self, actor: str | None, repo: str, number: int) -> str:
        self.calls.append((actor, repo, number))
        if isinstance(self.sha, Exception):
            raise self.sha
        return self.sha


def guard_config(seconds: float = 300) -> dict:
    return {
        "seconds": seconds,
        "guard": {
            "value": "head_unchanged",
            "ref": "inputs.head_sha",
            "repo": "o/r",
            "number": 7,
            "actor": "gh",
        },
    }


def two_step(config: dict):
    return workflow(
        (
            step("w", "wait", config=config, outputs=(port("done", "any", False),)),
            step("after", inputs=(port("done", "any", False),)),
        ),
        (edge("w", "done", "after", "done"),),
        inputs=(port("head_sha", "string", required=False),),
    )


def make(store, clock, actor, heads=None, host="spark") -> Executor:
    return Executor(store, host, ports_for(actor), clock=clock, head_lookup=heads)


def start(ex, config, head=SHA_A):
    r = rule(workflow_inputs={"head_sha": {"$literal": head}})
    return ex.start(r, two_step(config))


def test_wait_parks_without_dispatch_and_resumes_after_seconds(store, clock):
    actor = FakeActor()
    ex = make(store, clock, actor)
    run = start(ex, {"seconds": 60})
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "running"
    assert step_state(doc, "w")["status"] == "sleeping"
    assert actor.calls == []  # nothing invoked: no worker held for the wait

    clock.advance(59)
    ex.run_until_idle()
    assert step_state(ex.run(run["id"]), "w")["status"] == "sleeping"
    assert actor.calls_for("after") == []

    clock.advance(2)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert step_state(doc, "w")["status"] == "succeeded"
    assert doc["status"] == "succeeded"
    assert len(actor.calls_for("after")) == 1
    assert actor.calls_for("w") == []


def test_restart_mid_wait_resumes_once(store, clock):
    actor = FakeActor()
    ex = make(store, clock, actor)
    run = start(ex, {"seconds": 60})
    ex.run_until_idle()
    del ex  # node dies mid-wait
    clock.advance(30)
    other = make(store, clock, actor, host="thor")
    other.run_until_idle()
    assert step_state(other.run(run["id"]), "w")["status"] == "sleeping"
    clock.advance(31)
    other.run_until_idle()
    third = make(store, clock, actor, host="orin")
    third.run_until_idle()
    doc = third.run(run["id"])
    assert doc["status"] == "succeeded"
    assert len(actor.calls_for("after")) == 1
    resumed = [h for h in doc["history"] if h["event"] == "wait_done"]
    assert len(resumed) == 1


def test_guard_unchanged_head_proceeds(store, clock):
    actor, heads = FakeActor(), Heads(SHA_A)
    ex = make(store, clock, actor, heads)
    run = start(ex, guard_config(60))
    ex.run_until_idle()
    clock.advance(61)
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "succeeded"
    assert heads.calls == [("gh", "o/r", 7)]


def test_guard_moved_head_ends_superseded_and_runs_nothing_later(store, clock):
    actor, heads = FakeActor(), Heads(SHA_B)
    ex = make(store, clock, actor, heads)
    run = start(ex, guard_config(60))
    ex.run_until_idle()
    clock.advance(61)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "superseded"
    assert doc["finished_at"] is not None
    assert actor.calls == []
    assert step_state(doc, "after")["status"] == "cancelled"
    # terminal: later ticks change nothing
    clock.advance(600)
    assert ex.run_until_idle() == 0
    assert store.get(RUNS_COLLECTION, run["id"])["status"] == "superseded"


@pytest.mark.parametrize(
    "heads",
    [Heads(RuntimeError("boom")), None],
    ids=["lookup-raises", "no-lookup-configured"],
)
def test_guard_lookup_failure_never_proceeds(store, clock, heads):
    actor = FakeActor()
    ex = make(store, clock, actor, heads)
    run = start(ex, guard_config(60))
    ex.run_until_idle()
    clock.advance(61)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert doc["error"]["code"] == "head_lookup_failed"
    assert actor.calls == []


def test_guard_missing_expected_sha_fails_safe(store, clock):
    actor = FakeActor()
    ex = make(store, clock, actor, Heads(SHA_A))
    run = ex.start(rule(), two_step(guard_config(60)))  # head_sha input not supplied
    ex.run_until_idle()
    clock.advance(61)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert actor.calls == []


def test_wait_inside_loop_body_is_refused(store, clock):
    from culture_rules.engine.runs import RunError

    body = step("w", "wait", config={"seconds": 5})
    loop = step("loop", "retry_until", body=(body,), max_iterations=2, config={"until": {}})
    ex = make(store, clock, FakeActor())
    with pytest.raises(RunError):
        ex.start(rule(), workflow((loop,)))
