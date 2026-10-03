"""The route -> required-role matrix (pure; the server enforces it before any handler)."""

from __future__ import annotations

import pytest

from culture_rules.auth.policy import required_role


@pytest.mark.parametrize(
    "method, path, role",
    [
        ("GET", "/rules", "viewer"),
        ("GET", "/runs/r1", "viewer"),
        ("GET", "/events/stream", "viewer"),
        ("GET", "/whoami", "viewer"),
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
        ("POST", "/rules/r1/purge", "admin"),
        ("POST", "/workflows/wf/purge", "admin"),
        ("POST", "/service-tokens", "admin"),
        ("DELETE", "/service-tokens/t1", "admin"),
        ("POST", "/machines/thor/drain", "admin"),
        ("POST", "/machines/thor/undrain", "admin"),
        ("POST", "/controls/pause", "admin"),
        ("POST", "/controls/resume", "admin"),
        ("POST", "/something/new", "admin"),  # unknown mutations fail closed
        ("PATCH", "/rules/r1", "admin"),
    ],
)
def test_required_role(method, path, role):
    assert required_role(method, path) == role
