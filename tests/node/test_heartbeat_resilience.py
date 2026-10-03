"""A transient store error must never end the heartbeat loop (machine would go offline)."""

from __future__ import annotations

import logging

from culture_rules.machines.heartbeat import HEARTBEAT_INTERVAL_S, HeartbeatPublisher
from culture_rules.store.memory import MemoryStore
from culture_rules.store.port import StoreError, TransientStoreError


class FlakyStore:
    """Raises on the 2nd (and, optionally, 3rd) ``put``; otherwise delegates."""

    def __init__(self, inner: MemoryStore, errors: dict[int, Exception]) -> None:
        self._inner = inner
        self._errors = errors
        self.puts = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def put(self, collection, document):
        self.puts += 1
        error = self._errors.get(self.puts)
        if error is not None:
            raise error
        return self._inner.put(collection, document)


def test_run_keeps_beating_after_a_store_error_on_the_second_put(caplog):
    store = FlakyStore(
        MemoryStore(),
        {2: StoreError("primary stepped down"), 3: TransientStoreError("write conflict")},
    )
    sleeps: list[float] = []
    beat = HeartbeatPublisher(store, "spark", load_reader=lambda: {}, engine_version="t")
    with caplog.at_level(logging.WARNING, logger="culture_rules.machines.heartbeat"):
        beat.run(sleep=sleeps.append, max_beats=5)
    assert store.puts == 5  # every attempt was made; the loop never died
    assert sleeps == [HEARTBEAT_INTERVAL_S] * 4  # cadence kept
    assert store.get("heartbeats", "spark") is not None
    assert any("heartbeat" in r.getMessage() for r in caplog.records)
