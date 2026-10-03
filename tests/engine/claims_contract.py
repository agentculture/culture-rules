"""Shared contract suite for :mod:`culture_rules.engine.claims`.

Claims run on top of the StoragePort, so every storage adapter must make them
exactly-once. Bind an adapter exactly as for ``tests/store/contract.py``::

    from tests.engine.claims_contract import ClaimsContract

    class TestClaimsOnMyStore(ClaimsContract):
        def make_store(self, node_schema_version="1.0"):
            return MyStore(...)            # EMPTY backing data per call

        def open_peer(self, store, node_schema_version):
            return MyStore(...)            # another host on the SAME data

Each "engine instance" in these tests is a :class:`Claims` built on its own
peer handle with its own holder name, i.e. a separate host.

The class name does not start with ``Test`` so pytest never collects it alone.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.engine.claims import (
    CLAIMS_COLLECTION,
    Claims,
    firing_key,
    idempotency_key,
)
from culture_rules.store.port import StoragePort

T0 = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    """A settable clock shared by the engine instances of one test."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now = self.now + timedelta(**kwargs)


class ClaimsContract:
    """Exactly-once claim behaviour every StoragePort adapter must support."""

    # ------------------------------------------------------------------ hooks

    def make_store(self, node_schema_version: str = "1.0") -> StoragePort:  # pragma: no cover
        raise NotImplementedError

    def open_peer(
        self, store: StoragePort, node_schema_version: str
    ) -> StoragePort:  # pragma: no cover
        raise NotImplementedError

    @pytest.fixture
    def store(self) -> StoragePort:
        return self.make_store()

    @pytest.fixture
    def clock(self) -> FakeClock:
        return FakeClock()

    def engine(self, store: StoragePort, holder: str, clock, *, lease: float = 30.0) -> Claims:
        """A separate engine instance (host) on its own peer handle."""
        peer = self.open_peer(store, str(store.node_schema_version))
        return Claims(peer, holder, lease=timedelta(seconds=lease), clock=clock)

    # -------------------------------------------------------------- winning

    def test_first_claim_wins_and_records_key(self, store, clock):
        a = self.engine(store, "spark", clock)
        result = a.claim_step("run-1", "step-a")
        key = idempotency_key("run-1", "step-a")
        assert result.won is True
        assert result.key == key
        assert result.holder == "spark"
        assert result.attempt == 1
        assert result.reason == "acquired"
        doc = store.get(CLAIMS_COLLECTION, key)
        assert doc["holder"] == "spark"
        assert doc["status"] == "claimed"
        assert doc["kind"] == "step"
        assert doc["run_id"] == "run-1"
        assert doc["step_id"] == "step-a"
        assert datetime.fromisoformat(doc["lease_expires_at"]) == T0 + timedelta(seconds=30)

    def test_second_instance_loses_while_lease_is_live(self, store, clock):
        a = self.engine(store, "spark", clock)
        b = self.engine(store, "thor", clock)
        assert a.claim_step("run-1", "step-a").won
        lost = b.claim_step("run-1", "step-a")
        assert lost.won is False
        assert lost.reason == "held"
        assert lost.holder == "spark"
        assert lost.key == idempotency_key("run-1", "step-a")

    def test_same_holder_does_not_win_twice(self, store, clock):
        a = self.engine(store, "spark", clock)
        assert a.claim_step("run-1", "step-a").won
        assert a.claim_step("run-1", "step-a").won is False

    def test_distinct_steps_are_independent(self, store, clock):
        a = self.engine(store, "spark", clock)
        b = self.engine(store, "thor", clock)
        assert a.claim_step("run-1", "step-a").won
        assert b.claim_step("run-1", "step-b").won
        assert b.claim_step("run-2", "step-a").won

    def test_racing_instances_exactly_one_wins(self, store, clock):
        engines = [self.engine(store, f"host-{i}", clock) for i in range(8)]
        barrier = threading.Barrier(len(engines))
        results = []
        lock = threading.Lock()

        def race(engine: Claims) -> None:
            barrier.wait()
            outcome = engine.claim_step("run-race", "step-x")
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=race, args=(e,)) for e in engines]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert len(results) == len(engines)
        winners = [r for r in results if r.won]
        assert len(winners) == 1
        doc = store.get(CLAIMS_COLLECTION, idempotency_key("run-race", "step-x"))
        assert doc["holder"] == winners[0].holder
        assert doc["attempt"] == 1

    def test_racing_reclaim_exactly_one_wins(self, store, clock):
        crashed = self.engine(store, "crashed", clock, lease=5)
        assert crashed.claim_step("run-1", "step-a").won
        clock.advance(seconds=6)
        engines = [self.engine(store, f"host-{i}", clock) for i in range(8)]
        barrier = threading.Barrier(len(engines))
        results = []
        lock = threading.Lock()

        def race(engine: Claims) -> None:
            barrier.wait()
            outcome = engine.claim_step("run-1", "step-a")
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=race, args=(e,)) for e in engines]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        winners = [r for r in results if r.won]
        assert len(winners) == 1
        assert winners[0].reason == "reclaimed"
        assert winners[0].attempt == 2

    def test_firing_claim_is_exactly_once(self, store, clock):
        a = self.engine(store, "spark", clock)
        b = self.engine(store, "thor", clock)
        won = a.claim_firing("rule-1", "event-9")
        assert won.won and won.key == firing_key("rule-1", "event-9")
        assert b.claim_firing("rule-1", "event-9").won is False
        doc = store.get(CLAIMS_COLLECTION, won.key)
        assert doc["kind"] == "firing"
        assert doc["rule_id"] == "rule-1"
        assert doc["event_id"] == "event-9"

    # --------------------------------------------------------------- leases

    def test_expired_lease_is_reclaimable(self, store, clock):
        crashed = self.engine(store, "spark", clock, lease=10)
        rescuer = self.engine(store, "thor", clock)
        first = crashed.claim_step("run-1", "step-a")
        clock.advance(seconds=9)
        assert rescuer.claim_step("run-1", "step-a").won is False
        clock.advance(seconds=2)
        second = rescuer.claim_step("run-1", "step-a")
        assert second.won is True
        assert second.reason == "reclaimed"
        assert second.attempt == first.attempt + 1
        assert second.key == first.key  # the key never depends on the attempt
        doc = store.get(CLAIMS_COLLECTION, first.key)
        assert doc["holder"] == "thor"
        assert doc["previous_holder"] == "spark"

    def test_renew_extends_the_lease(self, store, clock):
        a = self.engine(store, "spark", clock, lease=10)
        b = self.engine(store, "thor", clock)
        claim = a.claim_step("run-1", "step-a")
        clock.advance(seconds=8)
        renewed = a.renew(claim)
        assert renewed.won is True
        clock.advance(seconds=8)  # 16s after the claim, 8s after renewal
        assert b.claim_step("run-1", "step-a").won is False

    def test_stale_holder_cannot_renew_complete_or_release_after_reclaim(self, store, clock):
        stale = self.engine(store, "spark", clock, lease=5)
        fresh = self.engine(store, "thor", clock)
        old = stale.claim_step("run-1", "step-a")
        clock.advance(seconds=6)
        new = fresh.claim_step("run-1", "step-a")
        assert new.won
        assert stale.renew(old).won is False
        assert stale.complete(old) is False
        assert stale.release(old) is False
        doc = store.get(CLAIMS_COLLECTION, old.key)
        assert doc["holder"] == "thor"
        assert doc["status"] == "claimed"

    def test_released_claim_can_be_taken_immediately(self, store, clock):
        a = self.engine(store, "spark", clock)
        b = self.engine(store, "thor", clock)
        claim = a.claim_step("run-1", "step-a")
        assert a.release(claim) is True
        taken = b.claim_step("run-1", "step-a")
        assert taken.won is True
        assert taken.attempt == 2

    # ----------------------------------------------------------- completion

    def test_completed_step_is_never_reclaimed(self, store, clock):
        a = self.engine(store, "spark", clock, lease=5)
        b = self.engine(store, "thor", clock)
        claim = a.claim_step("run-1", "step-a")
        assert a.complete(claim, result={"ok": True}) is True
        clock.advance(days=30)
        again = b.claim_step("run-1", "step-a")
        assert again.won is False
        assert again.reason == "completed"
        assert again.document["result"] == {"ok": True}
        assert b.is_completed(claim.key) is True
        assert a.complete(claim) is False  # completion is recorded once

    def test_complete_inside_a_transaction_with_the_step_result(self, store, clock):
        a = self.engine(store, "spark", clock)
        claim = a.claim_step("run-1", "step-a")
        with store.transaction() as tx:
            tx.put("step_results", {"id": claim.key, "value": 42})
            assert a.with_ops(tx).complete(claim) is True
        assert store.get("step_results", claim.key)["value"] == 42
        assert store.get(CLAIMS_COLLECTION, claim.key)["status"] == "completed"

    def test_failed_transaction_leaves_step_claimed(self, store, clock):
        a = self.engine(store, "spark", clock)
        claim = a.claim_step("run-1", "step-a")

        def crash_before_commit():
            with store.transaction() as tx:
                tx.put("step_results", {"id": claim.key, "value": 42})
                a.with_ops(tx).complete(claim)
                raise RuntimeError("crash before commit")

        with pytest.raises(RuntimeError):
            crash_before_commit()
        assert store.get(CLAIMS_COLLECTION, claim.key)["status"] == "claimed"
        assert a.is_completed(claim.key) is False
