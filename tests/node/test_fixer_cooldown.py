"""#39 t2: the built-in ``fixer.cooldown`` rests the level whose try hit capacity_exhausted.

The failed try comes from the step's own run (its verified ``rules.run.failed`` event,
:func:`~culture_rules.actors.lineage.upstream`, stubbed here; the chain test runs it for
real), never from an input; the target is the actor of the agent step that failed. The
queue then reads the cooldown (:mod:`culture_rules.node.actions.fixer_levels`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.actors import lineage
from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions.fixer_cooldown import (
    DEFAULT_COOLDOWN_S,
    FixerCooldownPort,
    capacity_actor,
)
from culture_rules.node.actions.fixer_levels import FIXER_COOLDOWNS, load_ladder
from culture_rules.store.memory import MemoryStore
from tests.node.test_queue_levels import ACTORS, CODEX, QWEN, actor

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
ENDED = NOW - timedelta(seconds=30)
DEADLINE = NOW + timedelta(hours=1)
CONFIG = {
    "builtin": "fixer.cooldown",
    "levels": "fixer_levels",
    "cooldown": "fixer_capacity_cooldown_s",
}
LEVELS = {"levels": "fixer_levels", "unavailable": "fixer_unavailable"}
CAPACITY = "capacity_exhausted: usage limit reached, try later"


def agent_step(step_id="agent", actor="qwen-fixer"):
    placement = {"actor": actor, "machine": None, "requirement": None} if actor else None
    return {"id": step_id, "kind": "ai", "placement": placement, "body": []}


def fix_run(
    *, step_id="agent", actor="qwen-fixer", message=CAPACITY, workflow_id="pr-fix", **extra
):
    """A failed pr-fix try whose agent step (in the fix loop) failed with ``message``."""
    loop = {"id": "fix", "kind": "retry_until", "body": [agent_step(step_id, actor)]}
    return {
        "id": "run-fix",
        "workflow_id": workflow_id,
        "status": "failed",
        "finished_at": ENDED.isoformat(),
        "trigger": {"data": {"repository": "o/r", "number": 7}},
        "workflow": {"definition": {"steps": [{"id": "quiet", "kind": "wait"}, loop]}},
        "inputs": extra.pop("inputs", {}),
        "steps": [
            {"key": "quiet", "def": "quiet", "status": "succeeded", "error": None},
            {"key": "fix", "def": "fix", "status": "failed", "error": {"message": "x"}},
            {
                "key": f"fix[0]/{step_id}",
                "def": step_id,
                "status": "failed",
                "error": {"code": "actor_failed", "message": message},
            },
        ],
        **extra,
    }


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def world(monkeypatch):
    clock = Clock()
    store = MemoryStore(clock=clock)
    store.put_variable("fixer_levels", [QWEN, CODEX], updated_by="t")
    store.put_variable("fixer_unavailable", "next", updated_by="t")
    store.put_variable("fixer_capacity_cooldown_s", 3600, updated_by="t")
    store.put("runs", {"id": "run-cool", "status": "running"})
    for actor_id, pool in ACTORS:
        store.put("actors", actor(actor_id, pool))
    upstream_of: dict = {"run": fix_run()}

    def fake_upstream(st, run, *, failed=False):
        assert failed is True
        assert run.get("id") == "run-cool"  # read from the store: the step's own run
        if upstream_of["run"] is None:
            raise lineage.LineageError("chain_unverified", "not started by a run failing")
        return upstream_of["run"]

    monkeypatch.setattr(lineage, "upstream", fake_upstream)
    return store, clock, upstream_of


def invoke(store, clock, config=CONFIG, repo="o/r", number=7):
    port = FixerCooldownPort(store, clock=clock)
    ctx = InvocationContext(
        run_id="run-cool", step_id="cooldown", kind="code", host="spark2", config=config
    )
    return port.invoke({"repo": repo, "number": number}, "ik", DEADLINE, context=ctx)


def cooldown(store, actor="qwen-fixer"):
    return store.get(FIXER_COOLDOWNS, actor)


def test_a_capacity_failure_rests_its_level_until_the_try_ended_plus_the_cooldown(world):
    store, clock, _ = world
    res = invoke(store, clock)
    assert res.outcome == COMPLETED
    until = (ENDED + timedelta(seconds=3600)).isoformat()
    assert res.output == {
        "retry": True,
        "actor": "qwen-fixer",
        "recorded": True,
        "level": 1,
        "until": until,
    }
    doc = cooldown(store)
    assert doc["until"] == until
    assert doc["reason"] == "capacity_exhausted"
    assert doc["run_id"] == "run-fix"
    assert doc["level"] == 1


def test_the_queue_skips_the_level_while_it_rests_and_takes_it_again_after(world):
    store, clock, _ = world
    invoke(store, clock)
    ladder = load_ladder(store, LEVELS)
    level, why = ladder.resolve(1, NOW)
    assert level.actor == "codex-fixer"
    assert why == ["level 1 (qwen-fixer): capacity_cooldown"]
    later = ENDED + timedelta(seconds=3601)
    assert ladder.resolve(1, later)[0].actor == "qwen-fixer"


def test_the_cooldown_variable_sets_the_length(world):
    store, clock, _ = world
    store.put_variable("fixer_capacity_cooldown_s", 600, updated_by="t")
    res = invoke(store, clock)
    assert res.output["until"] == (ENDED + timedelta(seconds=600)).isoformat()


def test_without_the_variable_the_cooldown_is_an_hour(world):
    store, clock, _ = world
    res = invoke(store, clock, config={k: v for k, v in CONFIG.items() if k != "cooldown"})
    assert DEFAULT_COOLDOWN_S == 3600
    assert res.output["until"] == (ENDED + timedelta(seconds=3600)).isoformat()


@pytest.mark.parametrize("value", [0, -5, True, "3600", 3600.0, 8 * 86400])
def test_a_malformed_cooldown_fails_closed(world, value):
    store, clock, _ = world
    store.put_variable("fixer_capacity_cooldown_s", value, updated_by="t")
    res = invoke(store, clock)
    assert res.outcome == FAILED
    assert res.error.startswith("bad_cooldown")
    assert cooldown(store) is None


def test_a_later_cooldown_is_never_shortened(world):
    store, clock, _ = world
    later = (NOW + timedelta(hours=5)).isoformat()
    store.put(FIXER_COOLDOWNS, {"id": "qwen-fixer", "until": later, "run_id": "older"})
    res = invoke(store, clock)
    assert res.output["until"] == later
    assert res.output["recorded"] is True
    assert cooldown(store)["run_id"] == "older"


@pytest.mark.parametrize("old", ["2026-10-11T08:00:00+00:00", "not a time", "2026-10-11T23:00:00"])
def test_an_ended_or_malformed_cooldown_is_replaced(world, old):
    store, clock, _ = world
    store.put(FIXER_COOLDOWNS, {"id": "qwen-fixer", "until": old})
    res = invoke(store, clock)
    assert res.output["recorded"] is True
    assert cooldown(store)["until"] == (ENDED + timedelta(seconds=3600)).isoformat()


def test_replaying_an_old_failure_records_nothing_new(world):
    store, clock, _ = world
    clock.now = NOW + timedelta(hours=3)  # the try ended long ago: its cooldown is over
    res = invoke(store, clock)
    assert res.outcome == COMPLETED
    assert res.output["recorded"] is False
    assert res.output["why"] == "already_over"


def test_a_failure_in_the_future_counts_from_now(world):
    store, clock, upstream_of = world
    upstream_of["run"] = fix_run(finished_at=(NOW + timedelta(days=1)).isoformat())
    res = invoke(store, clock)
    assert res.output["until"] == (NOW + timedelta(seconds=3600)).isoformat()


@pytest.mark.parametrize("step_id", ["agent", "agent_qwen", "agent_codex"])
def test_the_target_is_the_actor_of_the_agent_step_that_failed(world, step_id):
    store, clock, upstream_of = world
    upstream_of["run"] = fix_run(step_id=step_id, actor="codex-fixer")
    res = invoke(store, clock)
    assert res.output["actor"] == "codex-fixer"
    assert res.output["level"] == 2
    assert cooldown(store, "codex-fixer") is not None
    assert cooldown(store) is None


def test_the_run_input_fixer_is_read_only_when_the_step_names_no_actor(world):
    store, clock, upstream_of = world
    upstream_of["run"] = fix_run(actor="codex-fixer", inputs={"fixer": "qwen-fixer"})
    assert invoke(store, clock).output["actor"] == "codex-fixer"  # the step's actor wins
    upstream_of["run"] = fix_run(actor=None, inputs={"fixer": "codex-fixer"})
    assert capacity_actor(upstream_of["run"]) == "codex-fixer"


def test_an_actor_that_is_no_level_gets_no_cooldown(world):
    store, clock, upstream_of = world
    upstream_of["run"] = fix_run(actor="someone-else")
    res = invoke(store, clock)
    assert res.outcome == COMPLETED  # the story still goes back in line
    assert res.output["recorded"] is False
    assert res.output["why"] == "not_a_level"
    assert store.find(FIXER_COOLDOWNS) == []


def test_without_levels_no_cooldown_is_written(world):
    store, clock, _ = world
    res = invoke(store, clock, config={"builtin": "fixer.cooldown"})
    assert res.output["why"] == "no_levels"
    assert store.find(FIXER_COOLDOWNS) == []


def test_malformed_levels_fail_closed(world):
    store, clock, _ = world
    store.put_variable("fixer_levels", ["level=1 actor=x"], updated_by="t")
    res = invoke(store, clock)
    assert res.outcome == FAILED
    assert res.error.startswith("bad_levels")


@pytest.mark.parametrize(
    ("run", "code"),
    [
        (fix_run(message="timeout: did not finish"), "not_capacity_exhausted"),
        (fix_run(workflow_id="review-commit"), "not_a_fix"),
        (fix_run(actor=None), "bad_actor"),
        (fix_run(actor="bad actor;rm"), "bad_actor"),
        (None, "chain_unverified"),
    ],
)
def test_a_run_that_is_not_a_capacity_failed_try_is_refused(world, run, code):
    store, clock, upstream_of = world
    upstream_of["run"] = run
    res = invoke(store, clock)
    assert res.outcome == FAILED
    assert res.error.startswith(code)
    assert res.retryable is False
    assert store.find(FIXER_COOLDOWNS) == []


def test_an_input_naming_another_pr_is_refused(world):
    store, clock, _ = world
    res = invoke(store, clock, number=8)
    assert res.outcome == FAILED
    assert res.error.startswith("bad_input")


def test_a_capacity_message_on_a_step_that_is_not_an_agent_is_ignored(world):
    store, clock, upstream_of = world
    run = fix_run(message="timeout: no")
    run["steps"][0].update(status="failed", error={"message": CAPACITY})  # the wait step
    upstream_of["run"] = run
    assert invoke(store, clock).error.startswith("not_capacity_exhausted")
