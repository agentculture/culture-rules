"""Service tokens: bearer credentials for the CLI, MCP servers and mesh agents.

A token reads ``crt_<id>.<secret>``. Only ``sha256(secret)`` is stored (collection
:data:`SERVICE_TOKENS`, keyed by ``id``) together with the identity, kind (``service`` or
``agent``) and roles it grants; the plaintext is returned once, by :meth:`ServiceTokens.issue`.
The secret is 256 random bits, so a fast hash is enough (there is nothing to brute-force) and
the comparison is constant-time. Revocation tombstones the record (``revoked_at``) so the
audit trail keeps pointing at it. Issue and revoke are audited mutating verbs. Stdlib only.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from culture_rules.auth.principal import Principal, validate_roles
from culture_rules.engine.audit import AuditLog, mutating_verb, require_identity
from culture_rules.store.port import Document, StoragePort

__all__ = ["SERVICE_TOKENS", "TOKEN_PREFIX", "IssuedToken", "ServiceTokens", "TokenError"]

SERVICE_TOKENS = "service_tokens"
TOKEN_PREFIX = "crt_"  # nosec B105 - a public prefix, not a secret
TOKEN_KINDS = ("service", "agent")
_SEP = "."


class TokenError(ValueError):
    """An invalid issue/revoke request (bad roles, unknown or already-revoked token)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class IssuedToken:
    """The one moment the plaintext ``token`` exists; ``record`` is the stored doc sans hash."""

    id: str
    token: str
    record: dict[str, Any]


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def public(doc: Document) -> dict[str, Any]:
    """A stored record without its hash (what lists, responses and audit diffs may show)."""
    return {k: v for k, v in doc.items() if k != "hash"}


class ServiceTokens:
    """Issue, revoke and authenticate service tokens stored in ``store``."""

    def __init__(
        self,
        store: StoragePort,
        audit: AuditLog | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._audit = audit or AuditLog()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> str:
        return self._clock().astimezone(UTC).isoformat(timespec="microseconds")

    @mutating_verb("service_tokens.issue", "Issue a hashed service token for an identity/roles")
    def issue(
        self, identity: str, *, name: str, roles: Iterable[str], kind: str = "service"
    ) -> IssuedToken:
        """Issue a token for principal ``name`` (granting ``roles``); ``identity`` is the issuer."""
        require_identity(identity)
        if not isinstance(name, str) or not name.strip():
            raise TokenError("invalid", "token name (its principal identity) must be non-empty")
        if kind not in TOKEN_KINDS:
            raise TokenError("invalid", f"token kind must be one of {', '.join(TOKEN_KINDS)}")
        try:
            granted = validate_roles(roles)
        except ValueError as exc:
            raise TokenError("invalid", str(exc)) from None
        if not granted:
            raise TokenError("invalid", "a token must grant at least one role")
        token_id = uuid.uuid4().hex[:16]
        secret = secrets.token_urlsafe(32)
        doc = {
            "id": token_id,
            "identity": name.strip(),
            "kind": kind,
            "roles": sorted(granted, key=("viewer", "editor", "admin").index),
            "hash": _hash(secret),
            "created_at": self._now(),
            "created_by": identity,
            "revoked_at": None,
            "revoked_by": None,
        }
        with self._store.transaction() as tx:
            stored = tx.insert(SERVICE_TOKENS, doc)
            self._audit.write(
                tx,
                identity=identity,
                verb="service_tokens.issue",
                collection=SERVICE_TOKENS,
                target_id=token_id,
                before=None,
                after=public(stored),
            )
        return IssuedToken(token_id, f"{TOKEN_PREFIX}{token_id}{_SEP}{secret}", public(stored))

    @mutating_verb("service_tokens.revoke", "Revoke a service token (it stops authenticating)")
    def revoke(self, token_id: str, identity: str) -> dict[str, Any]:
        require_identity(identity)
        with self._store.transaction() as tx:
            before = tx.get(SERVICE_TOKENS, token_id)
            if before is None:
                raise TokenError("not_found", f"service token {token_id!r} does not exist")
            if before.get("revoked_at"):
                raise TokenError("conflict", f"service token {token_id!r} is already revoked")
            res = tx.update_if(
                SERVICE_TOKENS,
                token_id,
                {"revoked_at": None},
                {"revoked_at": self._now(), "revoked_by": identity},
            )
            if not res.won:
                raise TokenError("conflict", f"service token {token_id!r} changed concurrently")
            self._audit.write(
                tx,
                identity=identity,
                verb="service_tokens.revoke",
                collection=SERVICE_TOKENS,
                target_id=token_id,
                before=public(before),
                after=public(res.document),
            )
        return public(res.document)

    def list(self) -> list[dict[str, Any]]:
        docs = self._store.find(SERVICE_TOKENS)
        return [public(d) for d in sorted(docs, key=lambda d: (d.get("created_at", ""), d["id"]))]

    def authenticate(self, presented: str) -> Principal | None:
        """The principal a live token grants, or None (unknown, garbled, wrong or revoked)."""
        if not isinstance(presented, str) or not presented.startswith(TOKEN_PREFIX):
            return None
        parts = presented[len(TOKEN_PREFIX) :].split(_SEP)
        if len(parts) != 2 or not all(parts):
            return None
        token_id, presented_secret = parts
        doc = self._store.get(SERVICE_TOKENS, token_id)
        if doc is None or doc.get("revoked_at"):
            return None
        if not hmac.compare_digest(_hash(presented_secret), str(doc.get("hash", ""))):
            return None
        try:
            return Principal(doc["identity"], doc["kind"], frozenset(doc.get("roles") or ()))
        except (KeyError, ValueError):
            return None
