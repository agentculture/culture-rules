"""A declared extension role never applies to a core route (PR #142 review #7, Qodo #5).

An extension named ``acme`` declares ``viewer`` for core mutations on records whose id is
``acme``; the install-time check (a ``/acme/`` segment) lets those through, so the policy must
recognise them as core routes and keep core's role.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.policy import required_role  # noqa: E402
from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.extensions import registry  # noqa: E402
from culture_rules.server.app import create_app  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

LOWERED = (
    ("DELETE", "/rules/acme", "editor"),
    ("PUT", "/actors/acme", "editor"),
    ("PUT", "/variables/acme", "admin"),
    ("DELETE", "/variables/acme", "admin"),
    ("POST", "/workflows/acme/run", "editor"),
)


def _mount(app, ctx):
    @app.post("/acme/{thing}/poke", include_in_schema=False)
    def poke(thing: str) -> dict:
        return {"poked": thing}


def _register(ext):
    roles = tuple((method, path, "viewer") for method, path, _ in LOWERED)
    ext.api_routes("acme.routes", _mount, roles=(*roles, ("POST", "/acme/{thing}/poke", "editor")))


@pytest.fixture
def world():
    registry().install("acme", _register)
    try:
        store = MemoryStore()
        built = create_app(store, auth=AuthSettings(listener=LAN))
        yield built, store
    finally:
        registry().uninstall("acme")


@pytest.fixture
def app(world):
    return world[0]


def _bearer(store, role):
    issued = ServiceTokens(store).issue("root", name=role, roles=[role])
    return {"Authorization": f"Bearer {issued.token}"}


@pytest.mark.parametrize("method, path, role", LOWERED, ids=[f"{m} {p}" for m, p, _ in LOWERED])
def test_a_core_route_keeps_cores_role(app, method, path, role):
    assert required_role(method, path, app.core_routes) == role


@pytest.mark.parametrize("method, path, role", LOWERED, ids=[f"{m} {p}" for m, p, _ in LOWERED])
def test_without_an_app_a_declaration_cannot_lower_a_core_role(app, method, path, role):
    assert required_role(method, path) == role


def test_the_extensions_own_route_gets_its_declared_role(app):
    assert required_role("POST", "/acme/t1/poke", app.core_routes) == "editor"


def test_the_discord_targets_route_stays_viewer(app):
    path = "/actors/a1/discord/targets"
    assert required_role("GET", path, app.core_routes) == "viewer"


def test_the_extensions_routes_are_not_core(app):
    assert not app.core_routes.matches("POST", "/acme/t1/poke")


def test_core_routes_are_collected(app):
    assert app.core_routes.matches("DELETE", "/rules/r1")


def test_a_viewer_cannot_delete_the_rule_named_after_the_extension(world):
    app, store = world
    r = TestClient(app).delete("/rules/acme", headers=_bearer(store, "viewer"))
    assert r.status_code == 403


def test_an_editor_reaches_the_extension_route(world):
    app, store = world
    r = TestClient(app).post("/acme/t1/poke", headers=_bearer(store, "editor"))
    assert r.status_code == 200
