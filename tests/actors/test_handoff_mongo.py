"""#39, Codex round 2 #4 on the MongoDB adapter (marker ``mongo``; skips without docker): the
hand-off prune selects expired ids through a projection, never pack bodies, and the hand-offs
carry a TTL index on their ``expires_at`` date."""

from __future__ import annotations

from contextlib import contextmanager
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


class WriteAfterRead:
    """A store whose first transaction, right after it reads a hand-off, lets a write from
    outside the transaction renew that hand-off (Codex round 3 #4: an export lands between
    the prune's read and its delete)."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.renew = None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @contextmanager
    def transaction(self):
        with self._inner.transaction() as tx:
            yield _RenewAfterGet(tx, self)


class _RenewAfterGet:
    def __init__(self, tx, outer: WriteAfterRead) -> None:
        self._tx, self._outer = tx, outer

    def __getattr__(self, name):
        return getattr(self._tx, name)

    def get(self, collection, id):
        doc = self._tx.get(collection, id)
        if self._outer.renew is not None:
            renew, self._outer.renew = self._outer.renew, None
            renew()
        return doc


def test_the_prune_keeps_a_hand_off_renewed_inside_its_transaction_on_mongo(mongo_store):
    mongo_store.put(HANDOFFS_COLLECTION, handoff("hof_old", T0 - timedelta(seconds=1)))
    racing = WriteAfterRead(mongo_store)
    renewed = {"rev": "renewed", "expires_at": T0 + timedelta(days=1)}
    racing.renew = lambda: mongo_store.update_if(
        HANDOFFS_COLLECTION, "hof_old", {"rev": "hof_old"}, renewed
    )
    assert prune_handoffs(racing, T0) == 0
    assert mongo_store.get(HANDOFFS_COLLECTION, "hof_old")["rev"] == "renewed"
