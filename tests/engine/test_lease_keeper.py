"""A step's claim lease stays alive while its actor blocks (exactly-once, h136).

The executor invokes an actor synchronously and an adapter may block for minutes; the
claim lease is 30 s. These tests run two engine instances ("spark" and "thor") on one
store, each with its *own* adapter instance (as two real hosts would), and make spark's
invocation outlast the lease while thor keeps ticking. The work must happen once and be
settled by the host that started it; a crashed holder must still be recovered.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from typing import Any

import pytest

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.claims import CLAIMS_COLLECTION, idempotency_key
from culture_rules.engine.leasekeeper import LeaseKeeper
from culture_rules.engine.runs import RUNS_COLLECTION, Executor, step_state
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, Crash, FakeActor, rule, step, workflow

LEASE = timedelta(seconds=30)


# --------------------------------------------------------------------------- fixtures


class DrivenKeepers:
    """A ``lease_keeper`` factory whose renewals happen exactly when the test asks."""

    def __init__(self) -> None:
        self.live: list[Callable[[], bool]] = []
        self.intervals: list[float] = []
        self.exited = 0

    def __call__(self, renew: Callable[[], bool], interval: float) -> Any:
        self.intervals.append(interval)
        return self._keep(renew)

    @contextmanager
    def _keep(self, renew: Callable[[], bool]) -> Iterator[None]:
        self.live.append(renew)
        try:
            yield
        finally:
            self.live.remove(renew)
            self.exited += 1

    def renew_all(self) -> list[bool]:
        return [renew() for renew in list(self.live)]


class SlowPort:
    """Wraps one host's own FakeActor; while invoking ``s1`` it runs ``during`` first."""

    supports_idempotency_key = True

    def __init__(self, actor: FakeActor, during: Callable[[], None] | None = None) -> None:
        self.actor = actor
        self.during = during

    def invoke(
        self,
        payload: Mapping[str, Any],
        idempotency_key: str,
        deadline: Any,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        if context.step_id == "s1" and self.during is not None:
            during, self.during = self.during, None  # only the first invocation is slow
            during()
        return self.actor.invoke(payload, idempotency_key, deadline, context=context)


def beat(store: MemoryStore, clock: Clock, machine: str) -> None:
    """Write ``machine``'s heartbeat at ``clock()`` (what its heartbeat thread does)."""
    ts = clock().strftime("%Y-%m-%dT%H:%M:%SZ")
    store.put(HEARTBEAT_COLLECTION, {"id": machine, "machine": machine, "ts": ts})


def effects(*actors: FakeActor) -> int:
    return sum(a.effects_for("s1") for a in actors)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def base(clock: Clock) -> MemoryStore:
    return MemoryStore(clock=clock)


def executor(base: MemoryStore, host: str, port: Any, clock: Clock, **kw: Any) -> Executor:
    kw.setdefault("lease", LEASE)
    return Executor(base.peer(), host, {"*": port}, clock=clock, **kw)


def start_run(ex: Executor) -> str:
    return ex.start(rule(), workflow((step("s1"),)))["id"]


# --------------------------------------------------------------------------- scenarios


def test_slow_invoke_keeps_its_lease_so_a_peer_never_takes_the_step_over(base, clock):
    keepers = DrivenKeepers()
    spark_actor, thor_actor = FakeActor(), FakeActor()
    thor = executor(base, "thor", SlowPort(thor_actor), clock, lease_keeper=keepers)

    renewed: list[list[bool]] = []

    def slow() -> None:  # a 40 s step; the keeper renews every lease/3 = 10 s
        for _ in range(4):
            clock.advance(10)
            renewed.append(keepers.renew_all())
        thor.run_until_idle()  # thor's node cycle runs while spark is still invoking

    spark = executor(base, "spark", SlowPort(spark_actor, slow), clock, lease_keeper=keepers)
    run_id = start_run(spark)
    spark.run_until_idle()
    thor.run_until_idle()

    doc = base.get(RUNS_COLLECTION, run_id)
    s1 = step_state(doc, "s1")
    assert renewed == [[True]] * 4
    assert effects(spark_actor, thor_actor) == 1
    assert thor_actor.calls_for("s1") == []
    assert s1["status"] == "succeeded"
    assert s1["host"] == "spark"
    settled = [h["host"] for h in doc["history"] if h["step"] == "s1"]
    assert settled == ["spark", "spark"]  # dispatched + succeeded, both by spark
    assert doc["status"] == "succeeded"
    assert keepers.intervals[0] == pytest.approx(10.0)
    assert keepers.live == []


def test_no_takeover_of_a_lapsed_lease_while_the_holder_is_online(base, clock):
    spark_actor, thor_actor = FakeActor(), FakeActor()
    no_renewals = lambda renew, interval: nullcontext()  # noqa: E731 - lease really lapses
    thor = executor(base, "thor", SlowPort(thor_actor), clock, lease_keeper=no_renewals)
    seen: dict[str, Any] = {}

    def slow() -> None:  # 40 s; spark's heartbeat thread keeps beating meanwhile
        for _ in range(4):
            clock.advance(10)
            beat(base, clock, "spark")
        claim = base.get(CLAIMS_COLLECTION, idempotency_key(run_id, "s1"))
        seen["lapsed"] = datetime.fromisoformat(claim["lease_expires_at"]) < clock()
        seen["thor_made"] = thor.run_until_idle()

    spark = executor(base, "spark", SlowPort(spark_actor, slow), clock, lease_keeper=no_renewals)
    beat(base, clock, "spark")
    run_id = start_run(spark)
    spark.run_until_idle()
    thor.run_until_idle()

    s1 = step_state(base.get(RUNS_COLLECTION, run_id), "s1")
    assert seen["lapsed"] is True  # only the heartbeat gate stood in thor's way
    assert seen["thor_made"] == 0
    assert thor_actor.calls_for("s1") == []
    assert effects(spark_actor, thor_actor) == 1
    assert s1["status"] == "succeeded"
    assert s1["host"] == "spark"


def test_a_crashed_holder_is_taken_over_once_its_heartbeat_goes_stale(base, clock):
    lease = timedelta(seconds=5)
    spark_actor = FakeActor().on("s1", ("crash", {}))  # effect done, then the host dies
    thor_actor = FakeActor()
    spark = executor(base, "spark", spark_actor, clock, lease=lease)
    thor = executor(base, "thor", thor_actor, clock, lease=lease)
    beat(base, clock, "spark")
    run_id = start_run(spark)

    with pytest.raises(Crash):
        spark.run_until_idle()
    assert not any(t.name == "lease-keeper" for t in threading.enumerate())

    clock.advance(6)  # lease lapsed, but spark's last beat is only 6 s old
    assert thor.run_until_idle() == 0
    assert thor_actor.calls_for("s1") == []

    clock.advance(25)  # 31 s without a beat: spark is offline
    thor.run_until_idle()
    doc = base.get(RUNS_COLLECTION, run_id)
    s1 = step_state(doc, "s1")
    assert effects(spark_actor, thor_actor) >= 1
    assert len(thor_actor.calls_for("s1")) == 1
    assert s1["host"] == "thor"
    assert doc["status"] == "succeeded"


def test_the_holders_own_host_reclaims_after_the_lease_whatever_its_heartbeat(base, clock):
    actor = FakeActor().on("s1", ("crash", {}))
    spark = executor(base, "spark", actor, clock)
    beat(base, clock, "spark")
    run_id = start_run(spark)
    with pytest.raises(Crash):
        spark.run_until_idle()

    clock.advance(31)
    beat(base, clock, "spark")  # the restarted node beats again
    restarted = executor(base, "spark", actor, clock)
    restarted.run_until_idle()
    assert base.get(RUNS_COLLECTION, run_id)["status"] == "succeeded"
    assert actor.effects_for("s1") == 1  # same key: the actor deduplicated the re-invoke


def test_a_step_past_its_deadline_is_taken_over_even_from_an_online_holder(base, clock):
    no_renewals = lambda renew, interval: nullcontext()  # noqa: E731
    spark_actor = FakeActor().on("s1", ("crash", {}))
    thor_actor = FakeActor()
    spark = executor(base, "spark", spark_actor, clock, lease_keeper=no_renewals)
    thor = executor(base, "thor", thor_actor, clock)
    run_id = spark.start(rule(), workflow((step("s1", timeout_s=60),)))["id"]
    with pytest.raises(Crash):
        spark.run_until_idle()

    clock.advance(59)
    beat(base, clock, "spark")
    assert thor.run_until_idle() == 0  # lapsed lease, online holder, deadline ahead
    clock.advance(2)
    beat(base, clock, "spark")
    thor.run_until_idle()
    assert step_state(base.get(RUNS_COLLECTION, run_id), "s1")["host"] == "thor"


def test_the_keeper_stops_renewing_once_the_step_deadline_passed(base, clock):
    keepers = DrivenKeepers()
    renewed: list[list[bool]] = []

    def slow() -> None:
        clock.advance(10)
        renewed.append(keepers.renew_all())
        clock.advance(60)
        renewed.append(keepers.renew_all())

    spark = executor(base, "spark", SlowPort(FakeActor(), slow), clock, lease_keeper=keepers)
    spark.start(rule(), workflow((step("s1", timeout_s=30),)))
    spark.run_until_idle()
    assert renewed == [[True], [False]]


def test_the_real_keeper_renews_from_a_thread_while_invoke_blocks(base, clock):
    """End to end with :class:`LeaseKeeper` (interval = lease/3 = 0.1 s, real time)."""
    lease = timedelta(seconds=0.3)
    spark_actor, thor_actor = FakeActor(), FakeActor()
    thor = executor(base, "thor", SlowPort(thor_actor), clock, lease=lease)
    rounds: list[bool] = []

    def renewed_past_now() -> bool:
        claim = base.get(CLAIMS_COLLECTION, idempotency_key(run_id, "s1"))
        return datetime.fromisoformat(claim["lease_expires_at"]) > clock()

    def slow() -> None:  # outlives the lease three times over (in fake time)
        for _ in range(3):
            clock.advance(0.35)  # past any earlier lease: only a fresh renewal covers now
            deadline = time.monotonic() + 10
            while not renewed_past_now() and time.monotonic() < deadline:
                time.sleep(0.01)
            rounds.append(renewed_past_now())
            thor.run_until_idle()

    spark = executor(base, "spark", SlowPort(spark_actor, slow), clock, lease=lease)
    run_id = start_run(spark)
    spark.run_until_idle()

    assert rounds == [True, True, True]
    assert thor_actor.calls_for("s1") == []
    assert effects(spark_actor, thor_actor) == 1
    assert step_state(base.get(RUNS_COLLECTION, run_id), "s1")["host"] == "spark"
    assert not any(t.name == "lease-keeper" for t in threading.enumerate())


# --------------------------------------------------------------------------- LeaseKeeper


def wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_keeper_renews_on_its_interval_and_stops_with_the_block():
    calls: list[float] = []
    with LeaseKeeper(lambda: calls.append(time.monotonic()) or True, 0.01) as keeper:
        assert wait_for(lambda: len(calls) >= 3)
        assert keeper.alive
    assert not keeper.alive
    stopped_at = len(calls)
    time.sleep(0.05)
    assert len(calls) == stopped_at
    assert keeper.renewals >= 3


def _crash_inside(keeper: LeaseKeeper) -> None:
    with keeper:
        raise Crash("the invocation died")


def test_keeper_stops_cleanly_when_the_block_raises():
    keeper = LeaseKeeper(lambda: True, 0.01)
    with pytest.raises(Crash):
        _crash_inside(keeper)
    assert not keeper.alive


def test_keeper_gives_up_once_the_lease_is_lost():
    results = iter([True, False])
    calls: list[int] = []

    def renew() -> bool:
        calls.append(1)
        return next(results)

    with LeaseKeeper(renew, 0.01) as keeper:
        assert wait_for(lambda: not keeper.alive)
    assert keeper.lost is True
    assert len(calls) == 2
    assert keeper.renew_now() is False  # a lost lease stays lost


def test_keeper_survives_a_transient_renew_error():
    outcomes: list[Any] = [RuntimeError("primary stepped down"), True, True]
    calls: list[int] = []

    def renew() -> bool:
        calls.append(1)
        outcome = outcomes.pop(0) if outcomes else True
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with LeaseKeeper(renew, 0.01) as keeper:
        assert wait_for(lambda: keeper.renewals >= 2)
    assert keeper.lost is False


def test_keeper_renew_now_is_a_deterministic_hook():
    keeper = LeaseKeeper(lambda: True, 3600)
    assert keeper.renew_now() is True
    assert keeper.renewals == 1
    assert not keeper.alive  # never started: no thread


@pytest.mark.parametrize("interval", [0, -1.0, float("nan")])
def test_keeper_refuses_a_non_positive_interval(interval):
    with pytest.raises(ValueError, match="interval"):
        LeaseKeeper(lambda: True, interval)
