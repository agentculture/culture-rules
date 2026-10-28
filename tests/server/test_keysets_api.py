"""Keysets over the API: admin-only reads and clear, the clear's dry run (c18, h17)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules import keysets  # noqa: E402
from culture_rules.auth.policy import required_role  # noqa: E402
from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

NOW = datetime.now(UTC)
ROUTES = [
    ("GET", "/keysets", None),
    ("GET", "/keysets/seen", None),
    ("POST", "/keysets/seen/clear", {"dry_run": False}),
]


@pytest.fixture
def world():
    store = MemoryStore()
    keysets.add_keys(store, "seen", ["https://a/1", "https://a/2"], NOW, 3600)
    keysets.add_keys(store, "other", ["k"], NOW, 3600)
    client = TestClient(create_app(store, auth=AuthSettings(listener=LAN)))
    tokens = ServiceTokens(store)
    hdr = {
        role: {"Authorization": f"Bearer {tokens.issue(role, name=role, roles=[role]).token}"}
        for role in ("viewer", "editor", "admin")
    }
    return store, client, hdr


def _count(store, name):
    return len(store.find(keysets.KEYSETS_COLLECTION, {"set": name}))


@pytest.mark.parametrize("role", ["viewer", "editor"])
@pytest.mark.parametrize("method, path, body", ROUTES)
def test_non_admin_gets_403(world, role, method, path, body):
    _, client, hdr = world
    r = client.request(method, path, json=body, headers=hdr[role])
    assert r.status_code == 403


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_non_admin_clear_deletes_nothing(world, role):
    store, client, hdr = world
    client.post("/keysets/seen/clear", json={"dry_run": False}, headers=hdr[role])
    assert _count(store, "seen") == 2


@pytest.mark.parametrize(
    "method, path",
    [("GET", "/keysets"), ("GET", "/keysets/x"), ("POST", "/keysets/x/clear")],
)
def test_policy_says_admin(method, path):
    assert required_role(method, path) == "admin"


def test_list_names_sets_with_key_counts(world):
    _, client, hdr = world
    r = client.get("/keysets", headers=hdr["admin"])
    assert r.json()["items"] == [{"set": "other", "keys": 1}, {"set": "seen", "keys": 2}]


def test_show_lists_keys_with_iso_datetimes(world):
    _, client, hdr = world
    items = client.get("/keysets/seen", headers=hdr["admin"]).json()["items"]
    assert items[0] == {
        "key": "https://a/1",
        "first_seen": NOW.astimezone(UTC).isoformat(),
        "expires_at": (NOW + timedelta(seconds=3600)).astimezone(UTC).isoformat(),
    }


def test_show_names_the_set(world):
    _, client, hdr = world
    assert client.get("/keysets/seen", headers=hdr["admin"]).json()["set"] == "seen"


def test_show_of_an_unknown_set_is_empty(world):
    _, client, hdr = world
    assert client.get("/keysets/nothing", headers=hdr["admin"]).json()["items"] == []


def test_show_of_a_bad_set_name_is_422(world):
    _, client, hdr = world
    r = client.get("/keysets/bad%20name", headers=hdr["admin"])
    assert r.status_code == 422


def test_clear_is_a_dry_run_by_default_and_reports_the_count(world):
    _, client, hdr = world
    r = client.post("/keysets/seen/clear", json={}, headers=hdr["admin"])
    assert r.json() == {"set": "seen", "key": None, "dry_run": True, "count": 2}


def test_clear_dry_run_leaves_the_documents(world):
    store, client, hdr = world
    client.post("/keysets/seen/clear", json={}, headers=hdr["admin"])
    assert _count(store, "seen") == 2


def test_clear_without_a_body_is_a_dry_run(world):
    store, client, hdr = world
    client.post("/keysets/seen/clear", headers=hdr["admin"])
    assert _count(store, "seen") == 2


def test_clear_dry_run_of_a_key_reports_whether_it_exists(world):
    _, client, hdr = world
    body = {"key": "https://a/1"}
    r = client.post("/keysets/seen/clear", json=body, headers=hdr["admin"])
    assert r.json()["count"] == 1


def test_clear_dry_run_of_an_absent_key_counts_zero(world):
    _, client, hdr = world
    body = {"key": "https://a/9"}
    r = client.post("/keysets/seen/clear", json=body, headers=hdr["admin"])
    assert r.json()["count"] == 0


def test_clear_applied_with_a_key_removes_only_that_key(world):
    store, client, hdr = world
    body = {"key": "https://a/1", "dry_run": False}
    client.post("/keysets/seen/clear", json=body, headers=hdr["admin"])
    assert [d["key"] for d in store.find(keysets.KEYSETS_COLLECTION, {"set": "seen"})] == [
        "https://a/2"
    ]


def test_clear_applied_with_a_key_reports_one(world):
    _, client, hdr = world
    body = {"key": "https://a/1", "dry_run": False}
    r = client.post("/keysets/seen/clear", json=body, headers=hdr["admin"])
    assert r.json() == {"set": "seen", "key": "https://a/1", "dry_run": False, "count": 1}


def test_clear_applied_without_a_key_removes_the_set(world):
    store, client, hdr = world
    client.post("/keysets/seen/clear", json={"dry_run": False}, headers=hdr["admin"])
    assert _count(store, "seen") == 0


def test_clear_applied_without_a_key_leaves_other_sets(world):
    store, client, hdr = world
    client.post("/keysets/seen/clear", json={"dry_run": False}, headers=hdr["admin"])
    assert _count(store, "other") == 1


def test_clear_applied_without_a_key_reports_the_count(world):
    _, client, hdr = world
    r = client.post("/keysets/seen/clear", json={"dry_run": False}, headers=hdr["admin"])
    assert r.json()["count"] == 2


def test_clear_with_a_bad_set_name_is_422(world):
    _, client, hdr = world
    r = client.post("/keysets/bad%20name/clear", json={}, headers=hdr["admin"])
    assert r.status_code == 422


def test_clear_with_an_empty_key_is_422(world):
    _, client, hdr = world
    r = client.post("/keysets/seen/clear", json={"key": ""}, headers=hdr["admin"])
    assert r.status_code == 422
