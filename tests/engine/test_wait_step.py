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
