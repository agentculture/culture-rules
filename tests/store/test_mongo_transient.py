"""Criterion 4 of t41: MongoStore never leaks raw pymongo transient-transaction errors.

* a write conflict inside a transaction surfaces as :class:`TransientStoreError` (a
  :class:`StoreError`), never as ``pymongo.errors.OperationFailure``;
* :func:`culture_rules.store.retry.run_transaction` re-runs the body (bounded);
* two concurrent transactions inserting the same id: the loser gets
  :class:`DuplicateKeyError`, exactly as outside a transaction.

The store-independent parts run without docker; the rest is marked ``mongo``.
"""

from __future__ import annotations

import threading
import time

import pytest

from culture_rules.store.memory import MemoryStore
from culture_rules.store.port import DuplicateKeyError, StoreError, TransientStoreError
from culture_rules.store.retry import run_transaction

# --------------------------------------------------------------------------- no docker


def test_transient_store_error_is_a_store_error():
    assert issubclass(TransientStoreError, StoreError)


def test_run_transaction_re_runs_the_body_on_a_transient_error():
    store = MemoryStore()
    attempts: list[int] = []

    def body(tx):
        attempts.append(1)
        tx.insert("c", {"id": f"doc-{len(attempts)}"})
        if len(attempts) < 3:
            raise TransientStoreError("write conflict")
        return "done"

    assert run_transaction(store, body, attempts=5, backoff=0.0) == "done"
    assert len(attempts) == 3
    assert [d["id"] for d in store.find("c")] == ["doc-3"]  # earlier attempts rolled back


def test_run_transaction_is_bounded():
    def body(tx):
        raise TransientStoreError("always")

    store = MemoryStore()
    with pytest.raises(TransientStoreError):
        run_transaction(store, body, attempts=3, backoff=0.0)


def test_run_transaction_does_not_retry_other_errors():
    calls: list[int] = []

    def body(tx):
        calls.append(1)
        raise DuplicateKeyError("dup")

    store = MemoryStore()
    with pytest.raises(DuplicateKeyError):
        run_transaction(store, body, attempts=3, backoff=0.0)
    assert calls == [1]


def test_mongo_translation_maps_transient_labels_without_a_server():
    pytest.importorskip("pymongo")
    from pymongo.errors import OperationFailure

    from culture_rules.store.mongo import _translate_transient

    conflict = OperationFailure(
        "WriteConflict", code=112, details={"errorLabels": ["TransientTransactionError"]}
    )
    translated = _translate_transient(conflict)
    assert isinstance(translated, TransientStoreError)
    other = OperationFailure("boom", code=2)
    assert _translate_transient(other) is None


# --------------------------------------------------------------------------- real MongoDB


@pytest.fixture
def mongo_store():
    pytest.importorskip("pymongo")
    mongo_tests = pytest.importorskip("tests.store.test_mongo")
    mongo_tests.get_rig()
    binding = mongo_tests.TestMongoStore()
    store = binding.make_store()
    store.ensure_collections("c")
    try:
        yield store, (lambda: binding.open_peer(store, "1.0"))
    finally:
        mongo_tests._release_stores()


@pytest.mark.mongo
def test_concurrent_same_id_inserts_in_transactions_raise_duplicate_key(mongo_store):
    store, peer = mongo_store
    other = peer()
    inserted = threading.Event()
    release = threading.Event()
    outcome: dict[str, object] = {}

    def first():
        with store.transaction() as tx:
            tx.insert("c", {"id": "same", "by": "first"})
            inserted.set()
            release.wait(10)

    def second():
        try:
            with other.transaction() as tx:
                tx.insert("c", {"id": "same", "by": "second"})
        except BaseException as exc:  # noqa: BLE001 - inspected below
            outcome["error"] = exc
        else:
            outcome["error"] = None

    t1 = threading.Thread(target=first)
    t1.start()
    assert inserted.wait(10)
    t2 = threading.Thread(target=second)
    t2.start()
    time.sleep(0.3)  # the second insert conflicts with the uncommitted first
    release.set()
    t1.join(10)
    t2.join(20)
    assert isinstance(outcome["error"], DuplicateKeyError), repr(outcome["error"])
    assert store.get("c", "same")["by"] == "first"


@pytest.mark.mongo
def test_a_write_conflict_is_typed_and_run_transaction_retries_it(mongo_store):
    store, peer = mongo_store
    other = peer()
    store.put("c", {"id": "x", "n": 0})
    inside = threading.Event()
    release = threading.Event()

    def holder():
        with store.transaction() as tx:
            tx.update_if("c", "x", {"n": 0}, {"n": 1})
            inside.set()
            release.wait(10)

    t = threading.Thread(target=holder)
    t.start()
    assert inside.wait(10)
    try:

        def conflicting_write():
            with other.transaction() as tx:
                tx.update_if("c", "x", {}, {"n": 99})

        with pytest.raises(TransientStoreError):
            conflicting_write()
    finally:
        release.set()
        t.join(10)

    attempts: list[int] = []
    inside.clear()
    release.clear()
    t = threading.Thread(target=holder_two, args=(store, inside, release))
    t.start()
    assert inside.wait(10)

    def body(tx):
        attempts.append(1)
        if len(attempts) == 2:
            release.set()  # the conflicting transaction commits before the retry
            t.join(10)
        doc = tx.get("c", "x")
        return tx.update_if("c", "x", {"n": doc["n"]}, {"n": doc["n"] + 10}).won

    assert run_transaction(other, body, attempts=5, backoff=0.05) is True
    assert len(attempts) >= 2
    assert store.get("c", "x")["n"] == 12  # 1 (holder) + 1 (holder_two) + 10


def holder_two(store, inside, release):
    with store.transaction() as tx:
        doc = tx.get("c", "x")
        tx.update_if("c", "x", {"n": doc["n"]}, {"n": doc["n"] + 1})
        inside.set()
        release.wait(10)


@pytest.mark.mongo
def test_mongo_store_offers_run_transaction(mongo_store):
    store, _ = mongo_store
    assert store.run_transaction(lambda tx: tx.insert("c", {"id": "y"})["id"]) == "y"
