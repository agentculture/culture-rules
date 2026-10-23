"""Trigger edge cases on the in-memory store."""

from __future__ import annotations

import pytest

from culture_rules.events.ingest import EVENTS_COLLECTION, EventIngest
from culture_rules.events.triggers import FIRES_COLLECTION, EventTriggers
from culture_rules.store.memory import MemoryStore
from tests.events.fakes import FakeEventSource, envelope


def _setup():
    store = MemoryStore()
    source = FakeEventSource()
    return store, source, EventIngest(store, source, host="h")


def test_first_poll_persists_the_start_token_so_failover_cannot_skip():
    store, source, ingest = _setup()
    triggers = EventTriggers(store, lambda tx, d: None, host="h")
    assert store.load_cursor(triggers.consumer, EVENTS_COLLECTION) is None
    head = store.head(EVENTS_COLLECTION)
    triggers.poll()
    assert store.load_cursor(triggers.consumer, EVENTS_COLLECTION) == head


def test_poll_reports_fired_and_skipped():
    store, source, ingest = _setup()
    fired = []
    triggers = EventTriggers(store, lambda tx, d: fired.append(d["id"]), host="h")
    triggers.poll()
    source.publish(envelope(1))
    ingest.ingest()
    result = triggers.poll(timeout=1.0)
    assert result.fired == ("evt_1",)
    assert result.skipped == ()
    assert result.token == store.load_cursor(triggers.consumer, EVENTS_COLLECTION)


def test_a_failing_handler_rolls_back_and_is_retried_next_poll():
    store, source, ingest = _setup()
    attempts = []

    def flaky(tx, doc):
        attempts.append(doc["id"])
        tx.insert("runs", {"id": doc["id"]})
        if len(attempts) == 1:
            raise RuntimeError("boom")

    triggers = EventTriggers(store, flaky, host="h")
    triggers.poll()
    source.publish(envelope(1))
    ingest.ingest()
    with pytest.raises(RuntimeError):
        triggers.poll(timeout=1.0)
    assert store.get("runs", "evt_1") is None  # rolled back
    assert store.get(FIRES_COLLECTION, triggers.fire_id("evt_1")) is None
    assert triggers.poll(timeout=1.0).fired == ("evt_1",)
    assert attempts == ["evt_1", "evt_1"]


def test_a_handler_duplicate_key_is_not_mistaken_for_already_fired():
    from culture_rules.store.port import DuplicateKeyError

    store, source, ingest = _setup()
    store.insert("runs", {"id": "taken"})

    def handler(tx, doc):
        tx.insert("runs", {"id": "taken"})

    triggers = EventTriggers(store, handler, host="h")
    triggers.poll()
    source.publish(envelope(1))
    ingest.ingest()
    with pytest.raises(DuplicateKeyError):
        triggers.poll(timeout=1.0)


def test_non_insert_changes_are_ignored_but_advance_the_cursor():
    store, source, ingest = _setup()
    fired = []
    triggers = EventTriggers(store, lambda tx, d: fired.append(d["id"]), host="h")
    triggers.poll()
    pinned = store.load_cursor(triggers.consumer, EVENTS_COLLECTION)
    store.insert(EVENTS_COLLECTION, {"id": "manual"})
    store.delete(EVENTS_COLLECTION, "manual")  # never done by culture-rules; must not fire
    head = store.head(EVENTS_COLLECTION)
    result = triggers.poll(timeout=1.0)
    assert fired == ["manual"]
    assert result.ignored == 1
    cursor = store.load_cursor(triggers.consumer, EVENTS_COLLECTION)
    assert cursor != pinned
    assert cursor == head  # moved past the ignored delete too
    assert list(store.changes(EVENTS_COLLECTION, cursor)) == []


def test_different_consumers_fire_independently():
    store, source, ingest = _setup()
    seen = {"x": [], "y": []}
    tx_ = EventTriggers(store, lambda tx, d: seen["x"].append(d["id"]), host="h", consumer="x")
    ty_ = EventTriggers(store, lambda tx, d: seen["y"].append(d["id"]), host="h", consumer="y")
    tx_.poll()
    ty_.poll()
    source.publish(envelope(1))
    ingest.ingest()
    tx_.poll(timeout=1.0)
    ty_.poll(timeout=1.0)
    assert seen == {"x": ["evt_1"], "y": ["evt_1"]}


def test_fire_marker_records_host_and_event():
    store, source, ingest = _setup()
    triggers = EventTriggers(store, lambda tx, d: None, host="host-z")
    triggers.poll()
    source.publish(envelope(1))
    ingest.ingest()
    triggers.poll(timeout=1.0)
    marker = store.get(FIRES_COLLECTION, triggers.fire_id("evt_1"))
    assert marker["event_id"] == "evt_1"
    assert marker["host"] == "host-z"
    assert marker["consumer"] == triggers.consumer


@pytest.mark.parametrize("kw", [{"host": ""}, {"host": "h", "consumer": ""}])
def test_constructor_validates(kw):
    store = MemoryStore()
    with pytest.raises(ValueError):
        EventTriggers(store, lambda tx, d: None, **kw)


def test_a_quiet_event_feed_cursor_follows_the_feed_at_most_every_five_minutes():
    """The events cursor moves past other collections' writes too (MongoDB would otherwise
    rescan the oplog from the last event on every poll), but saving it is itself a write,
    so a quiet move is saved at most every QUIET_CURSOR_EVERY."""
    from datetime import UTC, datetime, timedelta

    store, source, ingest = _setup()
    now = [datetime(2026, 10, 23, 12, 0, tzinfo=UTC)]
    fired = []
    triggers = EventTriggers(
        store, lambda tx, d: fired.append(d["id"]), host="h", clock=lambda: now[0]
    )
    triggers.poll()
    pinned = store.load_cursor(triggers.consumer, EVENTS_COLLECTION)
    store.put("rules", {"id": "r1"})
    assert triggers.poll().token == pinned  # within five minutes of start: pinned
    now[0] += timedelta(minutes=5)
    first = triggers.poll()
    assert first.token == store.load_cursor(triggers.consumer, EVENTS_COLLECTION) != pinned

    store.put("rules", {"id": "r2"})
    now[0] += timedelta(minutes=1)
    assert triggers.poll().token == first.token  # within five minutes: not saved again

    now[0] += timedelta(minutes=5)
    later = triggers.poll()
    assert later.token != first.token
    assert later.token == store.load_cursor(triggers.consumer, EVENTS_COLLECTION)

    source.publish(envelope(1))
    ingest.ingest()
    assert triggers.poll(timeout=1.0).fired == ("evt_1",)
    assert fired == ["evt_1"]
