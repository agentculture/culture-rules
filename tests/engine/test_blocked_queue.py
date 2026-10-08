"""A step queued behind a capped actor (``blocked``): its own bound, backoff, bounded history.

Repro of live run ``run-96a1e3a45721cce64db1a7605ca83f1b`` (workflow pr-fixer, agent actor
``qwen-fixer`` with concurrency cap 1): ``fix[0]/agent`` was dispatched at 16:47:47, blocked
behind another run's seat, re-asked every ~6 s (about 1000 history entries), accepted at
17:40:44 and timed out at 17:52:47 - its 3900 s working budget had been started by the
first, blocked dispatch, so the agent got about 12 minutes of work.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.actors.limits import ActorLimits, LimitedActor
from culture_rules.engine import runs as runs_mod
from culture_rules.engine.actorport import InvocationContext
from culture_rules.engine.runs import RUNS_COLLECTION, Executor, due_steps, step_state
from culture_rules.model.common import RetryPolicy
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, Crash, FakeActor, rule, step, workflow

T_DISPATCH = datetime(2026, 10, 7, 16, 47, 47, tzinfo=UTC)
T_SEAT_FREE = datetime(2026, 10, 7, 17, 40, 9, tzinfo=UTC)
WORK_S = 3900.0  # the pr-fixer agent step's timeout_s


class Counting:
    """Counts every invocation (blocked ones included) reaching the capped port."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.supports_idempotency_key = True
        self.calls: list[tuple[datetime, str, datetime]] = []
        self.clock = None

    def invoke(self, input, key, deadline, *, context):
        res = self.inner.invoke(input, key, deadline, context=context)
        self.calls.append((self.clock(), res.outcome, deadline))
        return res


@pytest.fixture
def clock() -> Clock:
    return Clock(T_DISPATCH)


@pytest.fixture
def store(clock) -> MemoryStore:
    return MemoryStore(clock=clock)


def hold_seat(store, clock, key: str) -> None:
    """Another run takes qwen-fixer's only seat (accepted work, released when it ends)."""
    held = InvocationContext("other-run", "agent", "actor_task", "thor", 1, "qwen-fixer", {})
    holder = FakeActor().on("agent", ("accept",))
    LimitedActor(holder, "qwen-fixer", ActorLimits(max_concurrency=1), store, clock=clock).invoke(
        {}, key, clock() + timedelta(hours=3), context=held
    )


def capped(store, clock, inner: FakeActor | None = None) -> tuple[LimitedActor, Counting]:
    limited = LimitedActor(
        inner or FakeActor().on("agent", ("accept",)),
        "qwen-fixer",
        ActorLimits(max_concurrency=1),
        store,
        clock=clock,
    )
    hold_seat(store, clock, "holder-key")
    counting = Counting(limited)
    counting.clock = clock
    return limited, counting


def agent_workflow(timeout_s: float = WORK_S):
    return workflow((step("agent", "actor_task", timeout_s=timeout_s),))


def tick_until(ex: Executor, clock: Clock, until: datetime, every: float = 6.0) -> None:
    """Every ``every`` seconds of the injected clock, one engine tick (the node loop)."""
    while clock() < until:
        ex.run_until_idle()
        clock.advance(every)
    clock.now = until
    ex.run_until_idle()


def step_history(doc, key="agent"):
    return [h for h in doc["history"] if h["step"] == key]


def test_the_working_budget_starts_when_the_queued_work_is_accepted(store, clock):
    limited, port = capped(store, clock)
    ex = Executor(store, "spark", {"*": port}, clock=clock)
    run = ex.start(rule(), agent_workflow())
    tick_until(ex, clock, T_SEAT_FREE)
    assert step_state(ex.run(run["id"]), "agent")["status"] == "blocked"
    limited.release("holder-key")  # the other run finished: the seat is free
    tick_until(ex, clock, T_SEAT_FREE + timedelta(seconds=90))
    st = step_state(ex.run(run["id"]), "agent")
    assert st["status"] == "waiting"
    accepted_at, outcome, invoked_deadline = port.calls[-1]
    assert outcome == "accepted"
    assert accepted_at >= T_SEAT_FREE
    # the agent was told, and the engine enforces, a full budget from the acceptance
    assert invoked_deadline == accepted_at + timedelta(seconds=WORK_S)
    assert st["deadline"] == (accepted_at + timedelta(seconds=WORK_S)).isoformat()
    assert st["attempt"] == 1
    # the first dispatch's deadline (16:47:47 + 3900 s = 17:52:47) is no longer a timeout
    tick_until(ex, clock, T_DISPATCH + timedelta(seconds=WORK_S + 60), every=60)
    st = step_state(ex.run(run["id"]), "agent")
    assert st["status"] == "waiting", st["error"]
    assert ex.run(run["id"])["status"] == "running"


def test_waiting_in_the_queue_is_bounded_and_fails_queue_timeout(store, clock):
    _, port = capped(store, clock)
    ex = Executor(store, "spark", {"*": port}, clock=clock)
    run = ex.start(rule(), agent_workflow(timeout_s=600))
    limit = runs_mod.queue_limit_s(600)
    assert limit == 600 * runs_mod.QUEUE_LIMIT_FACTOR
    tick_until(ex, clock, T_DISPATCH + timedelta(seconds=limit - 1))
    assert step_state(ex.run(run["id"]), "agent")["status"] == "blocked"
    tick_until(ex, clock, T_DISPATCH + timedelta(seconds=limit + 1))
    doc = ex.run(run["id"])
    st = step_state(doc, "agent")
    assert st["status"] == "failed"
    assert st["error"]["code"] == runs_mod.QUEUE_TIMEOUT == "queue_timeout"
    assert st["attempt"] == 1  # never started: no attempt was used
    assert doc["status"] == "failed"
    assert all(outcome == "blocked" for _, outcome, _ in port.calls)


def test_blocked_redispatch_backs_off_exponentially_to_a_cap(store, clock):
    _, port = capped(store, clock)
    ex = Executor(store, "spark", {"*": port}, clock=clock)
    ex.start(rule(), agent_workflow())
    tick_until(ex, clock, T_DISPATCH + timedelta(minutes=20), every=1)
    times = [at for at, _, _ in port.calls]
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
    assert gaps[:5] == [5.0, 10.0, 20.0, 40.0, 60.0]
    assert set(gaps[5:]) == {runs_mod.BLOCKED_RETRY_MAX_S} == {60.0}
    # 20 minutes in the queue: ~24 polls, not ~200 at a fixed BLOCKED_RETRY_S
    assert len(port.calls) <= 25


def test_history_stays_bounded_while_blocked(store, clock):
    limited, port = capped(store, clock)
    ex = Executor(store, "spark", {"*": port}, clock=clock)
    run = ex.start(rule(), agent_workflow())
    tick_until(ex, clock, T_SEAT_FREE)
    doc = ex.run(run["id"])
    events = [h["event"] for h in step_history(doc)]
    assert events == ["dispatched", "blocked"]  # the transition into the queue, once
    st = step_state(doc, "agent")
    queue = st["queue"]
    assert queue["polls"] == len(port.calls) > 10
    assert queue["since"] == T_DISPATCH.isoformat()
    assert queue["left_at"] is None
    limited.release("holder-key")
    tick_until(ex, clock, T_SEAT_FREE + timedelta(seconds=90))
    doc = ex.run(run["id"])
    assert [h["event"] for h in step_history(doc)] == ["dispatched", "blocked", "waiting"]
    st = step_state(doc, "agent")
    assert st["queue"]["left_at"] is not None
    assert st["queue"]["polls"] == len(port.calls) - 1  # the last poll was accepted
    # rev still moves on every write (compare-and-set); history does not
    assert doc["rev"] == doc["history"][-1]["rev"]
    assert doc["rev"] > len(doc["history"]) + 3 * (len(port.calls) - 2)


def test_a_second_spell_in_the_queue_is_recorded_again(store, clock):
    """Leaving the queue and being blocked again later is a new state change."""
    inner = FakeActor().on("agent", ("fail", "flaky", True), ("complete", {}))
    limited, port = capped(store, clock, inner)
    ex = Executor(store, "spark", {"*": port}, clock=clock)
    wf = workflow(
        (
            step(
                "agent",
                "actor_task",
                timeout_s=WORK_S,
                retry=RetryPolicy(max_attempts=3, backoff_s=60),
            ),
        )
    )
    run = ex.start(rule(), wf)
    tick_until(ex, clock, T_DISPATCH + timedelta(seconds=30), every=1)
    limited.release("holder-key")
    for _ in range(120):
        if step_state(ex.run(run["id"]), "agent")["status"] == "retry_wait":
            break
        clock.advance(1)
        ex.run_until_idle()
    first = step_state(ex.run(run["id"]), "agent")["queue"]
    assert first["left_at"] is not None
    hold_seat(store, clock, "holder-2")  # the retry queues behind another run again
    tick_until(ex, clock, clock() + timedelta(seconds=300), every=1)
    doc = ex.run(run["id"])
    events = [h["event"] for h in step_history(doc)]
    assert events.count("blocked") == 2, events
    st = step_state(doc, "agent")
    assert st["status"] == "blocked"
    assert st["attempt"] == 2
    assert st["queue"]["since"] != first["since"]
    assert st["queue"]["left_at"] is None


def test_a_polled_queue_step_is_not_reported_as_executor_lag(store, clock):
    """``due_steps`` (health lag) dates a re-polled step from its last poll, not from the
    one history entry of its first block."""
    _, port = capped(store, clock)
    ex = Executor(store, "spark", {"*": port}, clock=clock)
    run = ex.start(rule(), agent_workflow())
    tick_until(ex, clock, T_DISPATCH + timedelta(minutes=30), every=1)
    doc = ex.run(run["id"])
    st = step_state(doc, "agent")
    clock.now = datetime.fromisoformat(st["next_attempt_at"])
    # one engine makes it pending (unblocked); another node has not dispatched it yet
    other = Executor(store, "thor", {}, clock=clock)
    new = runs_mod._housekeep(runs_mod._Plan.of(doc), doc, clock(), other.host)
    assert new is not None
    assert step_state(new, "agent")["status"] == "pending"
    found = due_steps(new, clock())
    assert found == [("agent", clock())]


def test_the_queue_bound_holds_while_the_step_is_pending_on_a_drained_node(store, clock):
    """Codex P2 on 88cb212: a queued step made ``pending`` by a due re-poll that its node
    cannot dispatch (drained) must still fail at its bound, not dispatch when undrained."""
    actor = FakeActor().on("agent", ("block", "at cap"))  # then: accepts at once
    ex = Executor(store, "spark", {"*": actor}, clock=clock)
    run = ex.start(rule(), agent_workflow(timeout_s=10))
    ex.run_until_idle()  # blocked at 0 s; the bound is 20 s
    runs_mod.Containment(store).drain("spark", "ops")
    clock.advance(5)
    ex.run_until_idle()  # the re-poll is due: pending, but a drained node dispatches nothing
    assert step_state(ex.run(run["id"]), "agent")["status"] == "pending"
    clock.advance(20)  # 25 s: past the bound while pending
    ex.run_until_idle()
    clock.advance(10)
    runs_mod.Containment(store).undrain("spark", "ops")
    ex.run_until_idle()
    st = step_state(ex.run(run["id"]), "agent")
    assert st["status"] == "failed", st
    assert st["error"]["code"] == runs_mod.QUEUE_TIMEOUT
    assert len(actor.calls_for("agent")) == 1  # never asked again past its bound
    assert actor.effects_for("agent") == 0


def test_a_dispatching_queued_step_past_its_bound_is_not_expired(store, clock):
    """A re-poll that is mid-dispatch may already have started the work: the queue bound
    never fails it; the resumed dispatch settles it."""
    actor = FakeActor().on("agent", ("block", "at cap"), ("crash", {}))
    ex = Executor(store, "spark", {"*": actor}, clock=clock)
    run = ex.start(rule(), agent_workflow(timeout_s=10))
    ex.run_until_idle()  # blocked at 0 s
    clock.advance(5)
    with pytest.raises(Crash):  # the re-poll started the work, then the engine died
        ex.run_until_idle()
    doc = ex.run(run["id"])
    st = step_state(doc, "agent")
    assert st["status"] == "dispatching"
    assert st["queue"]["left_at"] is None
    clock.advance(60)  # far past the 20 s queue bound
    plan = runs_mod._Plan.of(doc)
    assert runs_mod._due_timers(plan, doc, clock()) is None
    Executor(store, "spark", {"*": actor}, clock=clock).run_until_idle()  # restarted node
    st = step_state(ex.run(run["id"]), "agent")
    assert st["status"] == "succeeded", st["error"]
    assert actor.effects_for("agent") == 1


# ------------------------------------------------- upgrade: steps the pre-0.13 engine left


def legacy_blocked(store, clock, run_id: str, *, pending: bool = False) -> None:
    """Rewrite the run as the old engine persisted a blocked step: the working ``deadline``
    of its first (blocked) dispatch kept, a 5 s re-ask, and no ``queue`` record at all.
    ``pending``: the old ``unblocked`` shape (pending, ``resume`` set, deadline kept)."""
    doc = store.get(RUNS_COLLECTION, run_id)
    st = step_state(doc, "agent")
    assert st["status"] == "blocked"
    st.pop("queue", None)
    st["deadline"] = (T_DISPATCH + timedelta(seconds=600)).isoformat()
    st["next_attempt_at"] = (T_DISPATCH + timedelta(seconds=5)).isoformat()
    if pending:
        st.update(status="pending", next_attempt_at=None, resume=True)
    store.put(RUNS_COLLECTION, doc)


def blocked_once(store, clock) -> tuple[Executor, FakeActor, str]:
    actor = FakeActor().on("agent", *[("block", "at cap")] * 50)
    ex = Executor(store, "spark", {"*": actor}, clock=clock)
    run = ex.start(rule(), agent_workflow(timeout_s=600))  # bound: 1200 s
    ex.run_until_idle()
    return ex, actor, run["id"]


@pytest.mark.parametrize("pending", [False, True])
def test_a_legacy_blocked_step_adopts_a_bounded_queue_and_keeps_queuing(store, clock, pending):
    ex, actor, run_id = blocked_once(store, clock)
    legacy_blocked(store, clock, run_id, pending=pending)
    clock.now = T_DISPATCH + timedelta(seconds=700)  # past the old deadline, not the bound
    ex.run_until_idle()
    st = step_state(ex.run(run_id), "agent")
    assert st["status"] == "blocked", st["error"]
    assert st["queue"]["since"] == T_DISPATCH.isoformat()  # old deadline - timeout_s
    assert st["queue"]["deadline"] == (T_DISPATCH + timedelta(seconds=1200)).isoformat()
    assert st["deadline"] is None
    assert len(actor.calls_for("agent")) == 2  # asked again, still queued


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("drained", [False, True])
def test_a_legacy_blocked_step_past_its_bound_fails_without_dispatching(
    store, clock, pending, drained
):
    ex, actor, run_id = blocked_once(store, clock)
    legacy_blocked(store, clock, run_id, pending=pending)
    if drained:
        runs_mod.Containment(store).drain("spark", "ops")
    clock.now = T_DISPATCH + timedelta(seconds=1201)
    ex.run_until_idle()
    doc = ex.run(run_id)
    st = step_state(doc, "agent")
    assert st["status"] == "failed", st
    assert st["error"]["code"] == runs_mod.QUEUE_TIMEOUT
    assert doc["status"] == "failed"
    assert len(actor.calls_for("agent")) == 1  # never asked again


def test_a_legacy_blocked_step_without_a_deadline_dates_its_spell_from_history(store, clock):
    ex, actor, run_id = blocked_once(store, clock)
    legacy_blocked(store, clock, run_id)
    doc = store.get(RUNS_COLLECTION, run_id)
    step_state(doc, "agent")["deadline"] = None
    store.put(RUNS_COLLECTION, doc)
    clock.now = T_DISPATCH + timedelta(seconds=1201)
    ex.run_until_idle()
    st = step_state(ex.run(run_id), "agent")
    assert st["error"]["code"] == runs_mod.QUEUE_TIMEOUT  # since = its "blocked" entry
    assert len(actor.calls_for("agent")) == 1
