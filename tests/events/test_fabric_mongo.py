"""The event-fabric contract on a real MongoDB replica set (marker ``mongo``).

Reuses the storage binding in ``tests/store/test_mongo.py`` (its lazily-started
docker rig, ``make_store`` and ``open_peer``), so two ``MongoStore`` clients on
one database act as two hosts.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pymongo", reason="pymongo (culture-rules[store]) is not installed")

from tests.events.fabric_contract import EventFabricContract  # noqa: E402
from tests.store import test_mongo as _mongo  # noqa: E402

pytestmark = pytest.mark.mongo


class TestEventFabricOnMongo(EventFabricContract):
    @pytest.fixture(autouse=True)
    def _cleanup(self):
        yield
        _mongo._release_stores()

    def make_store(self):
        return _mongo.TestMongoStore.make_store(self)

    def open_peer(self, store):
        return _mongo.TestMongoStore.open_peer(self, store, "1.0")

    def test_events_collection_has_a_unique_index_on_the_envelope_id(self):
        from culture_rules.events.ingest import EVENTS_COLLECTION, EventIngest
        from tests.events.fakes import FakeEventSource, envelope

        store = self.make_store()
        EventIngest(store, FakeEventSource([envelope(1)]), host="h").ingest()
        indexes = store._collection(EVENTS_COLLECTION).index_information()
        # the store keys documents by id (_id); the envelope id IS the document id
        assert indexes["_id_"]["key"] == [("_id", 1)]
        raw = store._collection(EVENTS_COLLECTION).find_one({"_id": "evt_1"})
        assert raw["envelope"]["id"] == "evt_1"
