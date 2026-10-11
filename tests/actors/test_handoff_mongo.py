"""#39, Codex round 2 #4 on the MongoDB adapter (marker ``mongo``; skips without docker): the
hand-off prune selects expired ids through a projection, never pack bodies, and the hand-offs
carry a TTL index on their ``expires_at`` date."""

from __future__ import annotations

from datetime import timedelta

import pytest

from culture_rules.actors.handoff import (
    EXPIRY_INDEX,
    HANDOFFS_COLLECTION,
    ensure_handoff_expiry,
    prune_handoffs,
)
from tests.engine.run_helpers import T0

pytest.importorskip("pymongo", reason="pymongo (culture-rules[store]) is not installed")
_mongo_tests = pytest.importorskip("tests.store.test_mongo", reason="Mongo binding missing")

pytestmark = pytest.mark.mongo


@pytest.fixture
def mongo_store():
    _mongo_tests.get_rig()  # skips when docker / the image is unavailable
    binding = _mongo_tests.TestMongoStore()
    store = binding.make_store()
    yield store
    _mongo_tests._release_stores()


def handoff(doc_id, expires_at):
    return {"id": doc_id, "pack": "QUJD" * 1000, "rev": doc_id, "expires_at": expires_at}


def test_find_range_projects_only_the_fields_asked_for_on_mongo(mongo_store):
    mongo_store.put(HANDOFFS_COLLECTION, handoff("hof_old", T0))
    (doc,) = mongo_store.find_range(
        HANDOFFS_COLLECTION, None, field="expires_at", upto=T0, limit=10, fields=("id", "rev")
    )
    assert doc == {"id": "hof_old", "rev": "hof_old"}


def test_the_prune_deletes_only_expired_handoffs_on_mongo(mongo_store):
    mongo_store.put(HANDOFFS_COLLECTION, handoff("hof_old", T0 - timedelta(seconds=1)))
    mongo_store.put(HANDOFFS_COLLECTION, handoff("hof_new", T0 + timedelta(hours=1)))
    assert prune_handoffs(mongo_store, T0) == 1
    assert [d["id"] for d in mongo_store.find(HANDOFFS_COLLECTION)] == ["hof_new"]


def test_the_handoffs_have_a_ttl_index_on_their_expiry_on_mongo(mongo_store):
    ensure_handoff_expiry(mongo_store)
    info = mongo_store._db[HANDOFFS_COLLECTION].index_information()
    assert info[EXPIRY_INDEX]["expireAfterSeconds"] == 0
