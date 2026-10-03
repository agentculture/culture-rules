"""Event-fabric behaviour every StoragePort adapter must support (memory and mongo).

Bind it like the storage contract: subclass :class:`EventFabricContract` and
implement ``make_store()`` (empty data) and ``open_peer(store)`` (another host's
handle on the same data).
"""

from __future__ import annotations

import pytest

from culture_rules.events.ingest import EVENTS_COLLECTION, EventIngest
from culture_rules.events.triggers import FIRES_COLLECTION, EventTriggers
from culture_rules.store.port import StoragePort
from tests.events.fakes import FakeEventSource, envelope


class Crash(Exception):
    """Simulated host death mid-handler."""


class EventFabricContract:
    def make_store(self) -> StoragePort:  # pragma: no cover
        raise NotImplementedError

    def open_peer(self, store: StoragePort) -> StoragePort:  # pragma: no cover
        raise NotImplementedError

    # ------------------------------------------------------------- ingest

    def test_ingest_inserts_each_envelope_exactly_once_despite_duplicates(self):
        store = self.make_store()
        dup = [envelope(1), envelope(2), envelope(1), envelope(3), envelope(2), envelope(1)]
        ingest = EventIngest(store, FakeEventSource(dup), host="host-a", batch_size=4)
        results = ingest.ingest(max_batches=10)
        assert sum(r.inserted for r in results) == 3
        assert sum(r.duplicates for r in results) == 3
        ids = [d["id"] for d in store.find(EVENTS_COLLECTION)]
        assert ids == ["evt_1", "evt_2", "evt_3"]

    def test_two_hosts_ingesting_the_same_events_store_each_once(self):
        store_a = self.make_store()
        store_b = self.open_peer(store_a)
        events = [envelope(i) for i in range(5)]
        EventIngest(store_a, FakeEventSource(events, name="sub-a"), host="host-a").ingest()
        EventIngest(store_b, FakeEventSource(events, name="sub-b"), host="host-b").ingest()
        docs = store_a.find(EVENTS_COLLECTION)
        assert [d["id"] for d in docs] == [f"evt_{i}" for i in range(5)]
        assert {d["host"] for d in docs} == {"host-a"}  # first writer wins; never rewritten

    def test_stored_event_is_the_verbatim_envelope_plus_received_at_and_host(self):
        store = self.make_store()
        original = envelope(7, correlationId="corr_1", runId="run_9")
        EventIngest(store, FakeEventSource([original]), host="host-a").ingest()
        doc = store.get(EVENTS_COLLECTION, "evt_7")
        assert doc["envelope"] == original
        assert doc["host"] == "host-a"
        assert isinstance(doc["received_at"], str) and doc["received_at"]

    def test_a_redelivered_envelope_never_mutates_the_stored_event(self):
        store = self.make_store()
        first = envelope(1)
        changed = envelope(1, data={"tampered": True})
        source = FakeEventSource([first])
        ingest = EventIngest(store, source, host="host-a")
        ingest.ingest()
        before = store.get(EVENTS_COLLECTION, "evt_1")
        source.publish(changed)
        assert ingest.ingest()[0].duplicates == 1
        assert store.get(EVENTS_COLLECTION, "evt_1") == before

    def test_ingest_resumes_from_its_persisted_source_cursor(self):
        store = self.make_store()
        source = FakeEventSource([envelope(i) for i in range(3)])
        EventIngest(store, source, host="host-a").ingest()
        source.publish(envelope(3))
        again = EventIngest(store, source, host="host-a")  # restarted process, same host
        results = again.ingest()
        assert source.calls[-len(results)]["after"] == "3"
        assert sum(r.inserted for r in results) == 1

    # ----------------------------------------------------------- triggers

    def test_trigger_fires_once_per_inserted_event_from_its_start_position(self):
        store = self.make_store()
        source = FakeEventSource([envelope("old")])
        ingest = EventIngest(store, source, host="host-a")
        ingest.ingest()
        fired: list[str] = []
        triggers = EventTriggers(store, lambda tx, doc: fired.append(doc["id"]), host="host-a")
        triggers.poll()  # pins the start position at the current head
        source.publish(envelope(1), envelope(2), envelope(1))
        ingest.ingest()
        triggers.poll(timeout=1.0)
        triggers.poll()
        assert fired == ["evt_1", "evt_2"]  # "evt_old" predates the trigger: no backfill

    def test_trigger_handler_writes_commit_atomically_with_the_fire(self):
        store = self.make_store()
        source = FakeEventSource()
        ingest = EventIngest(store, source, host="host-a")

        def handler(tx, doc):
            tx.insert("runs", {"id": f"run-for-{doc['id']}", "event": doc["id"]})

        triggers = EventTriggers(store, handler, host="host-a", handler_collections=("runs",))
        triggers.poll()
        source.publish(envelope(1))
        ingest.ingest()
        triggers.poll(timeout=1.0)
        assert store.get("runs", "run-for-evt_1")["event"] == "evt_1"
        assert store.get(FIRES_COLLECTION, triggers.fire_id("evt_1")) is not None

    def test_failover_to_another_host_fires_every_later_event_exactly_once(self):
        store_a = self.make_store()
        store_b = self.open_peer(store_a)
        source = FakeEventSource()
        ingest = EventIngest(store_a, source, host="host-a")
        fired: list[tuple[str, str]] = []

        def handler_for(host, crash_on=None):
            def handler(tx, doc):
                if doc["id"] == crash_on:
                    raise Crash(doc["id"])
                tx.insert("runs", {"id": f"run-{doc['id']}"})
                fired.append((host, doc["id"]))

            return handler

        host_a = EventTriggers(
            store_a,
            handler_for("a", crash_on="evt_3"),
            host="host-a",
            handler_collections=("runs",),
        )
        host_a.poll()
        source.publish(*(envelope(i) for i in range(1, 6)))
        ingest.ingest()
        with pytest.raises(Crash):  # host A dies while handling evt_3
            for _ in range(5):
                host_a.poll(timeout=1.0)
        # host B takes over the same consumer (failover), resuming from the persisted token
        host_b = EventTriggers(
            store_b, handler_for("b"), host="host-b", handler_collections=("runs",)
        )
        source.publish(envelope(6))
        ingest.ingest()
        for _ in range(4):
            host_b.poll(timeout=1.0)
        ids = [e for _, e in fired]
        assert sorted(ids) == [f"evt_{i}" for i in range(1, 7)]
        assert len(ids) == len(set(ids))  # every event fired exactly once
        assert ("a", "evt_1") in fired and ("b", "evt_3") in fired and ("b", "evt_6") in fired
        assert len(store_a.find("runs")) == 6

    def test_a_stale_host_replaying_from_an_old_token_does_not_refire(self):
        store_a = self.make_store()
        store_b = self.open_peer(store_a)
        source = FakeEventSource()
        ingest = EventIngest(store_a, source, host="host-a")
        fired: list[str] = []
        host_a = EventTriggers(store_a, lambda tx, d: fired.append(d["id"]), host="host-a")
        host_a.poll()
        stale_token = store_a.load_cursor(host_a.consumer, EVENTS_COLLECTION)
        source.publish(envelope(1), envelope(2))
        ingest.ingest()
        host_a.poll(timeout=1.0)
        host_a.poll()
        # host B wrongly starts from the old token (e.g. a lagging cursor read)
        store_b.save_cursor(host_a.consumer, EVENTS_COLLECTION, stale_token)
        host_b = EventTriggers(store_b, lambda tx, d: fired.append(d["id"]), host="host-b")
        host_b.poll(timeout=1.0)
        host_b.poll()
        assert fired == ["evt_1", "evt_2"]
