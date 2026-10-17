"""Workflow revisions on the MongoDB adapter (marker ``mongo``; skips without docker).

The expected-revision check runs inside the write's transaction: of two writers that read
the same revision, exactly one writes and the other is refused with ``revision_conflict``.
"""

from __future__ import annotations

import threading

import pytest

from culture_rules.engine.audit import AUDIT_COLLECTION
from culture_rules.engine.lifecycle import Lifecycle
from culture_rules.server.service import Definitions, RevisionConflict
from tests.server.conftest import workflow_body

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


def _named(name: str, expected: int | None) -> dict:
    body = {**workflow_body(), "name": name}
    if expected is not None:
        body["expected_revision"] = expected
    return body


def test_current_stale_and_missing_on_mongo(mongo_store):
    defs = Definitions(mongo_store)
    assert defs.create("workflows", workflow_body(), "alice")["revision"] == 1
    assert defs.update("workflows", "wf", _named("one", 1), "alice")["revision"] == 2
    before = mongo_store.get("workflows", "wf")
    for expected in (1, None, 7):
        with pytest.raises(RevisionConflict) as exc:
            defs.update("workflows", "wf", _named("stale", expected), "alice")
        assert exc.value.errors[0]["current_revision"] == 2
    assert mongo_store.get("workflows", "wf") == before


def test_lifecycle_and_toggle_bump_on_mongo(mongo_store):
    defs = Definitions(mongo_store)
    defs.create("workflows", workflow_body(), "alice")
    assert defs.set_enabled("workflows", "wf", False, "alice")["revision"] == 2
    life = Lifecycle(mongo_store, defs._audit)
    assert life.soft_delete("workflows", "wf", "alice")["revision"] == 3
    assert life.restore("workflows", "wf", "alice")["revision"] == 4


def test_two_writers_on_one_revision_exactly_one_wins_on_mongo(mongo_store):
    defs = Definitions(mongo_store)
    defs.create("workflows", workflow_body(), "alice")
    barrier = threading.Barrier(2)
    outcomes: dict[str, str] = {}

    def save(name: str) -> None:
        barrier.wait()
        try:
            defs.update("workflows", "wf", _named(name, 1), name)
            outcomes[name] = "won"
        except RevisionConflict as exc:
            outcomes[name] = f"conflict@{exc.errors[0]['current_revision']}"

    threads = [threading.Thread(target=save, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes.values()) == ["conflict@2", "won"]
    winner = next(n for n, o in outcomes.items() if o == "won")
    stored = mongo_store.get("workflows", "wf")
    assert (stored["name"], stored["revision"]) == (winner, 2)
    updates = [
        e for e in mongo_store.find(AUDIT_COLLECTION) if e.get("verb") == "definitions.update"
    ]
    assert len(updates) == 1
