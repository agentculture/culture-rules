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
    ingester = EventIngest(store, src, host="h")
    with pytest.raises(TransientStoreError):
        ingester.ingest()
    assert store.find(QUARANTINE_COLLECTION) == []
    assert store.load_cursor("ingest@h", f"source:{src.name}") is None


# --------------------------------------------------------------------------- Codex round 6
# Only KNOWN content errors are quarantined; anything else re-raises like an outage (the
# batch is retried): stalling is recoverable, losing a valid event is not.


class OperationFailure(Exception):  # pymongo.errors.OperationFailure, by name
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class WriteConcernError(OperationFailure):
    pass


class InvalidDocument(Exception):  # bson.errors.InvalidDocument, by name
    pass


class InvalidStringData(Exception):
    pass


class DocumentTooLarge(InvalidDocument):
    pass


def _unicode_error():
    return UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed")


CONTENT_ERRORS = {
    "InvalidDocument": lambda: InvalidDocument("key 'a\x00' must not contain NUL"),
    "InvalidStringData": lambda: InvalidStringData("strings must be UTF-8"),
    "DocumentTooLarge": lambda: DocumentTooLarge("BSON document too large"),
    "OverflowError": lambda: OverflowError("MongoDB can only handle up to 8-byte ints"),
    "RecursionError": lambda: RecursionError("maximum recursion depth exceeded"),
    "UnicodeEncodeError": _unicode_error,
}
OTHER_ERRORS = {
    "OperationFailure_112": (
        OperationFailure,
        lambda: OperationFailure("WriteConflict", code=112),
    ),
    "WriteConcernError": (
        WriteConcernError,
        lambda: WriteConcernError("waiting for replication timed out"),
    ),
    "RuntimeError": (RuntimeError, lambda: RuntimeError("something unknown")),
    "TransientStoreError": (
        TransientStoreError,
        lambda: TransientStoreError("the replica set has no primary"),
    ),
}


class RaisingStore(MemoryStore):
    """A store whose insert of event ``evt_1`` (bus) or of any event (webhook) raises."""

    def __init__(self, make, only="evt_1"):
        super().__init__()
        self.make, self.only = make, only

    def insert(self, collection, document):
        if collection == EVENTS_COLLECTION and self.only in (None, document["id"]):
            raise self.make()
        return super().insert(collection, document)


@pytest.mark.parametrize("name", sorted(CONTENT_ERRORS))
def test_a_known_content_error_quarantines_and_the_batch_goes_on(name):
    store = RaisingStore(CONTENT_ERRORS[name])
    src = FakeEventSource([envelope(1), envelope(2)])
    (result,) = EventIngest(store, src, host="h").ingest()
    assert result.quarantined == 1
    assert [e["id"] for e in store.find(EVENTS_COLLECTION)] == ["evt_2"]
    [record] = store.find(QUARANTINE_COLLECTION)
    assert name in record["reason"]
    assert storable(record)
    assert store.load_cursor("ingest@h", f"source:{src.name}") is not None


@pytest.mark.parametrize("name", sorted(OTHER_ERRORS))
def test_anything_else_re_raises_with_no_quarantine_and_no_cursor(name):
    cls, make = OTHER_ERRORS[name]
    store = RaisingStore(make)
    src = FakeEventSource([envelope(1), envelope(2)])
    ingester = EventIngest(store, src, host="h")
    with pytest.raises(cls) as err:
        ingester.ingest()
    assert type(err.value) is cls
    assert store.find(QUARANTINE_COLLECTION) == []
    assert store.load_cursor("ingest@h", f"source:{src.name}") is None


def _hook(store):
    actor = {"id": "a", "kind": "app", "params": {"surface": "github", "events": ["t.x"]}}
    return sink(store, actor, "t.x", {"n": 1}, "d1", "alice")


@pytest.mark.parametrize("name", sorted(CONTENT_ERRORS))
def test_the_webhook_sink_quarantines_a_known_content_error(name):
    store = RaisingStore(CONTENT_ERRORS[name], only=None)
    assert _hook(store) == "quarantined"
    assert len(store.find(QUARANTINE_COLLECTION)) == 1


@pytest.mark.parametrize("name", sorted(OTHER_ERRORS))
def test_the_webhook_sink_re_raises_anything_else(name):
    cls, make = OTHER_ERRORS[name]
    store = RaisingStore(make, only=None)
    with pytest.raises(cls) as err:
        _hook(store)
    assert type(err.value) is cls
    assert store.find(QUARANTINE_COLLECTION) == []


def test_a_duplicate_event_is_counted_not_quarantined_nor_raised():
    store = MemoryStore()
    result = ingest(store, envelope(1), envelope(1))  # a redelivery
    assert (result.inserted, result.duplicates, result.quarantined) == (1, 1, 0)
    assert store.find(QUARANTINE_COLLECTION) == []


def test_minimal_records_dedupe_on_the_full_id_not_its_preview():
    store = MemoryStore()
    long_a, long_b = "x" * 200 + "a" + "x" * 200, "x" * 200 + "b" + "x" * 200
    for eid in (long_a, long_b):
        assert quarantine(store, {"id": eid, "data": {"$k": 1}}, "same", host="h") is True
    records = store.find(QUARANTINE_COLLECTION)
    assert len(records) == 2


class LosesTheFirstBump(MemoryStore):
    """Another host bumps the record's count just before this host's first update_if."""

    raced = False

    def update_if(self, collection, id, expected, changes, *, upsert=False):
        if collection == QUARANTINE_COLLECTION and not self.raced:
            self.raced = True
            current = self.get(collection, id)
            super().update_if(collection, id, {}, {"count": current["count"] + 1})
        return super().update_if(collection, id, expected, changes, upsert=upsert)


def test_concurrent_minimal_quarantines_lose_no_count():
    store = LosesTheFirstBump()
    env = {"id": "e1", "data": {"$k": 1}}
    quarantine(store, env, "r", host="h")
    quarantine(store, env, "r", host="h")  # races the other host's bump, retries
    [record] = store.find(QUARANTINE_COLLECTION)
    assert record["count"] == 3
