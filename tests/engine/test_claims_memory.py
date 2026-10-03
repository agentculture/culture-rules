"""Run the claims contract against the in-memory StoragePort adapter."""

from __future__ import annotations

from culture_rules.store.memory import MemoryStore
from tests.engine.claims_contract import ClaimsContract


class TestClaimsOnMemoryStore(ClaimsContract):
    def make_store(self, node_schema_version: str = "1.0") -> MemoryStore:
        return MemoryStore(node_schema_version=node_schema_version)

    def open_peer(self, store: MemoryStore, node_schema_version: str) -> MemoryStore:
        return store.peer(node_schema_version=node_schema_version)
