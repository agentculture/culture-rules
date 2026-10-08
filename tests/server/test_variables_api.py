"""Variables over the API: admin-only writes, history, and the rules that reference one."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.server.conftest import rule_body  # noqa: E402


@pytest.fixture
def world():
    store = MemoryStore()
    client = TestClient(create_app(store, auth=AuthSettings(listener=LAN)))
    tokens = ServiceTokens(store)
    hdr = {
        role: {"Authorization": f"Bearer {tokens.issue(role, name=role, roles=[role]).token}"}
        for role in ("viewer", "editor", "admin")
    }
    return store, client, hdr


def test_editor_gets_403_on_put_and_nothing_is_written(world):
    store, client, hdr = world
    r = client.put("/variables/trusted_authors", json={"value": ["a"]}, headers=hdr["editor"])
    assert r.status_code == 403
    assert store.get_variable("trusted_authors") is None
    r = client.put("/variables/trusted_authors", json={"value": ["a"]}, headers=hdr["viewer"])
    assert r.status_code == 403


def test_admin_write_creates_a_version_naming_the_principal(world):
    _, client, hdr = world
    r = client.put(
        "/variables/trusted_authors",
        json={"value": ["a"], "description": "who may"},
        headers=hdr["admin"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1
    assert r.json()["updated_by"] == "admin"
    r = client.put("/variables/trusted_authors", json={"value": ["a", "b"]}, headers=hdr["admin"])
    assert r.json()["version"] == 2
    assert r.json()["value"] == ["a", "b"]


def test_reads_are_viewer_and_list_get_history(world):
    _, client, hdr = world
    for v in (["a"], ["a", "b"]):
        client.put("/variables/trusted_authors", json={"value": v}, headers=hdr["admin"])
    client.put("/variables/limit", json={"value": 3}, headers=hdr["admin"])
    v = hdr["viewer"]
    names = [i["name"] for i in client.get("/variables", headers=v).json()["items"]]
    assert names == ["limit", "trusted_authors"]
    got = client.get("/variables/trusted_authors", headers=v).json()
    assert got["version"] == 2
    assert got["value"] == ["a", "b"]
    hist = client.get("/variables/trusted_authors/history", headers=v).json()["items"]
    assert [h["version"] for h in hist] == [1, 2]
    assert [h["value"] for h in hist] == [["a"], ["a", "b"]]
    assert all(h["updated_by"] == "admin" for h in hist)
    assert client.get("/variables/nope", headers=v).status_code == 404
    assert client.get("/variables/nope/history", headers=v).status_code == 404


def test_invalid_name_or_value_is_422(world):
    _, client, hdr = world
    assert (
        client.put("/variables/Bad-Name", json={"value": 1}, headers=hdr["admin"]).status_code
        == 422
    )
    r = client.put("/variables/ok", json={"value": {"a": 1}}, headers=hdr["admin"])
    assert r.status_code == 422
    assert client.put("/variables/ok", json={}, headers=hdr["admin"]).status_code == 422


def test_refs_lists_every_rule_referencing_the_variable(world):
    store, client, hdr = world
    cond = rule_body(
        "by-cond",
        condition={"op": "in", "value": {"field": "a"}, "items": {"var": "trusted_authors"}},
    )
    inp = rule_body(
        "by-input", workflow={"id": "wf", "inputs": {"allow": {"$var": "trusted_authors"}}}
    )
    other = rule_body(
        "other",
        condition={"op": "compare", "cmp": "<", "left": {"field": "n"}, "right": {"var": "limit"}},
    )
    gone = rule_body("deleted", condition={"var": "trusted_authors"})
    gone["deleted_at"] = "2026-01-01T00:00:00Z"
    for doc in (cond, inp, other, gone):
        store.put("rules", doc)
    r = client.get("/variables/trusted_authors/refs", headers=hdr["viewer"])
    assert r.status_code == 200, r.text
    assert [i["id"] for i in r.json()["items"]] == ["by-cond", "by-input"]
    assert client.get("/variables/unused/refs", headers=hdr["viewer"]).json()["items"] == []
    assert client.get("/variables/Bad-Name/refs", headers=hdr["viewer"]).status_code == 422


def test_a_missing_identity_is_not_reported_as_a_bad_value():
    """require_identity raises AuditError (a ValueError); it must not become invalid_value."""
    import pytest

    from culture_rules.engine.audit import AuditError
    from culture_rules.server.service import Variables
    from culture_rules.store.memory import MemoryStore

    variables = Variables(MemoryStore())
    with pytest.raises(AuditError):
        variables.set("limit", 3, "")
