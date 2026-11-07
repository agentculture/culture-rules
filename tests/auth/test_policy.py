"""The route -> required-role matrix (pure; the server enforces it before any handler)."""

from __future__ import annotations

import pytest

from culture_rules.auth.policy import CoreRoutes, required_role

#: An app none of whose core routes matches the paths these tests use.
NO_CORE = CoreRoutes(())


@pytest.mark.parametrize(
    "method, path, role",
    [
        ("GET", "/rules", "viewer"),
        ("GET", "/runs/r1", "viewer"),
        ("GET", "/rules/r1/history", "viewer"),
        ("POST", "/rules/r1/history", "admin"),
        ("GET", "/events/stream", "viewer"),
        ("GET", "/whoami", "viewer"),
        ("POST", "/replay", "viewer"),
        ("GET", "/asks", "viewer"),
        ("GET", "/machines/status", "viewer"),
        ("GET", "/repos", "viewer"),
        ("GET", "/export", "viewer"),
        ("POST", "/export", "editor"),
        ("POST", "/rules/r1/purge", "admin"),
        ("HEAD", "/rules", "viewer"),
        ("GET", "/service-tokens", "admin"),
        ("POST", "/rules", "editor"),
        ("PUT", "/workflows/wf", "editor"),
        ("POST", "/actors/a/enable", "editor"),
        ("POST", "/machines/m/disable", "editor"),
        ("DELETE", "/rules/r1", "editor"),
        ("POST", "/rules/r1/restore", "editor"),
        ("POST", "/import", "editor"),
        ("POST", "/runs", "editor"),
        ("POST", "/runs/x/cancel", "editor"),
        ("POST", "/asks/a/answer", "editor"),
        ("POST", "/actors/a/purge", "admin"),
        ("POST", "/workflows/wf/purge", "admin"),
        ("POST", "/service-tokens", "admin"),
        ("DELETE", "/service-tokens/t1", "admin"),
        ("POST", "/machines/thor/drain", "admin"),
        ("POST", "/machines/thor/undrain", "admin"),
        ("POST", "/controls/pause", "admin"),
        ("POST", "/controls/resume", "admin"),
        ("POST", "/something/new", "admin"),  # unknown mutations fail closed
        ("PATCH", "/rules/r1", "admin"),
        ("GET", "/rollouts", "viewer"),
        ("GET", "/rollouts/0.18.12", "viewer"),
        ("PUT", "/rollouts/0.18.12", "admin"),  # the fail-closed default
        ("POST", "/rollouts/0.18.12/steps/pause", "admin"),  # the fail-closed default
        ("POST", "/rollouts/0.18.12/steps/upgrade:thor", "admin"),
        ("POST", "/rollouts/0.18.12/snapshot", "admin"),
    ],
)
def test_required_role(method, path, role):
    assert required_role(method, path) == role


def test_the_discord_targets_role_is_declared_by_the_discord_extension():
    from culture_rules.extensions import registry

    declared = {
        (route.extension, role) for route in registry().api_routes() for role in route.roles
    }
    assert declared == {("discord", ("GET", "/actors/{actor_id}/discord/targets", "viewer"))}


def test_the_policy_reads_the_declared_role():
    assert required_role("GET", "/actors/disc/discord/targets") == "viewer"


def _acme_routes(ext):
    ext.api_routes(
        "acme.routes", lambda app, ctx: None, roles=(("POST", "/acme/{thing}/poke", "editor"),)
    )


@pytest.fixture
def acme_routes():
    from culture_rules.extensions import registry

    registry().install("acme", _acme_routes)
    yield
    registry().uninstall("acme")


def test_an_extension_route_gets_the_role_it_declares(acme_routes):
    assert required_role("POST", "/acme/t1/poke", NO_CORE) == "editor"


def test_without_core_routes_a_declared_role_can_only_raise(acme_routes):
    """No app context (no core table): a declaration never lowers core's role."""
    assert required_role("POST", "/acme/t1/poke") == "admin"


def test_a_declared_role_never_applies_to_a_core_route(acme_routes):
    import re

    core = CoreRoutes([({"POST"}, re.compile(r"^/acme/[^/]+/poke$"))])
    assert required_role("POST", "/acme/t1/poke", core) == "admin"


@pytest.mark.parametrize(
    "core_path",
    [("POST", "/rules/{id}/purge"), ("PUT", "/variables/{name}"), ("POST", "/controls/pause")],
)
def test_an_extension_cannot_declare_a_role_for_a_core_route(core_path):
    """A declared path must name the extension (a /<name>/ segment); core paths never do, so
    an extension can never open a core admin or editor route to a lesser role."""
    from culture_rules.extensions import ExtensionError, Registry

    method, path = core_path
    reg = Registry()

    def register(ext):
        ext.api_routes("acme.routes", lambda app, ctx: None, roles=((method, path, "viewer"),))

    with pytest.raises(ExtensionError):
        reg.install("acme", register)


def test_a_core_admin_route_stays_admin_with_an_extension_installed(acme_routes):
    assert required_role("PUT", "/variables/secret") == "admin"
