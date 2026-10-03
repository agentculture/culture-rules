"""Append-only audit log: one entry per mutation, never edited or removed.

Every mutating verb writes exactly one entry, in the same transaction as the
mutation, carrying who (``identity``), where (``host``), when (``at``) and what
changed (``diff``). The module exposes only ``write`` (insert) and read
helpers; there is deliberately no update or delete API for entries.

Mutating verbs register themselves in :data:`MUTATING_VERBS` through
:func:`mutating_verb`, so a registry-wide test can prove none skips the log.
Standard-library only.
"""

from __future__ import annotations

import itertools
import socket
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.store.port import Document, StoreOps

AUDIT_COLLECTION = "audit"

MUTATING_VERBS: dict[str, str] = {}
"""Registry of verbs that mutate state: verb name -> one-line description."""

_ENVELOPE = frozenset({"id", "schema_version", "updated_at"})


class AuditError(ValueError):
    """An audit entry could not be written (e.g. the identity is empty)."""


def mutating_verb(name: str, description: str = "") -> Callable[[Callable], Callable]:
    """Register ``name`` as a mutating verb (decorator; returns the function unchanged)."""

    def decorate(fn: Callable) -> Callable:
        MUTATING_VERBS[name] = description or (fn.__doc__ or "").strip().split("\n")[0]
        return fn

    return decorate


def require_identity(identity: Any) -> str:
    """Return ``identity`` if it is a non-blank string, else raise :class:`AuditError`."""
    if not isinstance(identity, str) or not identity.strip():
        raise AuditError("identity must be a non-empty string")
    return identity


def diff(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> dict[str, Any]:
    """Field-level diff ``{field: {"before": x, "after": y}}``, ignoring envelope fields."""
    b, a = before or {}, after or {}
    out: dict[str, Any] = {}
    for key in sorted((set(b) | set(a)) - _ENVELOPE):
        if b.get(key) != a.get(key):
            out[key] = {"before": b.get(key), "after": a.get(key)}
    return out


class AuditLog:
    """Writer for audit entries (insert-only) plus a read helper."""

    def __init__(self, *, host: str | None = None, clock: Callable[[], datetime] | None = None):
        self.host = host or socket.gethostname()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._seq = itertools.count()

    def write(
        self,
        ops: StoreOps,
        *,
        identity: str,
        verb: str,
        collection: str,
        target_id: str,
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any] | None,
    ) -> Document:
        """Insert one entry through ``ops`` (pass a transaction handle to commit atomically)."""
        require_identity(identity)
        at = self._clock().astimezone(UTC).isoformat(timespec="microseconds")
        entry = {
            # time-sortable id; the sequence orders same-instant entries from one writer
            "id": f"{at}-{next(self._seq):06d}-{uuid.uuid4().hex[:8]}",
            "identity": identity,
            "host": self.host,
            "at": at,
            "verb": verb,
            "target": {"collection": collection, "id": target_id},
            "diff": diff(before, after),
        }
        return ops.insert(AUDIT_COLLECTION, entry)

    @staticmethod
    def entries(
        store: StoreOps, *, target_id: str | None = None, verb: str | None = None
    ) -> list[Document]:
        """Return entries in time order, optionally filtered by target id and/or verb."""
        where = {"verb": verb} if verb else None
        found = store.find(AUDIT_COLLECTION, where)
        if target_id is not None:
            found = [e for e in found if e["target"]["id"] == target_id]
        return sorted(found, key=lambda e: (e["at"], e["id"]))
