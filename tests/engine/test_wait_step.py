"""Wait step (t10): a persisted sleep with no worker held, plus the head_unchanged guard.

Covers c40/h32: N seconds parks the run, a restart mid-wait still resumes once, and a PR head
that moved during the wait ends the run ``superseded`` with no later step run.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from culture_rules.engine.runs import RUNS_COLLECTION, Containment, Executor, step_state
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

    def __init__(self, sha: str | dict | Exception = SHA_A) -> None:
        self.sha = sha
        self.calls: list[tuple] = []

    def __call__(self, actor: str | None, repo: str, number: int) -> str | dict:
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
    # d21: the superseding transition wrote its immutable completion record with it
    record = store.get("run_completions", run["id"])
    assert record["status"] == "superseded"
    assert record["emitted"] is False
    assert record["envelope"]["type"] == "rules.run.superseded"


@pytest.mark.parametrize(
    ("pull", "why"),
    [
        ({"head_sha": SHA_A, "state": "closed", "merged": True}, "merged"),
        ({"head_sha": SHA_A, "state": "closed", "merged": False}, "closed"),
    ],
    ids=["merged", "closed"],
)
def test_guard_pr_no_longer_open_ends_superseded_pr_not_open(store, clock, pull, why):
    """#31 (d27): a PR merged or closed during the wait keeps its head sha, so the sha
    check alone let the run go on to provision and fail on ``git fetch`` of a deleted
    branch (run-048498ce, culture-rules #27). The wake now ends it ``superseded`` with
    ``pr_not_open`` and runs nothing later."""
    actor, heads = FakeActor(), Heads(pull)
    ex = make(store, clock, actor, heads)
    run = start(ex, guard_config(60))
    ex.run_until_idle()
    clock.advance(61)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "superseded"
    assert actor.calls == []
    assert step_state(doc, "after")["status"] == "cancelled"
    error = step_state(doc, "w")["error"]
    assert error["code"] == "pr_not_open"
    assert why in error["message"]
    record = store.get("run_completions", run["id"])
    assert record["status"] == "superseded"


def test_guard_open_pr_with_unchanged_head_proceeds(store, clock):
    actor = FakeActor()
    heads = Heads({"head_sha": SHA_A, "state": "open", "merged": False})
    ex = make(store, clock, actor, heads)
    run = start(ex, guard_config(60))
    ex.run_until_idle()
    clock.advance(61)
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "succeeded"
    assert len(actor.calls_for("after")) == 1


def test_guard_open_pr_with_moved_head_still_says_head_moved(store, clock):
    actor = FakeActor()
    heads = Heads({"head_sha": SHA_B, "state": "open", "merged": False})
    ex = make(store, clock, actor, heads)
    run = start(ex, guard_config(60))
    ex.run_until_idle()
    clock.advance(61)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "superseded"
    assert step_state(doc, "w")["error"]["code"] == "superseded"


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
    r, wf = rule(), workflow((loop,))
    with pytest.raises(RunError):
        ex.start(r, wf)


# ------------------------------------------------------------ Codex round 1


def _sleeping_run(ex, config, **kw):
    run = start(ex, config, **kw)
    ex.run_until_idle()
    return run


def test_guard_target_falls_back_to_the_normalized_event_envelope(store, clock):
    actor, heads = FakeActor(), Heads(SHA_A)
    ex = make(store, clock, actor, heads)
    guard = {"value": "head_unchanged", "ref": "inputs.head_sha", "actor": "gh"}
    r = rule(workflow_inputs={"head_sha": {"$literal": SHA_A}})
    envelope = {
        "id": "evt1",
        "type": "github.pr.synchronize",
        "data": {"repository": "acme/widgets", "number": 12, "action": "synchronize"},
    }
    run = ex.start(r, two_step({"seconds": 60, "guard": guard}), trigger=envelope)
    ex.run_until_idle()
    clock.advance(61)
    ex.run_until_idle()
    assert ex.run(run["id"])["status"] == "succeeded"
    assert heads.calls == [("gh", "acme/widgets", 12)]


def test_paused_engine_defers_the_guarded_lookup_until_resume(store, clock):
    from culture_rules.engine.runs import Containment

    actor, heads = FakeActor(), Heads(SHA_A)
    ex = make(store, clock, actor, heads)
    run = _sleeping_run(ex, guard_config(60))
    c = Containment(store, clock=clock)
    c.pause("op")
    clock.advance(61)
    ex.run_until_idle()
    assert heads.calls == []
    assert step_state(ex.run(run["id"]), "w")["status"] == "sleeping"
    c.resume("op")
    ex.run_until_idle()
    assert heads.calls == [("gh", "o/r", 7)]
    assert ex.run(run["id"])["status"] == "succeeded"


def test_unguarded_wake_still_completes_while_paused(store, clock):
    from culture_rules.engine.runs import Containment

    actor = FakeActor()
    ex = make(store, clock, actor)
    run = _sleeping_run(ex, {"seconds": 60})
    Containment(store, clock=clock).pause("op")
    clock.advance(61)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert step_state(doc, "w")["status"] == "succeeded"
    assert actor.calls == []  # but nothing new dispatches under pause


def test_only_the_eligible_node_performs_the_guarded_wake(store, clock):
    from culture_rules.model.actor import Actor
    from culture_rules.node.actors import ACTORS_COLLECTION
    from tests.engine.run_helpers import enrol_online, machine

    enrol_online(store, clock, machine("spark"), machine("thor"))
    store.put(
        ACTORS_COLLECTION,
        Actor(id="gh", name="gh", kind="service", machine="thor").to_dict(),
    )
    actor = FakeActor()
    wrong, right = Heads(SHA_A), Heads(SHA_A)
    spark = make(store, clock, actor, wrong, host="spark")
    thor = make(store, clock, actor, right, host="thor")
    run = _sleeping_run(spark, guard_config(60))
    clock.advance(61)
    enrol_online(store, clock, machine("spark"), machine("thor"))  # fresh beats
    spark.run_until_idle()  # not the actor's machine: must not look up or fail the run
    assert wrong.calls == []
    assert step_state(spark.run(run["id"]), "w")["status"] == "sleeping"
    thor.run_until_idle()
    assert right.calls == [("gh", "o/r", 7)]
    assert thor.run(run["id"])["status"] == "succeeded"


def test_a_disabled_guard_actor_does_not_strand_the_wait(store, clock):
    """A disabled machine-bound actor yields no placement, so the lookup reaches the
    router's failure path instead of leaving the step asleep forever (Codex t10 r2)."""
    from culture_rules.model.actor import Actor
    from culture_rules.node.actors import ACTORS_COLLECTION
    from tests.engine.run_helpers import enrol_online, machine

    enrol_online(store, clock, machine("spark"), machine("thor"))
    store.put(
        ACTORS_COLLECTION,
        Actor(id="gh", name="gh", kind="service", machine="thor", enabled=False).to_dict(),
    )
    heads = Heads(SHA_A)
    spark = make(store, clock, FakeActor(), heads, host="spark")
    run = _sleeping_run(spark, guard_config(60))
    clock.advance(61)
    enrol_online(store, clock, machine("spark"), machine("thor"))
    spark.run_until_idle()
    assert heads.calls == [("gh", "o/r", 7)]
    assert step_state(spark.run(run["id"]), "w")["status"] != "sleeping"


def test_drained_node_does_not_perform_the_guarded_wake(store, clock):
    from culture_rules.engine.runs import Containment
    from tests.engine.run_helpers import enrol_online, machine

    enrol_online(store, clock, machine("spark"))
    heads = Heads(SHA_A)
    ex = make(store, clock, FakeActor(), heads)
    run = _sleeping_run(ex, guard_config(60))
    Containment(store, clock=clock).drain("spark", "op")
    clock.advance(61)
    ex.run_until_idle()
    assert heads.calls == []
    assert step_state(ex.run(run["id"]), "w")["status"] == "sleeping"


def _router_executor(store, clock, inner):
    from culture_rules.model.actor import Actor
    from culture_rules.node.actors import ACTORS_COLLECTION, ActorRouter

    store.put(
        ACTORS_COLLECTION,
        Actor(id="gh", name="gh", kind="service", params={"max_concurrency": 1}).to_dict(),
    )
    router = ActorRouter(
        store, ports={"action:github.pr_head": inner, "*": FakeActor()}, clock=clock
    )
    seen = []

    def routed(ctx):
        port = router(ctx)
        seen.append((dict(ctx.config), type(port).__name__))
        return port

    return Executor(store, "spark", routed, clock=clock), seen


class ScriptedHead:
    supports_idempotency_key = True

    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def invoke(self, input, key, deadline, *, context):
        from culture_rules.engine.actorport import InvocationResult

        self.calls += 1
        kind, value = self.results.pop(0)
        if kind == "blocked":
            return InvocationResult.blocked("busy")
        if kind == "timeout":
            return InvocationResult.failed("deadline_exceeded", retryable=True)
        if kind == "busy":
            return InvocationResult.failed("lookup_busy", retryable=True)
        self.deadlines = [*getattr(self, "deadlines", []), deadline]
        return InvocationResult.completed({"head_sha": value})


def test_lookup_goes_through_the_actors_limits_and_blocked_retries_later(store, clock):
    inner = ScriptedHead(("blocked", None), ("ok", SHA_A))
    ex, seen = _router_executor(store, clock, inner)
    run = _sleeping_run(ex, guard_config(60))
    clock.advance(61)
    ex.run_until_idle()
    assert seen[0][0]["params"] == {"actor": "gh"}
    assert seen[0][1] == "LimitedActor"
    doc = ex.run(run["id"])
    assert doc["status"] == "running"  # blocked is not a failure
    assert step_state(doc, "w")["status"] == "sleeping"
    ex.run_until_idle()
    assert inner.calls == 1  # not retried before the retry delay
    clock.advance(10)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["error"] is None, doc["error"]
    assert doc["status"] == "succeeded"
    assert inner.calls == 2


def _guard_on_thor(store, clock):
    from culture_rules.model.actor import Actor
    from culture_rules.node.actors import ACTORS_COLLECTION
    from tests.engine.run_helpers import enrol_online, machine

    enrol_online(store, clock, machine("spark"), machine("thor"))
    store.put(
        ACTORS_COLLECTION,
        Actor(id="gh", name="gh", kind="service", machine="thor").to_dict(),
    )


def test_guard_actor_host_offline_fails_the_wait_after_the_bound(store, clock):
    """Review #17 finding 1: a guarded wake whose actor's machine stays offline never
    proceeds, but it is not stranded either - past PLACEMENT_ABANDON_AFTER (measured from the
    wake) any node fails the step ``placement_unavailable`` and the run ends (releasing a
    held concurrency key); before that it waits, the placement error recorded."""
    from culture_rules.engine.runs import PLACEMENT_ABANDON_AFTER, PLACEMENT_UNAVAILABLE
    from tests.engine.run_helpers import enrol_online, machine

    _guard_on_thor(store, clock)
    heads = Heads(SHA_A)
    spark = make(store, clock, FakeActor(), heads, host="spark")
    run = _sleeping_run(spark, guard_config(60))
    clock.advance(61)
    enrol_online(store, clock, machine("spark"))  # only spark beats: thor is offline
    spark.run_until_idle()
    st = step_state(spark.run(run["id"]), "w")
    assert st["status"] == "sleeping"  # merely offline for now: wait for it
    assert st["placement_error"]["code"] == "placement.machine_offline"
    assert heads.calls == []

    clock.advance(PLACEMENT_ABANDON_AFTER.total_seconds() - 5)
    enrol_online(store, clock, machine("spark"))
    spark.run_until_idle()
    assert step_state(spark.run(run["id"]), "w")["status"] == "sleeping"

    clock.advance(10)
    enrol_online(store, clock, machine("spark"))
    spark.run_until_idle()
    doc = spark.run(run["id"])
    assert step_state(doc, "w")["status"] == "failed"
    assert step_state(doc, "w")["error"]["code"] == PLACEMENT_UNAVAILABLE
    assert doc["status"] == "failed"
    assert heads.calls == []  # never proceeded as if the head were unchanged


def test_guard_actor_host_back_in_time_wakes_normally(store, clock):
    from tests.engine.run_helpers import enrol_online, machine

    _guard_on_thor(store, clock)
    heads = Heads(SHA_A)
    spark = make(store, clock, FakeActor(), Heads(SHA_A), host="spark")
    thor = make(store, clock, FakeActor(), heads, host="thor")
    run = _sleeping_run(spark, guard_config(60))
    clock.advance(61)
    enrol_online(store, clock, machine("spark"))
    spark.run_until_idle()
    clock.advance(120)  # thor is slow to come back, but inside the bound
    enrol_online(store, clock, machine("spark"), machine("thor"))
    spark.run_until_idle()
    thor.run_until_idle()
    assert heads.calls == [("gh", "o/r", 7)]
    assert thor.run(run["id"])["status"] == "succeeded"


def test_guard_actor_host_unenrolled_fails_the_wait_at_once(store, clock):
    from culture_rules.machines.enrol import unenrol
    from tests.engine.run_helpers import enrol_online, machine

    _guard_on_thor(store, clock)
    spark = make(store, clock, FakeActor(), Heads(SHA_A), host="spark")
    run = _sleeping_run(spark, guard_config(60))
    unenrol(store, "thor", apply=True)
    clock.advance(61)
    enrol_online(store, clock, machine("spark"))
    spark.run_until_idle()
    doc = spark.run(run["id"])
    assert step_state(doc, "w")["error"]["code"] == "placement.machine_unknown"
    assert doc["status"] == "failed"


def test_a_timed_out_head_lookup_is_retried_later_not_failed(store, clock):
    """Review #17 finding 6: the head port honours the invocation deadline, so a cold
    secret resolve or slow GitHub answers ``deadline_exceeded`` (retryable) while the App
    warms in the background; the wake re-arms instead of failing the run."""
    from datetime import timedelta

    from culture_rules.engine.runs import HEAD_LOOKUP_TIMEOUT_S

    inner = ScriptedHead(("timeout", None), ("busy", None), ("ok", SHA_A))
    ex, _ = _router_executor(store, clock, inner)
    run = _sleeping_run(ex, guard_config(60))
    clock.advance(61)
    ex.run_until_idle()
    st = step_state(ex.run(run["id"]), "w")
    assert st["status"] == "sleeping"
    assert st["lookup_retries"] == 1
    for _ in range(2):
        clock.advance(10)
        ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded", doc
    assert inner.calls == 3
    # the lookup's deadline is the short head-lookup bound, not a step timeout
    assert inner.deadlines[0] == clock() + timedelta(seconds=HEAD_LOOKUP_TIMEOUT_S)


def test_a_head_lookup_that_keeps_timing_out_fails_safe(store, clock):
    from culture_rules.engine.runs import HEAD_LOOKUP_RETRIES

    inner = ScriptedHead(*[("timeout", None)] * (HEAD_LOOKUP_RETRIES + 1))
    ex, _ = _router_executor(store, clock, inner)
    run = _sleeping_run(ex, guard_config(60))
    clock.advance(61)
    for _ in range(HEAD_LOOKUP_RETRIES + 1):
        ex.run_until_idle()
        clock.advance(10)
    doc = ex.run(run["id"])
    assert step_state(doc, "w")["error"]["code"] == "head_lookup_failed"
    assert doc["status"] == "failed"
    assert inner.calls == HEAD_LOOKUP_RETRIES + 1


class SlowTimingOutHead(ScriptedHead):
    """A head port whose every call uses up its whole deadline, then times out."""

    def __init__(self, clock, answers):
        super().__init__(*answers)
        self.clock = clock

    def invoke(self, input, key, deadline, *, context):
        self.clock.now = deadline  # the lookup consumed its full deadline
        return super().invoke(input, key, deadline, context=context)


@pytest.mark.parametrize("answer", ["timeout", "blocked"])
def test_a_slow_failed_lookup_backs_off_from_when_it_returned(store, clock, answer):
    """Codex r17b finding 3: the retry is scheduled from the clock after the lookup, so a
    lookup that used its whole deadline does not re-run at once in the same tick."""
    from datetime import timedelta

    from culture_rules.engine.runs import BLOCKED_RETRY_S, HEAD_LOOKUP_RETRIES

    inner = SlowTimingOutHead(clock, [(answer, None)] * (HEAD_LOOKUP_RETRIES + 1))
    ex, _ = _router_executor(store, clock, inner)
    run = _sleeping_run(ex, guard_config(60))
    clock.advance(61)
    ex.run_until_idle()
    assert inner.calls == 1  # one lookup per backoff, not all of them in one tick
    st = step_state(ex.run(run["id"]), "w")
    assert st["status"] == "sleeping"
    assert st["deadline"] == (clock() + timedelta(seconds=BLOCKED_RETRY_S)).isoformat()


def test_a_blocked_guarded_wake_backs_off_and_records_once(store, clock):
    """The blocked-queue churn on a guarded wait: its head lookup's actor stays at a limit.
    Re-arms back off like a blocked step (5, 10, 20, 40, 60 s) and history records the
    first ``wait_blocked`` only (a counter on the step tracks the repeats)."""
    from culture_rules.engine.runs import BLOCKED_RETRY_MAX_S

    inner = ScriptedHead(*[("blocked", None)] * 8, ("ok", SHA_A))
    ex, _ = _router_executor(store, clock, inner)
    run = _sleeping_run(ex, guard_config(60))
    clock.advance(61)
    wakes = []
    for _ in range(400):
        before = inner.calls
        ex.run_until_idle()
        if inner.calls != before:
            wakes.append(clock())
        clock.advance(1)
    gaps = [(b - a).total_seconds() for a, b in zip(wakes, wakes[1:], strict=False)]
    assert gaps[:5] == [5.0, 10.0, 20.0, 40.0, BLOCKED_RETRY_MAX_S]
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded", doc["error"]
    assert [h["event"] for h in doc["history"]].count("wait_blocked") == 1
    assert step_state(doc, "w")["lookup_blocked"] == 8


def test_a_guarded_wake_blocked_past_its_queue_bound_fails_queue_timeout(store, clock):
    """The guard lookup's queue has the same bound as a blocked step's (2 x the wait
    step's timeout_s, default 3600 s): past it the step fails ``queue_timeout`` - never
    proceeds as if the head were unchanged, never waits forever."""
    from culture_rules.engine.runs import DEFAULT_TIMEOUT_S, QUEUE_TIMEOUT, queue_limit_s

    inner = ScriptedHead(*[("blocked", None)] * 1000)
    ex, _ = _router_executor(store, clock, inner)
    run = _sleeping_run(ex, guard_config(60))
    clock.advance(61)
    bound = queue_limit_s(DEFAULT_TIMEOUT_S)
    for _ in range(int(bound / 60) + 3):
        ex.run_until_idle()
        clock.advance(60)
    doc = ex.run(run["id"])
    st = step_state(doc, "w")
    assert st["status"] == "failed", st
    assert st["error"]["code"] == QUEUE_TIMEOUT
    assert doc["status"] == "failed"
    assert [h["event"] for h in doc["history"]].count("wait_blocked") == 1


def test_a_blocked_guarded_wake_expires_at_its_bound_even_while_its_node_is_drained(store, clock):
    """Codex r2 on f6d31f3: the lookup's queue bound is checked in housekeeping, not only
    when another lookup answers blocked. Blocked once, node drained past the bound: the
    wait fails ``queue_timeout`` without another lookup, and an undrained node never lets
    a later successful lookup carry the run on past the expired bound."""
    from culture_rules.engine.runs import DEFAULT_TIMEOUT_S, QUEUE_TIMEOUT, queue_limit_s

    inner = ScriptedHead(("blocked", None), ("ok", SHA_A))
    ex, _ = _router_executor(store, clock, inner)
    run = _sleeping_run(ex, guard_config(60))
    clock.advance(61)
    ex.run_until_idle()
    assert inner.calls == 1
    assert step_state(ex.run(run["id"]), "w")["status"] == "sleeping"
    Containment(store).drain("spark", "ops")
    clock.advance(queue_limit_s(DEFAULT_TIMEOUT_S) + 1)  # 7201 s
    ex.run_until_idle()
    doc = ex.run(run["id"])
    st = step_state(doc, "w")
    assert st["status"] == "failed", st
    assert st["error"]["code"] == QUEUE_TIMEOUT
    assert "7200" in st["error"]["message"]  # names the bound
    assert inner.calls == 1  # no further lookup
    Containment(store).undrain("spark", "ops")
    ex.run_until_idle()
    assert inner.calls == 1
    assert ex.run(run["id"])["status"] == "failed"


@pytest.mark.parametrize("drained", [False, True])
def test_a_legacy_mid_blocked_guarded_wake_adopts_the_bound(store, clock, drained):
    """The old engine left a refused lookup as a plain ``sleeping`` wake re-armed 5 s out,
    with one ``wait_blocked`` history entry per refusal and no counter: on upgrade the spell
    is dated from the first entry of its trailing ``wait_blocked`` run and bounded."""
    from culture_rules.engine.runs import QUEUE_TIMEOUT

    inner = ScriptedHead(*[("blocked", None)] * 3, ("ok", SHA_A))
    ex, _ = _router_executor(store, clock, inner)
    run = _sleeping_run(ex, guard_config(60))
    clock.advance(61)
    ex.run_until_idle()  # refused once
    first_refusal = clock()
    doc = store.get(RUNS_COLLECTION, run["id"])
    st = step_state(doc, "w")
    for k in ("lookup_blocked", "lookup_blocked_since"):
        st.pop(k, None)
    # the old engine's second refusal: another entry, re-armed BLOCKED_RETRY_S out
    doc["rev"] += 1
    doc["history"].append(
        {
            "rev": doc["rev"],
            "at": (first_refusal + timedelta(seconds=5)).isoformat(),
            "host": "spark",
            "event": "wait_blocked",
            "step": "w",
        }
    )
    st["deadline"] = (first_refusal + timedelta(seconds=10)).isoformat()
    store.put(RUNS_COLLECTION, doc)
    if drained:
        Containment(store).drain("spark", "ops")
    clock.advance(7200 + 1)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    st = step_state(doc, "w")
    assert st["status"] == "failed", st
    assert st["error"]["code"] == QUEUE_TIMEOUT
    assert inner.calls == 1


def test_a_legacy_mid_blocked_guarded_wake_within_its_bound_keeps_waiting(store, clock):
    inner = ScriptedHead(("blocked", None), ("ok", SHA_A))
    ex, _ = _router_executor(store, clock, inner)
    run = _sleeping_run(ex, guard_config(60))
    clock.advance(61)
    ex.run_until_idle()
    first_refusal = clock()
    doc = store.get(RUNS_COLLECTION, run["id"])
    st = step_state(doc, "w")
    for k in ("lookup_blocked", "lookup_blocked_since"):
        st.pop(k, None)
    store.put(RUNS_COLLECTION, doc)
    clock.advance(30)
    ex.run_until_idle()
    doc = ex.run(run["id"])
    assert doc["status"] == "succeeded", doc["error"]
    assert step_state(doc, "w")["lookup_blocked_since"] == first_refusal.isoformat()


class _BlockedThenSlowBlockedHead(ScriptedHead):
    """Refuses at once, then refuses again only after using ``slow_s`` of the clock."""

    def __init__(self, clock, slow_s):
        super().__init__(("blocked", None), ("blocked", None))
        self.clock = clock
        self.slow_s = slow_s

    def invoke(self, input, key, deadline, *, context):
        if self.calls == 1:
            self.clock.advance(self.slow_s)
        return super().invoke(input, key, deadline, context=context)


def test_a_refused_lookup_that_itself_ran_past_the_queue_bound_fails_queue_timeout(store, clock):
    """Characterization (complexity refactor): the queue bound is re-checked after a refused
    lookup, at the time the lookup returned - a lookup that ran past it fails the wait
    ``queue_timeout`` with no re-arm, recorded once at that time."""
    from culture_rules.engine.runs import DEFAULT_TIMEOUT_S, QUEUE_TIMEOUT, queue_limit_s

    bound = queue_limit_s(DEFAULT_TIMEOUT_S)
    inner = _BlockedThenSlowBlockedHead(clock, bound + 1)
    ex, _ = _router_executor(store, clock, inner)
    run = _sleeping_run(ex, guard_config(60))
    clock.advance(61)
    ex.run_until_idle()  # refused once: re-armed, the spell dated now
    first = clock()
    st = step_state(ex.run(run["id"]), "w")
    assert st["status"] == "sleeping"
    assert st["lookup_blocked_since"] == first.isoformat()
    clock.advance(10)
    ex.run_until_idle()  # the second lookup returns refused past the bound
    assert inner.calls == 2
    doc = ex.run(run["id"])
    st = step_state(doc, "w")
    assert st["status"] == "failed", st
    assert st["error"]["code"] == QUEUE_TIMEOUT
    assert st["lookup_blocked_since"] == first.isoformat()
    assert "(1 refusals)" in st["error"]["message"]
    timeouts = [h for h in doc["history"] if h["event"] == QUEUE_TIMEOUT]
    assert len(timeouts) == 1
    assert timeouts[0]["step"] == "w"
    assert timeouts[0]["at"] == clock().isoformat()
    assert [h["event"] for h in doc["history"]].count("wait_blocked") == 1
    assert doc["status"] == "failed"


def test_a_malformed_deadline_raises_before_an_expired_lookup_queue_fails_the_wait(monkeypatch):
    """Characterization (Codex review of the complexity refactor): ``_due_timers`` parses a
    step's ``deadline`` before checking any timer, so a sleeping step whose lookup queue
    expired but whose deadline is malformed raises, unwritten - it is not failed
    ``queue_timeout``."""
    from culture_rules.engine import runs

    monkeypatch.setattr(runs, "_lookup_queue_expired", lambda plan, st, now: {"code": "x"})
    doc = {"steps": [{"key": "w", "status": runs.SLEEPING, "deadline": "not-a-date"}]}
    now = runs._parse("2026-10-08T00:00:00+00:00")
    with pytest.raises(ValueError, match="not-a-date"):
        runs._due_timers(None, doc, now)
