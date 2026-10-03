"""The request principal ``{identity, kind, roles}`` and the viewer < editor < admin order."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AuthError",
    "Forbidden",
    "KINDS",
    "ROLES",
    "Principal",
    "Unauthenticated",
    "role_rank",
    "validate_roles",
]

ROLES: tuple[str, ...] = ("viewer", "editor", "admin")
"""Every role, lowest first: each one includes everything the roles before it may do."""
KINDS: tuple[str, ...] = ("sso", "service", "agent")
"""sso = a person through Cloudflare Access; service = a service token; agent = a mesh agent."""


def role_rank(role: str) -> int:
    """Position of ``role`` in :data:`ROLES`; ``ValueError`` for an unknown role."""
    try:
        return ROLES.index(role)
    except ValueError:
        raise ValueError(f"unknown role {role!r}; expected one of {', '.join(ROLES)}") from None


def validate_roles(roles: Iterable[str]) -> frozenset[str]:
    out = frozenset(roles)
    for role in out:
        role_rank(role)
    return out


class AuthError(Exception):
    """A refused request: ``status`` is the HTTP status, ``code`` a stable reason string."""

    status = 401

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class Unauthenticated(AuthError):
    """No credential resolved to a principal (HTTP 401)."""

    status = 401


class Forbidden(AuthError):
    """The principal lacks the role the request needs (HTTP 403)."""

    status = 403


@dataclass(frozen=True)
class Principal:
    """Who is calling: a non-blank ``identity`` (audit identity), its ``kind`` and ``roles``."""

    identity: str
    kind: str
    roles: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, str) or not self.identity.strip():
            raise ValueError("principal identity must be a non-empty string")
        if self.kind not in KINDS:
            raise ValueError(f"unknown principal kind {self.kind!r}; expected one of {KINDS}")
        object.__setattr__(self, "roles", validate_roles(self.roles))

    def has_role(self, required: str) -> bool:
        """True iff some held role ranks at or above ``required``."""
        need = role_rank(required)
        return any(role_rank(r) >= need for r in self.roles)

    def with_roles(self, extra: Iterable[str]) -> Principal:
        return Principal(self.identity, self.kind, self.roles | validate_roles(extra))

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "kind": self.kind,
            "roles": sorted(self.roles, key=role_rank),
        }
