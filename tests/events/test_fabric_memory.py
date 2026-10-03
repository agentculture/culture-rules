"""The event-fabric contract on the in-memory store (two peers = two hosts)."""

from __future__ import annotations

from culture_rules.store.memory import MemoryStore
from tests.events.fabric_contract import EventFabricContract


class TestEventFabricOnMemory(EventFabricContract):
    def make_store(self):
        return MemoryStore()

    def open_peer(self, store):
        return store.peer()
