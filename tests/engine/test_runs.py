"""Run executor (t12): persisted state machine runs over the ActorPort seam.

Covers c5/h5 (persisted, resumable runs), c84/h65 (pinned versions), c83/h64 (timeouts,
retries, lost acks), c88/h69 (pause, drain, cancel, audited) and c11/h11 (per-step
placement, typed outputs -> typed inputs).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from culture_rules.engine import runs as runs_mod
from culture_rules.engine.actorport import (
    ACCEPTED,
    BLOCKED,
    COMPLETED,
    FAILED,
    OUTCOMES,
    ActorPort,
    InvocationResult,
)
from culture_rules.engine.audit import AUDIT_COLLECTION, MUTATING_VERBS, AuditLog
from culture_rules.engine.claims import idempotency_key
from culture_rules.engine.matching import match
from culture_rules.engine.runs import (
    ACTION_STEP,
    RUNS_COLLECTION,
    Containment,
    Executor,
    RunError,
    is_paused,
    step_key,
    step_state,
)
from culture_rules.model.action import Action
from culture_rules.model.common import RetryPolicy
from culture_rules.model.placement import Placement
from culture_rules.model.workflow import Output
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import (
    Clock,
    Crash,
    FakeActor,
    edge,
    enrol_online,
    machine,
    port,
    ports_for,
    rule,
    step,
    workflow,
)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock) -> MemoryStore:
    return MemoryStore(clock=clock)


@pytest.fixture
def actor() -> FakeActor:
    return FakeActor()


def make_executor(store, actor, clock, host="spark", **kw) -> Executor:
    return Executor(store, host, ports_for(actor), clock=clock, **kw)


def three_step_human_workflow(version: int = 1, s3_config: dict | None = None):
    return workflow(
        (
            step("s1", outputs=(port("n", "integer"),)),
            step(
                "s2", "actor_task", inputs=(port("n", "integer"),), outputs=(port("ok", "boolean"),)
            ),
            step("s3", inputs=(port("ok", "boolean"),), config=s3_config or {"v": version}),
        ),
        (edge("s1", "n", "s2", "n"), edge("s2", "ok", "s3", "ok")),
        version=version,
    )


def human_actor() -> FakeActor:
    a = FakeActor(default=lambda inp, ctx: {"n": 7} if ctx.step_id == "s1" else {})
    a.on("s2", ("accept",))
    return a


# ---------------------------------------------------------------- actor port seam


def test_actorport_outcomes_and_protocol():
    assert set(OUTCOMES) == {ACCEPTED, COMPLETED, FAILED, BLOCKED}
    assert InvocationResult.completed({"x": 1}).outcome == COMPLETED
    assert InvocationResult.accepted().outcome == ACCEPTED
    assert InvocationResult.failed("boom", retryable=False).retryable is False
    assert InvocationResult.blocked("over budget").outcome == BLOCKED
    assert isinstance(FakeActor(), ActorPort)
    with pytest.raises(ValueError):
        InvocationResult(outcome="maybe")


def test_round_trip_result_dict():
    r = InvocationResult.completed({"a": [1]})
    assert InvocationResult.from_dict(r.to_dict()) == r


# ---------------------------------------------------------------- c5 / h5 persistence


def test_simple_run_succeeds_and_every_transition_is_persisted(store, actor, clock):
    ex = make_executor(store, actor, clock)
    head = store.head(RUNS_COLLECTION)
    run = ex.start(rule(), workflow((step("a"), step("b"))))
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    revs = [h["rev"] for h in doc["history"]]
    assert revs == list(range(1, len(revs) + 1))
    assert doc["rev"] == revs[-1]
    # every transition is its own committed write to the runs collection
    feed = [c for c in store.changes(RUNS_COLLECTION, head) if c.id == run["id"]]
    assert len(feed) == len(revs)
    assert [c.document["rev"] for c in feed] == revs


def test_engine_holds_no_run_state_in_memory(store, actor, clock):
    run = make_executor(store, actor, clock).start(rule(), workflow((step("a"), step("b"))))
    for _ in range(20):  # a brand-new engine instance for every tick
        make_executor(store, actor, clock).tick()
    assert store.get(RUNS_COLLECTION, run["id"])["status"] == "succeeded"
    assert len(actor.calls_for("a")) == 1
    assert len(actor.calls_for("b")) == 1


def test_restart_with_pending_human_step_resumes_without_reexecuting(store, clock):
    a = human_actor()
    ex = make_executor(store, a, clock)
    run = ex.start(rule(), three_step_human_workflow())
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert step_state(doc, "s1")["status"] == "succeeded"
    assert step_state(doc, "s2")["status"] == "waiting"
    assert doc["status"] == "running"
    del ex  # the engine process dies

    restarted = make_executor(store, a, clock, host="spark")
    key = idempotency_key(run["id"], "s2")
    assert restarted.deliver(key, InvocationResult.completed({"ok": True})) is True
    restarted.run_until_idle()
    doc = restarted.run(run["id"])
    assert doc["status"] == "succeeded"
    assert a.effects_for("s1") == 1
    assert len(a.calls_for("s1")) == 1
    assert a.effects_for("s2") == 1
    assert a.effects_for("s3") == 1
    assert a.calls_for("s3")[0][1] == {"ok": True}


def test_kill_mid_dispatch_resumes_after_lease_with_single_side_effect(store, clock):
    a = FakeActor().on("b", ("crash", {"v": 1}))
    ex = make_executor(store, a, clock, lease=timedelta(seconds=30))
    run = ex.start(rule(), workflow((step("a"), step("b"))))
    with pytest.raises(Crash):
        ex.run_until_idle()
    assert step_state(store.get(RUNS_COLLECTION, run["id"]), "b")["status"] == "dispatching"

    other = make_executor(store, a, clock, host="spark")
    other.run_until_idle()  # lease still live: nobody re-dispatches
    assert len(a.calls_for("b")) == 1
    clock.advance(31)
    other.run_until_idle()
    doc = other.run(run["id"])
    assert doc["status"] == "succeeded"
    assert a.effects_for("b") == 1  # re-invoked with the same key, deduplicated
    keys = {c[0] for c in a.calls_for("b")}
    assert keys == {idempotency_key(run["id"], "b")}
    assert step_state(doc, "b")["outputs"] == {"v": 1}


def test_deliver_unknown_key_returns_false(store, actor, clock):
    assert make_executor(store, actor, clock).deliver("ik1:nope", InvocationResult.accepted()) is (
        False
    )


# ---------------------------------------------------------------- c84 / h65 pinning


def _store_definitions(store, wf, r):
    store.put("workflows", {"id": wf.id, **wf.to_dict()})
    store.put("rules", {"id": r.id, **r.to_dict()})


def test_edit_mid_run_leaves_run_on_old_version_and_new_runs_on_new(store, clock):
    a = human_actor()
    ex = make_executor(store, a, clock)
    _store_definitions(store, three_step_human_workflow(1), rule())
    old = ex.start_from_store("r1")
    ex.run_until_idle()
    assert old["workflow"]["version"] == 1
    assert old["rule"]["id"] == "r1"
    assert old["rule"]["digest"]

    # edit the workflow while the human step is pending
    _store_definitions(store, three_step_human_workflow(2, {"v": 2, "edited": True}), rule())
    a.on("s2", ("accept",))
    new = ex.start_from_store("r1")
    assert new["workflow"]["version"] == 2
    ex.run_until_idle()

    assert ex.deliver(idempotency_key(old["id"], "s2"), InvocationResult.completed({"ok": True}))
    assert ex.deliver(idempotency_key(new["id"], "s2"), InvocationResult.completed({"ok": False}))
    ex.run_until_idle()
    s3_calls = {c[2].run_id: c[2].config for c in a.calls_for("s3")}
    assert s3_calls[old["id"]] == {"v": 1}
    assert s3_calls[new["id"]] == {"v": 2, "edited": True}
    old_doc = ex.run(old["id"])
    assert old_doc["workflow"]["version"] == 1
    assert old_doc["workflow"]["definition"]["steps"][2]["config"] == {"v": 1}


def test_rule_digest_changes_when_rule_changes(store, actor, clock):
    ex = make_executor(store, actor, clock)
    wf = workflow((step("a"),))
    d1 = ex.start(rule(), wf)["rule"]["digest"]
    d2 = ex.start(rule(action=Action(kind="message")), wf)["rule"]["digest"]
    assert d1 != d2


def test_rule_pinning_an_unavailable_workflow_version_is_refused(store, actor, clock):
    _store_definitions(store, three_step_human_workflow(2), rule(version=1))
    ex = make_executor(store, actor, clock)
    with pytest.raises(RunError) as exc:
        ex.start_from_store("r1")
    assert exc.value.code == "workflow_version_unavailable"


def test_start_from_store_unknown_or_deleted_rule(store, actor, clock):
    ex = make_executor(store, actor, clock)
    with pytest.raises(RunError) as exc:
        ex.start_from_store("ghost")
    assert exc.value.code == "rule_not_found"
    _store_definitions(store, three_step_human_workflow(1), rule())
    store.update_if("rules", "r1", {}, {"deleted_at": "2026-01-01T00:00:00+00:00"})
    with pytest.raises(RunError) as exc:
        ex.start_from_store("r1")
    assert exc.value.code == "not_fireable"


def _store_raw_rule(store, *, action: dict, trigger: dict | None = None) -> None:
    """Store a rule as an older engine would have written it (pre-catalog shapes)."""
    data = {"id": "r1", **rule(workflow_id=None).to_dict()}
    data["action"] = action
    if trigger is not None:
        data["trigger"] = trigger
    store.put("rules", data)


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "comment", "params": {"body": "hi"}},
        {"kind": "github.comment", "params": {"body": "hi", "repo": "o/r"}},
    ],
)
def test_stored_rule_with_pre_catalog_action_still_starts(store, actor, clock, action):
    _store_raw_rule(store, action=action)
    ex = make_executor(store, actor, clock)
    run = ex.start_from_store("r1")
    assert run["rule"]["definition"]["action"]["kind"] == action["kind"]
    ex.run_until_idle()
    assert step_state(ex.run(run["id"]), ACTION_STEP) is not None


def test_stored_typeless_event_rule_can_still_be_started_manually(store, actor, clock):
    _store_raw_rule(store, action={"kind": "noop"}, trigger={"kind": "event", "params": {}})
    ex = make_executor(store, actor, clock)
    assert ex.start_from_store("r1")["rule_id"] == "r1"


def test_fresh_rule_with_unknown_action_kind_is_still_refused(store, actor, clock):
    ex = make_executor(store, actor, clock)
    fresh = rule(workflow_id=None, action=Action(kind="teleport"))
    with pytest.raises(RunError) as exc:
        ex.start(fresh)
    assert exc.value.code == "invalid_rule"


# ---------------------------------------------------------------- c83 / h64 timeouts, retries


def test_timeout_is_retried_per_policy_with_the_same_key_then_fails(store, clock):
    a = FakeActor(idempotent=False)
    a.supports_idempotency_key = True  # target dedupes by key itself
    a.on("h", ("accept",), ("accept",), ("accept",))
    ex = make_executor(store, a, clock)
    wf = workflow(
        (step("h", "actor_task", timeout_s=10, retry=RetryPolicy(max_attempts=3, backoff_s=5)),)
    )
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    assert len(a.calls_for("h")) == 1
    clock.advance(11)
    ex.run_until_idle()
    st = step_state(ex.run(run["id"]), "h")
    assert st["status"] == "retry_wait"
    assert st["attempt"] == 1
    assert len(a.calls_for("h")) == 1  # backoff not yet elapsed
    clock.advance(5)
    ex.run_until_idle()
    assert len(a.calls_for("h")) == 2
    clock.advance(11)  # second attempt times out ...
    ex.run_until_idle()
    clock.advance(5)  # ... and is retried after the (constant) backoff
    ex.run_until_idle()
    assert len(a.calls_for("h")) == 3
    clock.advance(11)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert step_state(doc, "h")["error"]["code"] == "timeout"
    assert len(a.calls_for("h")) == 3
    assert {c[0] for c in a.calls_for("h")} == {idempotency_key(run["id"], "h")}
    assert [c[2].attempt for c in a.calls_for("h")] == [1, 2, 3]


def test_deadline_passed_to_actor_is_now_plus_timeout(store, actor, clock):
    ex = make_executor(store, actor, clock)
    ex.start(rule(), workflow((step("a", timeout_s=42),)))
    ex.run_until_idle()
    assert actor.calls_for("a")[0][3] == clock.now + timedelta(seconds=42)


def test_backoff_multiplier(store, clock):
    a = FakeActor().on("a", ("fail", "x", True), ("fail", "x", True))
    ex = make_executor(store, a, clock)
    wf = workflow(
        (step("a", retry=RetryPolicy(max_attempts=3, backoff_s=2, backoff_multiplier=3)),)
    )
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    assert (
        step_state(ex.run(run["id"]), "a")["next_attempt_at"]
        == (clock.now + timedelta(seconds=2)).isoformat()
    )
    clock.advance(2)
    ex.run_until_idle()
    assert (
        step_state(ex.run(run["id"]), "a")["next_attempt_at"]
        == (clock.now + timedelta(seconds=6)).isoformat()
    )
    clock.advance(6)
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "succeeded"
    assert len(a.calls_for("a")) == 3


def test_non_retryable_failure_fails_without_retry(store, clock):
    a = FakeActor().on("a", ("fail", "bad input", False))
    ex = make_executor(store, a, clock)
    run = ex.start(rule(), workflow((step("a", retry=RetryPolicy(max_attempts=5)), step("b"))))
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert step_state(doc, "a")["error"] == {"code": "actor_failed", "message": "bad input"}
    assert len(a.calls_for("a")) == 1
    assert a.calls_for(ACTION_STEP) == []


def test_lost_ack_on_step_is_not_executed_twice(store, clock):
    a = FakeActor().on("a", ("lose_ack", {"x": 1}))
    ex = make_executor(store, a, clock)
    run = ex.start(rule(), workflow((step("a", retry=RetryPolicy(max_attempts=2)),)))
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    assert len(a.calls_for("a")) == 2
    assert a.effects_for("a") == 1
    assert step_state(doc, "a")["outputs"] == {"x": 1}


def test_lost_ack_on_rule_action_is_not_executed_twice(store, clock):
    a = FakeActor().on(ACTION_STEP, ("lose_ack", {"posted": True}))
    ex = make_executor(store, a, clock)
    act = Action(kind="noop", params={"body": "hi"}, retry=RetryPolicy(max_attempts=3))
    run = ex.start(rule(workflow_id=None, action=act), None)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    assert a.effects_for(ACTION_STEP) == 1
    assert len(a.calls_for(ACTION_STEP)) == 2
    assert a.calls_for(ACTION_STEP)[0][2].kind == "action"
    assert a.calls_for(ACTION_STEP)[0][1] == {"body": "hi"}


def test_target_without_keys_is_never_blindly_retried(store, clock):
    a = FakeActor(idempotent=False).on(ACTION_STEP, ("lose_ack", {}))
    act = Action(kind="mesh.message", retry=RetryPolicy(max_attempts=3), idempotent=False)
    ex = make_executor(store, a, clock)
    run = ex.start(rule(workflow_id=None, action=act), None)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert step_state(doc, ACTION_STEP)["error"]["code"] == "unsafe_retry"
    assert a.effects_for(ACTION_STEP) == 1
    assert len(a.calls_for(ACTION_STEP)) == 1


def test_idempotent_action_without_key_support_may_retry(store, clock):
    a = FakeActor(idempotent=False).on(ACTION_STEP, ("fail", "flaky", True))
    act = Action(kind="noop", retry=RetryPolicy(max_attempts=2), idempotent=True)
    ex = make_executor(store, a, clock)
    run = ex.start(rule(workflow_id=None, action=act), None)
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "succeeded"


def test_blocked_is_redispatched_without_consuming_an_attempt(store, clock):
    a = FakeActor().on("a", ("block", "at cap"), ("block", "at cap"))
    ex = make_executor(store, a, clock)
    run = ex.start(rule(), workflow((step("a", timeout_s=100),)))
    ex.run_until_idle()
    st = step_state(ex.run(run["id"]), "a")
    assert st["status"] == "blocked"
    assert st["attempt"] == 1
    clock.advance(runs_mod.BLOCKED_RETRY_S)
    ex.run_until_idle()
    clock.advance(runs_mod.BLOCKED_RETRY_S * 2)  # the re-ask backs off (5 s, then 10 s)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    assert step_state(doc, "a")["attempt"] == 1
    assert len(a.calls_for("a")) == 3


def test_late_completion_from_earlier_attempt_is_accepted_once(store, clock):
    a = FakeActor().on("h", ("accept",))
    ex = make_executor(store, a, clock)
    wf = workflow((step("h", "actor_task", timeout_s=10, retry=RetryPolicy(max_attempts=2)),))
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    clock.advance(11)
    ex.run_until_idle()  # timed out -> retried (same key; the fake dedupes as accepted)
    key = idempotency_key(run["id"], "h")
    assert ex.deliver(key, InvocationResult.completed({})) is True
    assert ex.deliver(key, InvocationResult.completed({})) is False  # already recorded
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "succeeded"


# ---------------------------------------------------------------- loops


def test_for_each_runs_body_per_item_within_max(store, clock):
    a = FakeActor(default=lambda inp, ctx: {"y": inp.get("item", 0) * 10})
    loop = step(
        "each",
        "for_each",
        inputs=(port("items", "array"),),
        outputs=(port("results", "array"),),
        max_iterations=5,
        body=(step("dbl", inputs=(port("item", "integer"),), outputs=(port("y", "integer"),)),),
    )
    wf = workflow(
        (step("src", outputs=(port("items", "array"),)), loop),
        (edge("src", "items", "each", "items"),),
        outputs=(Output(name="all", type="array", source="steps.each.outputs.results"),),
    )
    a.on("src", ("complete", {"items": [1, 2, 3]}))
    ex = make_executor(store, a, clock)
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    body_calls = [c for c in a.calls if c[2].step_id.startswith("each[")]
    assert [c[1]["item"] for c in body_calls] == [1, 2, 3]
    assert {c[2].step_id for c in body_calls} == {step_key("each", i, "dbl") for i in range(3)}
    assert len({c[0] for c in body_calls}) == 3  # one idempotency key per iteration
    assert doc["outputs"] == {"all": [{"y": 10}, {"y": 20}, {"y": 30}]}


def test_for_each_over_max_never_runs_beyond_max(store, clock):
    a = FakeActor().on("src", ("complete", {"items": list(range(6))}))
    loop = step(
        "each",
        "for_each",
        inputs=(port("items", "array"),),
        max_iterations=5,
        body=(step("b", inputs=(port("item", "any"),)),),
    )
    wf = workflow(
        (step("src", outputs=(port("items", "array"),)), loop),
        (edge("src", "items", "each", "items"),),
    )
    ex = make_executor(store, a, clock)
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert step_state(doc, "each")["error"]["code"] == "loop_max_exceeded"
    assert len([c for c in a.calls if c[2].step_id.startswith("each[")]) <= 5


def _retry_until(max_iterations: int):
    return workflow(
        (
            step(
                "poll",
                "retry_until",
                outputs=(port("ready", "boolean"),),
                max_iterations=max_iterations,
                config={
                    "until": {
                        "op": "compare",
                        "cmp": "==",
                        "left": {"field": "ready"},
                        "right": {"literal": True},
                    }
                },
                body=(step("check", outputs=(port("ready", "boolean"),)),),
            ),
        )
    )


def test_retry_until_stops_when_condition_holds(store, clock):
    a = FakeActor()
    a.on(step_key("poll", 0, "check"), ("complete", {"ready": False}))
    a.on(step_key("poll", 1, "check"), ("complete", {"ready": True}))
    ex = make_executor(store, a, clock)
    run = ex.start(rule(), _retry_until(4))
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    assert len([c for c in a.calls if c[2].step_id.startswith("poll[")]) == 2
    assert step_state(doc, "poll")["outputs"] == {"ready": True}


def test_retry_until_never_exceeds_max(store, clock):
    a = FakeActor(default=lambda inp, ctx: {"ready": False})
    ex = make_executor(store, a, clock)
    run = ex.start(rule(), _retry_until(4))
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert step_state(doc, "poll")["error"]["code"] == "loop_max_exceeded"
    assert len([c for c in a.calls if c[2].step_id.startswith("poll[")]) == 4


def test_retry_until_carry_feeds_the_previous_result_into_the_next_iteration(store, clock):
    a = FakeActor()
    a.on(step_key("poll", 0, "work"), ("complete", {"note": "first"}))
    a.on(step_key("poll", 0, "check"), ("complete", {"ready": False, "hint": "try harder"}))
    a.on(step_key("poll", 1, "work"), ("complete", {"note": "second"}))
    a.on(step_key("poll", 1, "check"), ("complete", {"ready": True, "hint": None}))
    wf = workflow(
        (
            step(
                "poll",
                "retry_until",
                inputs=(port("task", "string"),),
                outputs=(port("ready", "boolean"),),
                max_iterations=3,
                config={
                    "until": {
                        "op": "compare",
                        "cmp": "==",
                        "left": {"field": "ready"},
                        "right": {"literal": True},
                    },
                    "carry": {"task": "hint"},
                },
                body=(
                    step("work", inputs=(port("task", "string"),), outputs=(port("note"),)),
                    step("check", outputs=(port("ready", "boolean"), port("hint", required=False))),
                ),
            ),
        ),
        (edge("inputs", "task", "poll", "task"),),
        inputs=(port("task", "string"),),
    )
    ex = make_executor(store, a, clock)
    run = ex.start(rule(workflow_inputs={"task": "trigger.task"}), wf, trigger={"task": "do it"})
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "succeeded"
    tasks = [c[1]["task"] for c in a.calls if c[2].step_id.endswith("/work")]
    assert tasks == ["do it", "try harder"]


# ---------------------------------------------------------------- c88 / h69 containment


def _audit(store, verb):
    return [e for e in store.find(AUDIT_COLLECTION) if e["verb"] == verb]


def test_global_pause_fires_nothing_and_is_audited(store, actor, clock):
    c = Containment(store, clock=clock)
    c.pause("alice")
    assert is_paused(store)
    ex = make_executor(store, actor, clock)
    paused_rule, wf = rule(), workflow((step("a"),))
    with pytest.raises(RunError) as exc:
        ex.start(paused_rule, wf)
    assert exc.value.code == "paused"
    decisions = match({"kind": "manual"}, [rule()], paused=is_paused(store))
    assert decisions
    assert not any(d.fire for d in decisions)
    assert store.find(RUNS_COLLECTION) == []
    (entry,) = _audit(store, "engine.pause")
    assert entry["identity"] == "alice"
    c.resume("bob")
    assert not is_paused(store)
    assert _audit(store, "engine.resume")[0]["identity"] == "bob"
    ex.start(rule(), workflow((step("a"),)))


def test_pause_halts_dispatch_of_in_flight_runs_until_resumed(store, actor, clock):
    ex = make_executor(store, actor, clock)
    run = ex.start(rule(), workflow((step("a"),)))
    c = Containment(store, clock=clock)
    c.pause("alice")
    ex.run_until_idle()
    assert actor.calls == []
    c.resume("alice")
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "succeeded"


def test_pause_twice_is_an_error(store, clock):
    c = Containment(store, clock=clock)
    c.pause("alice")
    with pytest.raises(RunError):
        c.pause("alice")
    assert len(_audit(store, "engine.pause")) == 1


def test_cancel_stops_run_ignores_late_results_and_is_audited(store, clock):
    a = human_actor()
    ex = make_executor(store, a, clock)
    run = ex.start(rule(), three_step_human_workflow())
    ex.run_until_idle()
    Containment(store, clock=clock).cancel(run["id"], "carol", reason="runaway")
    doc = ex.run(run["id"])
    assert doc["status"] == "cancelled"
    assert step_state(doc, "s2")["status"] == "cancelled"
    assert step_state(doc, "s3")["status"] == "cancelled"
    assert ex.deliver(
        idempotency_key(run["id"], "s2"), InvocationResult.completed({"ok": True})
    ) is (False)
    ex.run_until_idle()
    assert a.calls_for("s3") == []
    (entry,) = _audit(store, "runs.cancel")
    assert entry["identity"] == "carol"
    assert entry["target"] == {"collection": RUNS_COLLECTION, "id": run["id"]}
    containment = Containment(store, clock=clock)
    with pytest.raises(RunError):
        containment.cancel(run["id"], "carol")
    with pytest.raises(RunError):
        containment.cancel("missing", "carol")


def test_run_start_is_audited_with_identity(store, actor, clock):
    run = make_executor(store, actor, clock).start(rule(), workflow((step("a"),)), identity="dave")
    (entry,) = _audit(store, "runs.start")
    assert entry["identity"] == "dave"
    assert entry["target"]["id"] == run["id"]
    make_executor(store, actor, clock).start(rule(), workflow((step("a"),)))
    assert {e["identity"] for e in _audit(store, "runs.start")} == {"dave", "engine@spark"}


def test_drain_moves_new_placements_off_the_host_and_running_steps_finish(store, clock):
    enrol_online(store, clock, machine("spark2", "gpu"), machine("thor", "gpu"))
    a = FakeActor()
    a.on("first", ("accept",))
    gpu = Placement(requirement=("gpu",))
    wf = workflow(
        (
            step("first", "actor_task", placement=gpu, outputs=(port("done", "boolean"),)),
            step("second", placement=gpu, inputs=(port("done", "boolean"),)),
        ),
        (edge("first", "done", "second", "done"),),
    )
    engines = {h: make_executor(store.peer(), a, clock, host=h) for h in ("spark2", "thor")}
    run = engines["spark2"].start(rule(), wf)
    for _ in range(3):
        for e in engines.values():
            e.tick()
    doc = store.get(RUNS_COLLECTION, run["id"])
    assert step_state(doc, "first")["host"] == "spark2"  # first eligible by name
    assert step_state(doc, "first")["status"] == "waiting"

    c = Containment(store, clock=clock)
    c.drain("spark2", "erin")
    assert _audit(store, "machine.drain")[0]["identity"] == "erin"
    # the running step on the drained host still finishes
    assert engines["spark2"].deliver(
        idempotency_key(run["id"], "first"), InvocationResult.completed({"done": True})
    )
    for _ in range(3):
        for e in engines.values():
            e.tick()
    doc = store.get(RUNS_COLLECTION, run["id"])
    assert doc["status"] == "succeeded"
    assert step_state(doc, "second")["host"] == "thor"
    assert a.calls_for("second")[0][2].host == "thor"
    c.undrain("spark2", "erin")
    assert _audit(store, "machine.undrain")[0]["identity"] == "erin"
    assert runs_mod.drained_machines(store) == set()


def test_drained_engine_takes_no_unplaced_steps(store, actor, clock):
    Containment(store, clock=clock).drain("spark", "erin")
    drained = make_executor(store, actor, clock, host="spark")
    run = drained.start(rule(), workflow((step("a"),)))
    drained.run_until_idle()
    assert actor.calls == []
    make_executor(store, actor, clock, host="thor").run_until_idle()
    assert actor.calls[0][2].host == "thor"
    assert store.get(RUNS_COLLECTION, run["id"])["status"] == "succeeded"


# ---------------------------------------------------------------- c11 / h11 placement + types


def test_three_hosts_each_step_on_its_placement_with_typed_values(store, clock):
    enrol_online(store, clock, machine("spark"), machine("thor"), machine("spark2"))
    actors = {h: FakeActor() for h in ("spark", "thor", "spark2")}
    actors["spark"].on("s1", ("complete", {"n": 2}))
    actors["thor"].default = lambda inp, ctx: {"x": inp["n"] * 1.5} if "n" in inp else {}
    actors["spark2"].default = lambda inp, ctx: {"msg": f"got {inp['x']}"} if "x" in inp else {}
    wf = workflow(
        (
            step("s1", placement=Placement(machine="spark"), outputs=(port("n", "integer"),)),
            step(
                "s2",
                "ai",
                placement=Placement(machine="thor"),
                inputs=(port("n", "number"),),
                outputs=(port("x", "number"),),
            ),
            step(
                "s3",
                placement=Placement(machine="spark2"),
                inputs=(port("x", "number"),),
                outputs=(port("msg", "string"),),
            ),
        ),
        (edge("s1", "n", "s2", "n"), edge("s2", "x", "s3", "x")),
        outputs=(Output(name="msg", type="string", source="steps.s3.outputs.msg"),),
    )
    engines = [
        Executor(store.peer(), h, ports_for(actors[h]), clock=clock)
        for h in ("spark", "thor", "spark2")
    ]
    run = engines[0].start(rule(), wf)
    for _ in range(6):
        for e in reversed(engines):
            e.tick()
    doc = store.get(RUNS_COLLECTION, run["id"])
    assert doc["status"] == "succeeded"
    assert [step_state(doc, s)["host"] for s in ("s1", "s2", "s3")] == ["spark", "thor", "spark2"]
    steps_on = {
        h: [c[2].step_id for c in a.calls if c[2].step_id != ACTION_STEP] for h, a in actors.items()
    }
    assert steps_on == {"spark": ["s1"], "thor": ["s2"], "spark2": ["s3"]}
    assert sum(len(a.calls_for(ACTION_STEP)) for a in actors.values()) == 1
    assert actors["thor"].calls[0][1] == {"n": 2}
    assert actors["spark2"].calls[0][1] == {"x": 3.0}
    assert doc["outputs"] == {"msg": "got 3.0"}


def test_static_port_type_mismatch_fails_validation_at_start(store, actor, clock):
    wf = workflow(
        (
            step("a", outputs=(port("s", "string"),)),
            step("b", inputs=(port("n", "integer"),)),
        ),
        (edge("a", "s", "b", "n"),),
    )
    ex, r = make_executor(store, actor, clock), rule()
    with pytest.raises(RunError) as exc:
        ex.start(r, wf)
    assert exc.value.code == "invalid_workflow"
    assert any(e["code"] == "port_type_mismatch" for e in exc.value.details)
    assert store.find(RUNS_COLLECTION) == []


def test_runtime_output_type_mismatch_fails_step_and_stops_downstream(store, clock):
    a = FakeActor().on("a", ("complete", {"n": "seven"}))
    wf = workflow(
        (
            step("a", outputs=(port("n", "integer"),), retry=RetryPolicy(max_attempts=3)),
            step("b", inputs=(port("n", "integer"),)),
        ),
        (edge("a", "n", "b", "n"),),
    )
    ex = make_executor(store, a, clock)
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert step_state(doc, "a")["error"]["code"] == "output_type_mismatch"
    assert len(a.calls_for("a")) == 1  # deterministic: not retried
    assert a.calls_for("b") == []


def test_workflow_inputs_are_mapped_from_the_trigger_and_type_checked(store, actor, clock):
    wf = workflow(
        (step("a", inputs=(port("n", "integer"),)),),
        (edge("inputs", "n", "a", "n"),),
        inputs=(port("n", "integer"),),
    )
    r = rule(workflow_inputs={"n": "trigger.data.number"})
    ex = make_executor(store, actor, clock)
    ex.start(r, wf, trigger={"data": {"number": 5}})
    ex.run_until_idle()
    assert actor.calls_for("a")[0][1] == {"n": 5}
    with pytest.raises(RunError) as exc:
        ex.start(r, wf, trigger={"data": {"number": "five"}})
    assert exc.value.code == "input_type_mismatch"
    with pytest.raises(RunError) as exc:
        ex.start(r, wf, trigger={})
    assert exc.value.code == "input_missing"


def test_structured_literal_that_looks_like_a_reference_resolves_to_the_literal(
    store, actor, clock
):
    wf = workflow(
        (step("a", inputs=(port("n", "string"), port("m", "integer"))),),
        (edge("inputs", "n", "a", "n"), edge("inputs", "m", "a", "m")),
        inputs=(port("n", "string"), port("m", "integer")),
    )
    r = rule(
        workflow_inputs={
            "n": {"$literal": "trigger.data.number"},
            "m": {"$ref": "trigger.data.number"},
        }
    )
    ex = make_executor(store, actor, clock)
    ex.start(r, wf, trigger={"data": {"number": 5}})
    ex.run_until_idle()
    assert actor.calls_for("a")[0][1] == {"n": "trigger.data.number", "m": 5}


def test_action_params_resolve_workflow_outputs(store, clock):
    a = FakeActor().on("a", ("complete", {"n": 3}))
    wf = workflow(
        (step("a", outputs=(port("n", "integer"),)),),
        outputs=(Output(name="count", type="integer", source="steps.a.outputs.n"),),
    )
    act = Action(
        kind="mesh.message",
        params={"n": "workflow.outputs.count", "text": "n={{ workflow.outputs.count }}"},
    )
    ex = make_executor(store, a, clock)
    run = ex.start(rule(action=act), wf)
    ex.run_until_idle()
    assert a.calls_for(ACTION_STEP)[0][1] == {"n": 3, "text": "n=3"}
    assert ex.run(run["id"])["outputs"] == {"count": 3}


def test_unknown_placement_target_fails_the_step(store, actor, clock):
    wf = workflow((step("a", placement=Placement(machine="nowhere")),))
    ex = make_executor(store, actor, clock)
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert step_state(doc, "a")["error"]["code"] == "placement.machine_unknown"


def test_offline_placement_waits_instead_of_failing(store, actor, clock):
    enrol_online(store, clock, machine("thor"))
    clock.advance(120)  # heartbeat goes stale
    wf = workflow((step("a", placement=Placement(machine="thor")),))
    ex = make_executor(store, actor, clock, host="thor")
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "running"
    assert step_state(doc, "a")["status"] == "pending"
    assert step_state(doc, "a")["placement_error"]["code"] == "placement.machine_offline"
    assert actor.calls == []


def test_disabled_step_is_skipped(store, actor, clock):
    ex = make_executor(store, actor, clock)
    run = ex.start(rule(), workflow((step("a", enabled=False), step("b"))))
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    assert step_state(doc, "a")["status"] == "skipped"
    assert actor.calls_for("a") == []


def test_missing_port_for_kind_fails_step(store, clock):
    a = FakeActor()
    ex = Executor(store, "spark", {"code": a}, clock=clock)
    run = ex.start(rule(), workflow((step("x", "ai"),)))
    ex.run_until_idle()
    assert step_state(ex.run(run["id"]), "x")["error"]["code"] == "no_actor_port"


def test_new_verbs_are_registered_mutating_verbs():
    for verb in (
        "runs.start",
        "runs.cancel",
        "engine.pause",
        "engine.resume",
        "machine.drain",
        "machine.undrain",
    ):
        assert verb in MUTATING_VERBS


def test_audit_log_can_be_injected(store, actor, clock):
    log = AuditLog(host="h-1", clock=clock)
    make_executor(store, actor, clock, audit=log).start(rule(), workflow((step("a"),)))
    assert _audit(store, "runs.start")[0]["host"] == "h-1"


# ---------------------------------------------------------------- edge cases


def test_timed_out_work_on_a_target_without_keys_is_not_reinvoked(store, clock):
    a = FakeActor(idempotent=False).on("h", ("accept",))
    ex = make_executor(store, a, clock)
    wf = workflow((step("h", "actor_task", timeout_s=10, retry=RetryPolicy(max_attempts=3)),))
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    clock.advance(11)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert step_state(doc, "h")["error"]["code"] == "unsafe_retry"
    assert len(a.calls_for("h")) == 1


def test_idempotent_step_config_allows_retry_without_key_support(store, clock):
    a = FakeActor(idempotent=False).on("h", ("lose_ack", {}))
    ex = make_executor(store, a, clock)
    wf = workflow((step("h", retry=RetryPolicy(max_attempts=2), config={"idempotent": True}),))
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "succeeded"
    assert len(a.calls_for("h")) == 2


def test_cancel_while_the_actor_is_working_discards_the_result(store, clock):
    holder = {}

    class CancellingActor(FakeActor):
        def invoke(self, input, idempotency_key, deadline, *, context):
            Containment(store, clock=clock).cancel(context.run_id, "zed")
            return super().invoke(input, idempotency_key, deadline, context=context)

    a = CancellingActor()
    ex = make_executor(store, a, clock)
    holder["run"] = ex.start(rule(), workflow((step("a"), step("b"))))
    ex.run_until_idle()
    doc = ex.run(holder["run"]["id"])
    assert doc["status"] == "cancelled"
    assert step_state(doc, "a")["status"] == "cancelled"
    assert a.calls_for("b") == []


def test_loop_body_failure_fails_the_loop_and_the_run(store, clock):
    a = FakeActor().on(step_key("each", 1, "b"), ("fail", "nope", False))
    a.on("src", ("complete", {"items": [1, 2, 3]}))
    loop = step(
        "each",
        "for_each",
        inputs=(port("items", "array"),),
        max_iterations=3,
        body=(step("b", inputs=(port("item", "any"),)),),
    )
    wf = workflow(
        (step("src", outputs=(port("items", "array"),)), loop),
        (edge("src", "items", "each", "items"),),
    )
    ex = make_executor(store, a, clock)
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "failed"
    assert step_state(doc, "each")["error"]["code"] == "loop_body_failed"
    assert len([c for c in a.calls if c[2].step_id.startswith("each[")]) == 2


def test_loop_body_steps_run_in_order_and_wire_within_the_iteration(store, clock):
    a = FakeActor(
        default=lambda inp, ctx: (
            {"y": inp["item"] + 1} if ctx.step_id.endswith("/first") else {"z": inp.get("y")}
        )
    )
    loop = step(
        "each",
        "for_each",
        inputs=(port("items", "array"),),
        outputs=(port("z", "array"),),
        max_iterations=2,
        body=(
            step("first", inputs=(port("item", "integer"),), outputs=(port("y", "integer"),)),
            step("second", inputs=(port("y", "integer"),), outputs=(port("z", "integer"),)),
        ),
    )
    wf = workflow(
        (loop,),
        (edge("inputs", "xs", "each", "items"), edge("first", "y", "second", "y")),
        inputs=(port("xs", "array"),),
        outputs=(Output(name="zs", type="array", source="steps.each.outputs.z"),),
    )
    ex = make_executor(store, a, clock)
    run = ex.start(rule(workflow_inputs={"xs": "trigger.xs"}), wf, trigger={"xs": [10, 20]})
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    order = [c[2].step_id for c in a.calls if c[2].step_id != ACTION_STEP]
    assert order == [
        step_key("each", 0, "first"),
        step_key("each", 0, "second"),
        step_key("each", 1, "first"),
        step_key("each", 1, "second"),
    ]
    assert doc["outputs"] == {"zs": [11, 21]}


def test_empty_for_each_succeeds_without_iterations(store, clock):
    a = FakeActor()
    loop = step(
        "each",
        "for_each",
        inputs=(port("items", "array"),),
        outputs=(port("results", "array"),),
        max_iterations=2,
        body=(step("b"),),
    )
    wf = workflow((loop,), (edge("inputs", "xs", "each", "items"),), inputs=(port("xs", "array"),))
    ex = make_executor(store, a, clock)
    run = ex.start(rule(workflow_inputs={"xs": "trigger.xs"}), wf, trigger={"xs": []})
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded"
    assert step_state(doc, "each")["outputs"] == {"results": []}
    assert [c for c in a.calls if c[2].step_id != ACTION_STEP] == []


def test_callable_router_and_workflow_output_type_mismatch(store, clock):
    a = FakeActor().on("a", ("complete", {"n": 1}))
    routed = []

    def router(ctx):
        routed.append(ctx.kind)
        return a

    wf = workflow(
        (step("a", outputs=(port("n", "any"),)),),
        outputs=(Output(name="n", type="string", source="steps.a.outputs.n"),),
    )
    ex = Executor(store, "spark", router, clock=clock)
    run = ex.start(rule(), wf)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert routed == ["code"]
    assert doc["status"] == "failed"
    assert doc["error"]["code"] == "output_type_mismatch"


def test_start_argument_errors(store, actor, clock):
    ex = make_executor(store, actor, clock)
    no_workflow_rule, wf = rule(workflow_id=None), workflow((step("a"),))
    with pytest.raises(RunError) as exc:
        ex.start(no_workflow_rule, wf)
    assert exc.value.code == "workflow_mismatch"
    plain_rule = rule()
    with pytest.raises(RunError) as exc:
        ex.start(plain_rule, None)
    assert exc.value.code == "workflow_required"
    other_rule = rule(workflow_id="other")
    with pytest.raises(RunError) as exc:
        ex.start(other_rule, wf)
    assert exc.value.code == "workflow_mismatch"
    action_wf = workflow((step(ACTION_STEP),))
    with pytest.raises(RunError) as exc:
        ex.start(plain_rule, action_wf)
    assert exc.value.code == "unsupported_workflow"
    with pytest.raises(ValueError):
        Executor(store, "", {})


def test_a_step_only_ever_blocked_redispatches_safely_on_a_non_deduplicating_target(store, clock):
    """R4 repro: blocked work never started, so re-asking it is not an unknown outcome -
    even a target that cannot deduplicate is asked again (no unsafe_retry), and the time it
    spent queued is not counted against its working budget (no timeout)."""
    actor = FakeActor(idempotent=False).on("h", *[("block", "at cap")] * 3)
    ex = make_executor(store, actor, clock)
    wf = workflow((step("h", "actor_task", timeout_s=30, retry=RetryPolicy(max_attempts=3)),))
    run = ex.start(rule(), wf)
    for _ in range(12):  # asks at 0, 5, 15 s (blocked), 35 s (past the 30 s budget)
        ex.run_until_idle()
        clock.advance(5)
    doc = ex.run(run["id"])
    st = step_state(doc, "h")
    assert st["status"] == "succeeded", st["error"]
    assert doc["status"] == "succeeded"
    assert st["attempt"] == 1
    assert actor.effects_for("h") == 1
    assert "timeout" not in [h["event"] for h in doc["history"] if h["step"] == "h"]


def test_a_step_blocked_past_its_queue_bound_fails_queue_timeout(store, clock):
    actor = FakeActor(idempotent=False).on("h", *[("block", "at cap")] * 10)
    ex = make_executor(store, actor, clock)
    wf = workflow((step("h", "actor_task", timeout_s=8, retry=RetryPolicy(max_attempts=2)),))
    run = ex.start(rule(), wf)
    for _ in range(12):  # the queue bound is 2 x 8 s; a retry would only queue again
        ex.run_until_idle()
        clock.advance(5)
    st = step_state(ex.run(run["id"]), "h")
    assert st["status"] == "failed"
    assert st["error"]["code"] == "queue_timeout"
    assert st["attempt"] == 1
    assert actor.effects_for("h") == 0


def test_rule_action_context_carries_the_actor_named_in_params(store, actor, clock):
    """t8: ``action.params.actor`` (a literal id, never resolved) is the context's actor."""
    ex = make_executor(store, actor, clock)
    act = Action(kind="noop", params={"actor": "box", "x": 1})
    ex.start(rule(workflow_id=None, action=act), None)
    ex.run_until_idle()
    [call] = actor.calls_for(ACTION_STEP)
    assert call[2].actor == "box"
    assert call[2].config["params"]["actor"] == "box"


def test_rule_action_context_has_no_actor_when_none_is_named(store, actor, clock):
    ex = make_executor(store, actor, clock)
    ex.start(rule(workflow_id=None, action=Action(kind="noop", params={"x": 1})), None)
    ex.run_until_idle()
    [call] = actor.calls_for(ACTION_STEP)
    assert call[2].actor is None


# --- heartbeat semantics for takeover (#7) -------------------------------


def test_missing_heartbeat_doc_counts_as_offline_so_takeover_is_allowed(store, actor, clock):
    ex = make_executor(store, actor, clock)
    assert ex._machine_online("thor", clock()) is False


def test_garbage_heartbeat_ts_counts_as_online_so_no_takeover(store, actor, clock, caplog):
    from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION

    store.put(HEARTBEAT_COLLECTION, {"id": "thor", "machine": "thor", "ts": "not-a-date"})
    ex = make_executor(store, actor, clock)
    with caplog.at_level("WARNING"):
        assert ex._machine_online("thor", clock()) is True
    assert "thor" in caplog.text


def test_stale_heartbeat_is_offline_and_fresh_is_online(store, actor, clock):
    from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION

    ex = make_executor(store, actor, clock, holder_offline_after=timedelta(seconds=30))
    store.put(
        HEARTBEAT_COLLECTION,
        {"id": "thor", "machine": "thor", "ts": clock().strftime("%Y-%m-%dT%H:%M:%SZ")},
    )
    clock.advance(10)
    assert ex._machine_online("thor", clock()) is True
    clock.advance(30)
    assert ex._machine_online("thor", clock()) is False
