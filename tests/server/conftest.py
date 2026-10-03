"""Fixtures for the HTTP API tests (need the optional ``server`` extra)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.resolve import AuthSettings  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.engine.run_helpers import rule, step, workflow  # noqa: E402

ALICE = {"X-Culture-Identity": "alice"}
# The route/lifecycle tests predate auth (t24): they run on the explicit, off-by-default
# insecure dev identity (X-Culture-Identity, everyone admin). Auth itself is covered by
# tests/server/test_authz_matrix.py and tests/server/test_listeners.py.
DEV = AuthSettings(insecure_dev_identity=True)


def dev_app(store, **kw):
    return create_app(store, auth=DEV, **kw)


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def client(store) -> TestClient:
    return TestClient(dev_app(store))


def rule_body(id: str = "r1", **changes) -> dict:
    body = rule(id=id, workflow_id=None).to_dict()
    body.update(changes)
    return body


def workflow_body(id: str = "wf") -> dict:
    return workflow((step("a"),), id=id).to_dict()
