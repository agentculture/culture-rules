"""Ingest edge cases on the in-memory store: bounds, rejects, cursor handling."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from culture_rules.events.ingest import (
    EVENTS_COLLECTION,
    MAX_BATCH,
    EventIngest,
    event_document,
)
from culture_rules.events.source import EventFabricError, EventSource
from culture_rules.store.memory import MemoryStore
from tests.events.fakes import FakeEventSource, envelope


def test_fake_source_satisfies_the_protocol():
    assert isinstance(FakeEventSource(), EventSource)


def test_batches_are_bounded_by_batch_size():
    source = FakeEventSource([envelope(i) for i in range(10)])
    results = EventIngest(MemoryStore(), source, host="h", batch_size=3).ingest(max_batches=10)
    assert [r.received for r in results] == [3, 3, 3, 1]
    assert all(call["max"] == 3 for call in source.calls)
    assert results[-1].has_more is False


def test_ingest_stops_after_max_batches_even_if_more_is_queued():
    source = FakeEventSource([envelope(i) for i in range(10)])
    results = EventIngest(MemoryStore(), source, host="h", batch_size=2).ingest(max_batches=2)
    assert len(results) == 2
    assert results[-1].has_more is True


@pytest.mark.parametrize("size", [0, -1, MAX_BATCH + 1, 1.5, True])
def test_batch_size_must_be_a_bounded_positive_int(size):
    store, source = MemoryStore(), FakeEventSource()
    with pytest.raises(ValueError):
        EventIngest(store, source, host="h", batch_size=size)


@pytest.mark.parametrize("host", ["", None, 3])
def test_host_is_required(host):
    store, source = MemoryStore(), FakeEventSource()
    with pytest.raises(ValueError):
        EventIngest(store, source, host=host)


def test_a_source_that_overdelivers_is_refused_and_nothing_is_inserted():
    source = FakeEventSource([envelope(i) for i in range(5)])
    source.overdeliver = True
    store = MemoryStore()
    ingest = EventIngest(store, source, host="h", batch_size=2)
    with pytest.raises(EventFabricError, match="bound"):
        ingest.ingest_once()
    assert store.find(EVENTS_COLLECTION) == []


def test_envelopes_without_a_usable_id_are_rejected_not_stored():
    bad = [{"type": "x"}, {"id": ""}, {"id": 5}, "not-a-mapping", envelope(1)]
    store = MemoryStore()
    (result,) = EventIngest(store, FakeEventSource(bad), host="h").ingest()
    assert (result.inserted, result.rejected) == (1, 4)
    assert [d["id"] for d in store.find(EVENTS_COLLECTION)] == ["evt_1"]


def test_cursor_is_saved_only_after_the_batch_is_stored():
    store = MemoryStore()
    source = FakeEventSource([envelope(1), envelope(2)])
    ingest = EventIngest(store, source, host="h")
    assert store.load_cursor(ingest.consumer, ingest.cursor_key) is None
    ingest.ingest()
    assert store.load_cursor(ingest.consumer, ingest.cursor_key) == "2"


def test_an_empty_drain_keeps_the_cursor():
    store = MemoryStore()
    ingest = EventIngest(store, FakeEventSource(), host="h")
    (result,) = ingest.ingest()
    assert result.received == 0
    assert result.cursor is None
    assert store.load_cursor(ingest.consumer, ingest.cursor_key) is None


def test_each_host_has_its_own_cursor():
    store = MemoryStore()
    a = EventIngest(store, FakeEventSource(name="s"), host="a")
    b = EventIngest(store, FakeEventSource(name="s"), host="b")
    assert a.consumer != b.consumer


def test_event_document_shape_uses_the_clock():
    moment = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    doc = event_document(envelope(1), host="h", received_at=moment)
    assert doc == {
        "id": "evt_1",
        "envelope": envelope(1),
        "received_at": "2026-10-03T12:00:00.000000+00:00",
        "host": "h",
    }


def test_event_document_copies_the_envelope():
    env = envelope(1)
    doc = event_document(env, host="h")
    env["data"]["nested"]["k"].append(3)
    assert doc["envelope"]["data"]["nested"]["k"] == [1, 2]


def test_ingest_uses_its_clock_for_received_at():
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    store = MemoryStore()
    EventIngest(store, FakeEventSource([envelope(1)]), host="h", clock=lambda: moment).ingest()
    assert store.get(EVENTS_COLLECTION, "evt_1")["received_at"].startswith("2026-01-01T00:00")
