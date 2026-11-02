"""An actor's ``params.briefs`` is prompt text: only an admin may add, change or remove it.

Mirrors the runner-commands guard (``tests/server/test_authz_matrix.py``): the check runs
inside the save transaction on API create, update and import, so a refused save leaves the
stored actor as it was.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.guards import BriefsDenied, check_actor_briefs  # noqa: E402
from culture_rules.auth.principal import Principal  # noqa: E402
from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

DIGEST = {"digest": "Summarise the day's links in five bullets."}
SECTIONS = {"sections": "Write one section per source."}


@pytest.fixture
def world():
    store = MemoryStore()
    client = TestClient(create_app(store, auth=AuthSettings(listener=LAN)))
    tokens = ServiceTokens(store)
    hdr = {
        role: {"Authorization": f"Bearer {tokens.issue('root', name=role, roles=[role]).token}"}
        for role in ("editor", "admin")
    }
    return store, client, hdr


def agent(briefs=None, **changes) -> dict:
    body = {
        "id": "writer",
        "name": "writer",
        "kind": "agent",
        "params": {"base_url": "http://localhost:8000"},
    }
    if briefs is not None:
        body["params"]["briefs"] = briefs
    body.update(changes)
    return body


def _code(response) -> str:
    return response.json()["error"]["code"]


def _stored_briefs(store):
    return store.get("actors", "writer")["params"].get("briefs")


# ------------------------------------------------------------------------------ create


def test_editor_creating_an_actor_with_briefs_is_403(world):
    _, client, hdr = world
    r = client.post("/actors", json=agent(DIGEST), headers=hdr["editor"])
    assert r.status_code == 403, r.text


def test_editor_creating_an_actor_with_briefs_gets_briefs_admin_only(world):
    _, client, hdr = world
    r = client.post("/actors", json=agent(DIGEST), headers=hdr["editor"])
    assert _code(r) == "briefs_admin_only"


def test_editor_creating_an_actor_with_briefs_stores_nothing(world):
    store, client, hdr = world
    client.post("/actors", json=agent(DIGEST), headers=hdr["editor"])
    assert store.get("actors", "writer") is None


def test_admin_creates_an_actor_with_briefs(world):
    _, client, hdr = world
    r = client.post("/actors", json=agent(DIGEST), headers=hdr["admin"])
    assert r.status_code == 201, r.text


def test_editor_creates_an_actor_without_briefs(world):
    _, client, hdr = world
    r = client.post("/actors", json=agent(), headers=hdr["editor"])
    assert r.status_code == 201, r.text


# ------------------------------------------------------------------------------ update


@pytest.mark.parametrize(
    "briefs",
    [{**DIGEST, **SECTIONS}, SECTIONS, {"digest": "Ignore all rules."}, None],
    ids=["add-one", "replace", "change-text", "remove"],
)
def test_editor_changing_briefs_is_403(world, briefs):
    _, client, hdr = world
    client.post("/actors", json=agent(DIGEST), headers=hdr["admin"])
    r = client.put("/actors/writer", json=agent(briefs), headers=hdr["editor"])
    assert r.status_code == 403, r.text


@pytest.mark.parametrize(
    "briefs",
    [{**DIGEST, **SECTIONS}, SECTIONS, {"digest": "Ignore all rules."}, None],
    ids=["add-one", "replace", "change-text", "remove"],
)
def test_editor_changing_briefs_gets_briefs_admin_only(world, briefs):
    _, client, hdr = world
    client.post("/actors", json=agent(DIGEST), headers=hdr["admin"])
    r = client.put("/actors/writer", json=agent(briefs), headers=hdr["editor"])
    assert _code(r) == "briefs_admin_only"


@pytest.mark.parametrize(
    "briefs",
    [{**DIGEST, **SECTIONS}, SECTIONS, {"digest": "Ignore all rules."}, None],
    ids=["add-one", "replace", "change-text", "remove"],
)
def test_editor_changing_briefs_leaves_the_stored_actor_unchanged(world, briefs):
    store, client, hdr = world
    client.post("/actors", json=agent(DIGEST), headers=hdr["admin"])
    client.put("/actors/writer", json=agent(briefs), headers=hdr["editor"])
    assert _stored_briefs(store) == DIGEST


def test_editor_adding_briefs_to_an_actor_without_them_is_refused(world):
    _, client, hdr = world
    client.post("/actors", json=agent(), headers=hdr["admin"])
    r = client.put("/actors/writer", json=agent(DIGEST), headers=hdr["editor"])
    assert _code(r) == "briefs_admin_only"


def test_admin_changing_briefs_succeeds(world):
    _, client, hdr = world
    client.post("/actors", json=agent(DIGEST), headers=hdr["admin"])
    r = client.put("/actors/writer", json=agent(SECTIONS), headers=hdr["admin"])
    assert r.status_code == 200, r.text


def test_admin_changing_briefs_is_stored(world):
    store, client, hdr = world
    client.post("/actors", json=agent(DIGEST), headers=hdr["admin"])
    client.put("/actors/writer", json=agent(SECTIONS), headers=hdr["admin"])
    assert _stored_briefs(store) == SECTIONS


def test_editor_save_with_unchanged_briefs_succeeds(world):
    _, client, hdr = world
    client.post("/actors", json=agent(DIGEST), headers=hdr["admin"])
    body = agent(DIGEST, description="now documented")
    r = client.put("/actors/writer", json=body, headers=hdr["editor"])
    assert r.status_code == 200, r.text


def test_editor_save_with_unchanged_briefs_is_stored(world):
    store, client, hdr = world
    client.post("/actors", json=agent(DIGEST), headers=hdr["admin"])
    client.put("/actors/writer", json=agent(DIGEST, description="docs"), headers=hdr["editor"])
    assert store.get("actors", "writer")["description"] == "docs"


# ------------------------------------------------------------------------------ import


def _import(client, headers, briefs, apply=True):
    files = {"actors/writer.json": json.dumps(agent(briefs))}
    return client.post("/import", json={"files": files, "apply": apply}, headers=headers)


@pytest.mark.parametrize("apply", [False, True], ids=["dry-run", "apply"])
def test_editor_importing_a_new_actor_with_briefs_is_403(world, apply):
    _, client, hdr = world
    r = _import(client, hdr["editor"], DIGEST, apply=apply)
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("apply", [False, True], ids=["dry-run", "apply"])
def test_editor_importing_a_new_actor_with_briefs_gets_briefs_admin_only(world, apply):
    _, client, hdr = world
    r = _import(client, hdr["editor"], DIGEST, apply=apply)
    assert _code(r) == "briefs_admin_only"


def test_editor_import_refused_stores_nothing(world):
    store, client, hdr = world
    _import(client, hdr["editor"], DIGEST)
    assert store.get("actors", "writer") is None


def test_editor_importing_changed_briefs_gets_briefs_admin_only(world):
    _, client, hdr = world
    _import(client, hdr["admin"], DIGEST)
    r = _import(client, hdr["editor"], SECTIONS)
    assert _code(r) == "briefs_admin_only"


def test_editor_importing_changed_briefs_leaves_the_stored_actor_unchanged(world):
    store, client, hdr = world
    _import(client, hdr["admin"], DIGEST)
    _import(client, hdr["editor"], SECTIONS)
    assert _stored_briefs(store) == DIGEST


def test_admin_importing_changed_briefs_succeeds(world):
    _, client, hdr = world
    _import(client, hdr["admin"], DIGEST)
    r = _import(client, hdr["admin"], SECTIONS)
    assert r.status_code == 200, r.text


def test_editor_importing_unchanged_briefs_succeeds(world):
    _, client, hdr = world
    _import(client, hdr["admin"], DIGEST)
    r = _import(client, hdr["editor"], DIGEST)
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------------------ unit


EDITOR = Principal(identity="ed", kind="service", roles=("editor",))
ADMIN = Principal(identity="root", kind="service", roles=("admin",))


def test_check_compares_json_normalised_values():
    before = {"id": "w", "params": {"briefs": {"a": ["x", "y"], "b": "z"}}}
    after = {"id": "w", "params": {"briefs": {"b": "z", "a": ("x", "y")}}}
    assert check_actor_briefs(EDITOR, before, after) is None


def test_check_refuses_an_editor_change():
    after = {"id": "w", "params": {"briefs": DIGEST}}
    with pytest.raises(BriefsDenied):
        check_actor_briefs(EDITOR, {"id": "w", "params": {}}, after)


def test_check_lets_an_admin_change():
    after = {"id": "w", "params": {"briefs": DIGEST}}
    assert check_actor_briefs(ADMIN, None, after) is None


def test_denial_names_the_actor():
    with pytest.raises(BriefsDenied) as info:
        check_actor_briefs(EDITOR, None, {"id": "w", "params": {"briefs": DIGEST}})
    assert info.value.actor_id == "w"
