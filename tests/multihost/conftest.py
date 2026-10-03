"""Fixtures for the multi-host tests: a shared store per backend and a simulated cluster.

``cluster`` runs once on a MemoryStore (every host gets its own ``peer`` handle on the same
data) and once on the disposable MongoDB replica-set rig (marker ``mongo``; every host gets
its own client), so the realistic run goes through real change streams and transactions.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from culture_rules.store.memory import MemoryStore
from tests.multihost.harness import Cluster

pytestmark = pytest.mark.multihost


def _memory() -> tuple[Callable[[], Any], Callable[[], None]]:
    base = MemoryStore()
    return base.peer, lambda: None


def _mongo() -> tuple[Callable[[], Any], Callable[[], None]]:
    pytest.importorskip("pymongo", reason="pymongo (culture-rules[store]) is not installed")
    mongo_tests = pytest.importorskip("tests.store.test_mongo", reason="Mongo binding missing")
    mongo_tests.get_rig()  # skips when docker / the image is unavailable
    binding = mongo_tests.TestMongoStore()
    base = binding.make_store()
    return (lambda: binding.open_peer(base, "1.0")), mongo_tests._release_stores


@pytest.fixture(params=["memory", pytest.param("mongo", marks=pytest.mark.mongo)])
def cluster(request) -> Iterator[Cluster]:
    peer, release = _memory() if request.param == "memory" else _mongo()
    c = Cluster(peer, backend=request.param)
    try:
        yield c
    finally:
        try:
            c.close()
        finally:
            release()
