"""Run the shared StoragePort contract against the in-memory adapter."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import culture_rules.store.memory as memory_module
import culture_rules.store.port as port_module
from culture_rules.store.memory import MemoryStore
from culture_rules.store.port import StoreError
from tests.store.contract import StoragePortContract


class TestMemoryStore(StoragePortContract):
    def make_store(self, node_schema_version: str = "1.0") -> MemoryStore:
        return MemoryStore(node_schema_version=node_schema_version)

    def open_peer(self, store: MemoryStore, node_schema_version: str) -> MemoryStore:
        return store.peer(node_schema_version=node_schema_version)


def test_clock_is_injectable():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    ticks = iter([t0, t0 + timedelta(seconds=1)])
    store = MemoryStore(clock=lambda: next(ticks))
    store.put("rules", {"id": "a"})
    assert store.get("rules", "a")["updated_at"] == t0.isoformat(timespec="microseconds")
    store.put("rules", {"id": "a"})
    assert datetime.fromisoformat(store.get("rules", "a")["updated_at"]) == t0 + timedelta(
        seconds=1
    )


def test_peer_shares_data():
    a = MemoryStore()
    b = a.peer(node_schema_version="2.0")
    a.put("rules", {"id": "x"})
    assert b.get("rules", "x")["id"] == "x"
    assert str(b.node_schema_version) == "2.0"
    assert MemoryStore().get("rules", "x") is None  # a fresh store is empty


def test_nested_transaction_rejected():
    store = MemoryStore()
    with store.transaction():
        with pytest.raises(StoreError):
            with store.transaction():
                pass  # pragma: no cover


def test_direct_write_inside_own_transaction_rejected():
    store = MemoryStore()
    with store.transaction() as tx:
        tx.put("rules", {"id": "a"})
        with pytest.raises(StoreError):
            store.put("rules", {"id": "b"})
    assert store.get("rules", "b") is None


def test_transaction_handle_unusable_after_exit():
    store = MemoryStore()
    with store.transaction() as tx:
        tx.put("rules", {"id": "a"})
    with pytest.raises(StoreError):
        tx.put("rules", {"id": "b"})


def test_foreign_or_bad_token_rejected():
    store = MemoryStore()
    with pytest.raises(ValueError):
        list(store.changes("rules", "not-a-token"))
    with pytest.raises(ValueError):
        list(store.changes("rules", "999999"))  # beyond the log head


@pytest.mark.parametrize("module", [port_module, memory_module])
def test_no_third_party_imports(module):
    tree = ast.parse(Path(module.__file__).read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add(node.module.split(".")[0])
    import sys

    allowed = set(sys.stdlib_module_names) | {"culture_rules"}
    assert roots <= allowed, roots - allowed
