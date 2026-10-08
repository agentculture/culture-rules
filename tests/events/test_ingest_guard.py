"""Codex round 5 (mostly pre-existing, closed alongside d25): ingest never raises on
envelope content.

Valid JSON a store cannot hold (a NUL in a key, a ``$``-prefixed key, an int outside signed
64-bit) or cannot walk (deep nesting) used to raise while validating, copying, storing or
quarantining, which stalled the bus batch before its cursor was saved and failed the
webhook. Now :func:`~culture_rules.events.emit.reserved_reason` refuses those shapes
up front (iteratively), quarantine never copies an unbounded or unstorable payload, and a
general guard turns anything else an envelope's content makes raise into a minimal,
always-storable quarantine record. A genuine store outage still propagates as before.
"""

from __future__ import annotations

import json

import pytest

from culture_rules.events.emit import envelope_shape_problem, reserved_reason
from culture_rules.events.hook_sink import sink
from culture_rules.events.ingest import (
    EVENTS_COLLECTION,
    QUARANTINE_COLLECTION,
    EventIngest,
    quarantine,
)
from culture_rules.store.memory import MemoryStore
from culture_rules.store.port import TransientStoreError
from tests.events.fakes import FakeEventSource, envelope


def nested(depth: int):
    value: object = 1
    for _ in range(depth):
        value = [value]
    return value


REFUSED = {
    "nul_key": {"a\x00b": 1},
    "dollar_key": {"$where": 1},
    "int_2_63": 2**63,
    "int_below": -(2**63) - 1,
    "int_2_100": 2**100,
    "nest_33": nested(33),
    "nest_500": nested(500),
    "non_str_key": {1: "x"},
}
ACCEPTED = {
    "nan": float("nan"),
    "inf": float("inf"),
    "dotted_key": {"a.b": 1},  # MongoDB 8 stores dotted field names
    "int_max": 2**63 - 1,
    "int_min": -(2**63),
    "nest_28": nested(28),
}


def storable(doc) -> bool:
    """A quarantine record a store can hold: encodable text, sane keys and ints, shallow."""
    if envelope_shape_problem(doc) is not None:
        return False
    json.dumps(doc, ensure_ascii=False, default=str).encode("utf-8")
    return True


def ingest(store, *envelopes):
    (result,) = EventIngest(store, FakeEventSource(list(envelopes)), host="h").ingest()
    return result


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_an_unstorable_shape_is_refused_up_front(name):
    assert reserved_reason(envelope(1, data={"x": REFUSED[name]}))


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_a_storable_shape_still_passes(name):
    assert reserved_reason(envelope(1, data={"x": ACCEPTED[name]})) is None


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_the_bus_quarantines_it_and_moves_on(name):
    store = MemoryStore()
    src = FakeEventSource([envelope(1, data={"x": REFUSED[name]}), envelope(2)])
    (result,) = EventIngest(store, src, host="h").ingest()
    assert result.quarantined == 1
    assert [e["id"] for e in store.find(EVENTS_COLLECTION)] == ["evt_2"]
    assert all(storable(q) for q in store.find(QUARANTINE_COLLECTION))
    (again,) = EventIngest(store, src, host="h").ingest()
    assert again.received == 0


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_the_bus_stores_a_storable_shape(name):
    store = MemoryStore()
    result = ingest(store, envelope(1, data={"x": ACCEPTED[name]}), envelope(2))
    assert result.inserted == 2


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_the_webhook_sink_quarantines_it(name):
    store = MemoryStore()
    actor = {"id": "a", "kind": "app", "params": {"surface": "github", "events": ["t.x"]}}
    assert sink(store, actor, "t.x", {"x": REFUSED[name]}, "d1", "alice") == "quarantined"
    assert store.find(EVENTS_COLLECTION) == []
    assert all(storable(q) for q in store.find(QUARANTINE_COLLECTION))


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_quarantine_itself_never_raises_on_it(name):
    store = MemoryStore()
    env = {**envelope(1, data={"x": REFUSED[name]}), "type": REFUSED[name]}
    assert quarantine(store, env, "test", host="h") is True
    assert all(storable(q) for q in store.find(QUARANTINE_COLLECTION))


class ContentRefusingStore(MemoryStore):
    """A store that refuses one event's content (as MongoDB would with InvalidDocument)."""

    def insert(self, collection, document):
        if collection == EVENTS_COLLECTION and document["id"] == "evt_1":
            raise OverflowError("MongoDB can only handle up to 8-byte ints")
        return super().insert(collection, document)


class DownStore(MemoryStore):
    def insert(self, collection, document):
        if collection == EVENTS_COLLECTION:
            raise TransientStoreError("the replica set has no primary")
        return super().insert(collection, document)


def test_anything_else_the_content_makes_raise_is_a_minimal_quarantine_record():
    store = ContentRefusingStore()
    result = ingest(store, envelope(1), envelope(2))
    assert result.quarantined == 1
    assert [e["id"] for e in store.find(EVENTS_COLLECTION)] == ["evt_2"]
    [record] = store.find(QUARANTINE_COLLECTION)
    assert "OverflowError" in record["reason"]
    assert storable(record)


def test_a_store_outage_still_propagates_and_the_cursor_stays():
    store = DownStore()
    src = FakeEventSource([envelope(1)])
    with pytest.raises(TransientStoreError):
        EventIngest(store, src, host="h").ingest()
    assert store.find(QUARANTINE_COLLECTION) == []
    assert store.load_cursor("ingest@h", f"source:{src.name}") is None
