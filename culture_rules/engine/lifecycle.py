"""Soft delete, restore and purge for stored items (rules, workflows, actors, ...).

``soft_delete`` tombstones an item in place: the document and its audit
history are kept, it is restorable for :data:`RETENTION` (30 days), and it
never fires while deleted (:meth:`Lifecycle.is_fireable`). ``purge`` removes
the document for good and is allowed only for admins, and only with
``apply=True`` (dry-run otherwise). Audit entries survive a purge.

Tombstone fields live on the item document: ``deleted_at``, ``deleted_by``,
``restorable_until``. Each verb writes its mutation and exactly one audit entry
in a single transaction. Standard-library only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.engine.audit import AuditLog, mutating_verb, require_identity
from culture_rules.store.port import Document, StoragePort

RETENTION = timedelta(days=30)

_LIVE = {"deleted_at": None, "deleted_by": None, "restorable_until": None}


class LifecycleError(ValueError):
    """The requested lifecycle transition is not valid for the item's state."""


class PermissionDenied(LifecycleError):
    """The identity is not allowed to perform this verb."""


@dataclass(frozen=True)
class PurgeResult:
    """Outcome of :meth:`Lifecycle.purge`; ``applied`` is False for a dry run."""

    collection: str
    id: str
    applied: bool


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


class Lifecycle:
    """Soft-delete lifecycle over a :class:`StoragePort`, audited via ``audit``."""

    def __init__(
        self,
        store: StoragePort,
        audit: AuditLog,
        *,
        admins: Iterable[str] = (),
        clock: Callable[[], datetime] | None = None,
    ):
        self._store = store
        self._audit = audit
        self._admins = frozenset(admins)
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def is_deleted(doc: dict[str, Any] | None) -> bool:
        return bool(doc and doc.get("deleted_at"))

    def is_fireable(self, collection: str, id: str) -> bool:
        """True iff the item exists and is not tombstoned."""
        doc = self._store.get(collection, id)
        return doc is not None and not self.is_deleted(doc)

    def assert_fireable(self, collection: str, id: str) -> None:
        if not self.is_fireable(collection, id):
            raise LifecycleError(f"{collection}/{id} is deleted or missing and must not fire")

    def _require(self, ops: Any, collection: str, id: str) -> Document:
        doc = ops.get(collection, id)
        if doc is None:
            raise LifecycleError(f"{collection}/{id} does not exist")
        return doc

    @mutating_verb("lifecycle.soft_delete", "Tombstone an item; restorable for 30 days")
    def soft_delete(self, collection: str, id: str, identity: str) -> Document:
        require_identity(identity)
        now = self._clock()
        with self._store.transaction() as tx:
            before = self._require(tx, collection, id)
            if self.is_deleted(before):
                raise LifecycleError(f"{collection}/{id} is already deleted")
            res = tx.update_if(
                collection,
                id,
                {"deleted_at": None},
                {
                    "deleted_at": _iso(now),
                    "deleted_by": identity,
                    "restorable_until": _iso(now + RETENTION),
                },
            )
            if not res.won:
                raise LifecycleError(f"{collection}/{id} changed concurrently")
            after = res.document
            self._audit.write(
                tx,
                identity=identity,
                verb="lifecycle.soft_delete",
                collection=collection,
                target_id=id,
                before=before,
                after=after,
            )
        return after

    @mutating_verb("lifecycle.restore", "Restore a tombstoned item within the 30-day window")
    def restore(self, collection: str, id: str, identity: str) -> Document:
        require_identity(identity)
        now = self._clock()
        with self._store.transaction() as tx:
            before = self._require(tx, collection, id)
            if not self.is_deleted(before):
                raise LifecycleError(f"{collection}/{id} is not deleted")
            if now > datetime.fromisoformat(before["restorable_until"]):
                raise LifecycleError(f"restore window expired for {collection}/{id}")
            res = tx.update_if(collection, id, {"deleted_at": before["deleted_at"]}, _LIVE)
            if not res.won:
                raise LifecycleError(f"{collection}/{id} changed concurrently")
            after = res.document
            self._audit.write(
                tx,
                identity=identity,
                verb="lifecycle.restore",
                collection=collection,
                target_id=id,
                before=before,
                after=after,
            )
        return after

    @mutating_verb("lifecycle.purge", "Permanently remove a tombstoned item (admin, --apply)")
    def purge(self, collection: str, id: str, identity: str, *, apply: bool = False) -> PurgeResult:
        require_identity(identity)
        if identity not in self._admins:
            raise PermissionDenied(f"{identity!r} may not purge; admin required")
        with self._store.transaction() as tx:
            before = self._require(tx, collection, id)
            if not self.is_deleted(before):
                raise LifecycleError(f"{collection}/{id} must be soft-deleted before purge")
            if not apply:
                return PurgeResult(collection, id, applied=False)
            tx.delete(collection, id)
            self._audit.write(
                tx,
                identity=identity,
                verb="lifecycle.purge",
                collection=collection,
                target_id=id,
                before=before,
                after=None,
            )
        return PurgeResult(collection, id, applied=True)
