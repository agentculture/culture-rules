"""Run the claims contract against the MongoDB StoragePort adapter.

The Mongo adapter and its disposable replica-set binding are owned by the
storage-adapter task (``culture_rules/store/mongo.py`` +
``tests/store/test_mongo.py``). This module reuses that binding's
``make_store`` / ``open_peer`` hooks so the claims contract runs against the
very same store setup as the StoragePort contract. Until that binding exists
(or when pymongo / the replica set is unavailable) the whole module skips.
"""

from __future__ import annotations

import pytest

from tests.engine.claims_contract import ClaimsContract
from tests.store.contract import StoragePortContract

pytest.importorskip("pymongo", reason="pymongo (culture-rules[store]) is not installed")
pytest.importorskip("culture_rules.store.mongo", reason="Mongo adapter is not present yet")
_mongo_tests = pytest.importorskip(
    "tests.store.test_mongo", reason="Mongo StoragePort contract binding is not present yet"
)

_bindings = [
    obj
    for obj in vars(_mongo_tests).values()
    if isinstance(obj, type)
    and issubclass(obj, StoragePortContract)
    and obj is not StoragePortContract
]
if not _bindings:  # pragma: no cover - depends on the adapter task's layout
    pytest.skip("no StoragePortContract binding in tests.store.test_mongo", allow_module_level=True)
_Binding = _bindings[0]


class TestClaimsOnMongoStore(ClaimsContract):
    make_store = _Binding.make_store
    open_peer = _Binding.open_peer

    def test_concurrency_claim_write_conflict_rereads_to_deduplicated(self):
        from culture_rules.engine.claims import (
            RULE_ATTEMPT_BUDGETS,
            budget_id,
            reserve_concurrency,
        )
        from culture_rules.engine.runs import RUNS_COLLECTION
        from culture_rules.store.port import TransientStoreError

        store = self.make_store()
        peer = self.open_peer(store, "1.0")
        store.ensure_collections(RULE_ATTEMPT_BUDGETS, RUNS_COLLECTION, "rule_fires")
        assert reserve_concurrency(store, "a", "pr", "old", "old-intent", 3) is None
        store.put(RUNS_COLLECTION, {"id": "old", "status": "failed"})
        budget = store.find(RULE_ATTEMPT_BUDGETS)[0]
        assert budget["id"] == budget_id("pr")  # global: the key alone, no rule id

        def lose_the_race():
            with peer.transaction() as loser:
                assert loser.get(RULE_ATTEMPT_BUDGETS, budget["id"])["count"] == 1
                with store.transaction() as winner:
                    assert reserve_concurrency(winner, "a", "pr", "winner", "win-intent", 3) is None
                reserve_concurrency(loser, "a", "pr", "loser", "lose-intent", 3)

        with pytest.raises(TransientStoreError):
            lose_the_race()
        with peer.transaction() as retry:
            assert (
                reserve_concurrency(retry, "a", "pr", "loser", "lose-intent", 3) == "deduplicated"
            )
        assert store.find(RULE_ATTEMPT_BUDGETS)[0]["count"] == 2

    def test_release_write_conflicts_with_a_concurrent_dedup_note(self):
        """Write-skew guard: a trigger transaction that read the holding run as active
        conflicts with the chain transaction releasing the key, and its retry is admitted
        instead of leaving a pending event nobody would fire."""
        from culture_rules.engine.claims import (
            RULE_ATTEMPT_BUDGETS,
            note_deduplicated,
            release_concurrency,
            reserve_concurrency,
        )
        from culture_rules.engine.runs import RUNS_COLLECTION
        from culture_rules.store.port import TransientStoreError

        store = self.make_store()
        peer = self.open_peer(store, "1.0")
        store.ensure_collections(RULE_ATTEMPT_BUDGETS, RUNS_COLLECTION, "rule_fires")
        assert reserve_concurrency(store, "a", "pr", "run1", "intent1", None) is None
        store.put(RUNS_COLLECTION, {"id": "run1", "status": "running"})
        budget = store.find(RULE_ATTEMPT_BUDGETS)[0]

        def note_against_a_concurrent_release():
            with peer.transaction() as trigger:
                assert reserve_concurrency(trigger, "a", "pr", "run2", "i2", None) == (
                    "deduplicated"
                )
                store.put(RUNS_COLLECTION, {"id": "run1", "status": "failed"})
                with store.transaction() as chain:
                    assert release_concurrency(chain, budget["id"], "run1") is None
                note_deduplicated(trigger, "a", "pr", "evt_2")

        with pytest.raises(TransientStoreError):
            note_against_a_concurrent_release()
        with peer.transaction() as retry:
            assert reserve_concurrency(retry, "a", "pr", "run2", "i2", None) is None
        assert store.get(RULE_ATTEMPT_BUDGETS, budget["id"])["pending_event_id"] is None
