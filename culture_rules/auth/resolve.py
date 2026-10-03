"""Resolve a request's headers into a :class:`Principal`, per listener.

Two listeners, on purpose (culture-nodes ``internal/api/principal.go``):

- ``loopback`` - reached only by ``cloudflared`` on the same host, i.e. traffic that passed
  Cloudflare Access. Honours ``Cf-Access-Jwt-Assertion`` (when Access is configured); a
  present-but-invalid assertion is refused outright, never silently downgraded.
- ``lan`` - the machines, the CLI and agents. Ignores the Access header (a JWT seen on the
  plaintext LAN is useless there) and requires ``Authorization: Bearer <service token>``.

A service token works on both. Roles: a token carries its own; an SSO principal gets
``sso_default_role`` (viewer) plus editor/admin when its identity is listed in ``editors`` /
``admins``; ``admins`` also elevates any other principal. The ``X-Culture-Identity`` dev
header is honoured only with ``insecure_dev_identity=True`` (off by default), and then makes
every unauthenticated caller an admin - never enable it outside a laptop.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from culture_rules.auth.access import AccessIdentity, AccessVerifier
from culture_rules.auth.principal import Principal, Unauthenticated, role_rank
from culture_rules.auth.tokens import ServiceTokens

__all__ = [
    "ACCESS_HEADER",
    "ANONYMOUS",
    "DEV_IDENTITY_HEADER",
    "LAN",
    "LOOPBACK",
    "AuthSettings",
    "Resolver",
]

ACCESS_HEADER = "Cf-Access-Jwt-Assertion"
DEV_IDENTITY_HEADER = "X-Culture-Identity"
ANONYMOUS = "anonymous"
LOOPBACK = "loopback"
LAN = "lan"


@dataclass(frozen=True)
class AuthSettings:
    """Per-listener authentication configuration (no secrets, no per-request state)."""

    listener: str = LAN
    access: AccessVerifier | None = None
    admins: frozenset[str] = field(default_factory=frozenset)
    editors: frozenset[str] = field(default_factory=frozenset)
    sso_default_role: str = "viewer"
    insecure_dev_identity: bool = False

    def __post_init__(self) -> None:
        if self.listener not in (LOOPBACK, LAN):
            raise ValueError(f"listener must be {LOOPBACK!r} or {LAN!r}, not {self.listener!r}")
        role_rank(self.sso_default_role)
        object.__setattr__(self, "admins", frozenset(self.admins))
        object.__setattr__(self, "editors", frozenset(self.editors))

    def with_admins(self, admins: Iterable[str]) -> AuthSettings:
        return AuthSettings(
            listener=self.listener,
            access=self.access,
            admins=self.admins | frozenset(admins),
            editors=self.editors,
            sso_default_role=self.sso_default_role,
            insecure_dev_identity=self.insecure_dev_identity,
        )


class Resolver:
    """``resolve(headers) -> Principal``; raises :class:`Unauthenticated` (HTTP 401)."""

    def __init__(self, settings: AuthSettings, tokens: ServiceTokens) -> None:
        self.settings = settings
        self._tokens = tokens

    def resolve(self, headers: Mapping[str, str]) -> Principal:
        h = {str(k).lower(): v for k, v in headers.items()}
        s = self.settings
        assertion = h.get(ACCESS_HEADER.lower(), "")
        if s.listener == LOOPBACK and s.access is not None and assertion:
            return self._from_access(s.access.verify(assertion))  # raises Unauthenticated
        authorization = h.get("authorization", "")
        if authorization:
            return self._from_bearer(authorization)
        if s.insecure_dev_identity:
            identity = (h.get(DEV_IDENTITY_HEADER.lower()) or "").strip() or ANONYMOUS
            return Principal(identity, "sso", frozenset({"admin"}))
        raise Unauthenticated(
            "no_credentials",
            "authenticate with 'Authorization: Bearer <service token>'"
            + (" or Cloudflare Access" if s.listener == LOOPBACK and s.access else ""),
        )

    def _from_access(self, asserted: AccessIdentity) -> Principal:
        s = self.settings
        # a person gets the default role; an Access service token only what is listed
        base = s.sso_default_role if asserted.kind == "sso" else "viewer"
        listed = "editor" if asserted.identity in s.editors else base
        role = max(base, listed, key=role_rank)
        return self._elevate(Principal(asserted.identity, asserted.kind, frozenset({role})))

    def _from_bearer(self, authorization: str) -> Principal:
        scheme, _, presented = authorization.partition(" ")
        principal = None
        if scheme.lower() == "bearer":
            principal = self._tokens.authenticate(presented.strip())
        if principal is None:
            raise Unauthenticated("bad_token", "the bearer token is unknown or revoked")
        return self._elevate(principal)

    def _elevate(self, principal: Principal) -> Principal:
        if principal.identity in self.settings.admins:
            return principal.with_roles({"admin"})
        return principal
