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
