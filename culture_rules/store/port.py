"""The StoragePort: the one persistence seam every culture-rules component uses.

Claims, the run executor, the event fabric, the audit log and machine
heartbeats all talk to storage through this protocol, never to a driver. The
in-memory adapter (:mod:`culture_rules.store.memory`) and the MongoDB adapter
implement it and both pass the shared contract suite in
``tests/store/contract.py``. This module is standard-library only.

Model
=====

*Collections* hold *documents*: JSON-like ``dict`` values keyed by a string
``id``. Every stored document carries the **envelope** fields

``id``
    non-empty ``str``, unique within its collection.
``schema_version``
    ``"MAJOR.MINOR"``. Stamped with :attr:`StoragePort.node_schema_version`
    when the writer omits it.
``updated_at``
    ISO-8601 UTC timestamp, owned by the store and refreshed on every write.

Write rules (enforced by every adapter via
:func:`culture_rules.store.versioning.prepare_write`):

- writing a document whose major version is newer than the node's raises
  :class:`VersionSkewError`, and so does writing *over* such a document - an
  old node refuses instead of corrupting;
- a write never lowers an existing document's ``schema_version``
  (:class:`SchemaDowngradeError`);
- unknown fields are stored and returned verbatim; readers ignore what they do
  not understand.

Returned documents are always copies; mutating them never changes the store.

Equality filters (``find``'s ``where`` and ``update_if``'s ``expected``) match
top-level fields by equality, and ``None`` matches both an explicit ``null``
and a missing field.

Change feed
===========

Every committed write (insert, update/replace, delete) appends one
:class:`Change` to its collection's feed. :meth:`StoragePort.head` returns the
token of the current end of a feed; :meth:`StoragePort.changes` yields every
change committed strictly after a token, exactly once, in commit order. Resume
by passing the ``token`` of the last change processed. Consumers persist that
token in the store itself (:meth:`StoragePort.save_cursor`) so another host can
resume after failover. Failed or lost writes emit nothing. Tokens are opaque
strings meaningful only to the adapter that issued them.

Transactions
============

:meth:`StoragePort.transaction` returns a context manager yielding a
:class:`StoreOps` handle. Writes through it are atomic: they all commit when
the block exits normally, or none do if it raises (the exception propagates).
Reads through the handle see its own writes. Its changes appear in the feeds
only at commit, contiguously and in the order they were made. Do not nest
transactions or write through the store itself inside one.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

Document = dict[str, Any]
"""A stored document: a JSON-like mapping with the envelope fields."""

ChangeOp = Literal["insert", "update", "delete"]

CURSOR_COLLECTION = "_cursors"
"""Reserved collection holding change-feed cursors saved via ``save_cursor``."""

VARIABLES_COLLECTION = "variables"
"""Collection holding shared variables (``put_variable`` and friends)."""

EVENTS_COLLECTION = "events"
"""Collection holding every stored event envelope (see :mod:`culture_rules.events.ingest`)."""


class StoreError(Exception):
    """Base class for storage failures."""


class DuplicateKeyError(StoreError):
    """``insert`` found a document with the same id already in the collection."""


class TransientStoreError(StoreError):
    """A transient failure (e.g. a write conflict between concurrent transactions): the
    transaction was rolled back and retrying the whole transaction body may succeed (see
    :func:`culture_rules.store.retry.run_transaction`)."""


_OUTAGE_NAMES = frozenset(
    (
        "ConnectionFailure",
        "AutoReconnect",
        "NetworkTimeout",
        "ServerSelectionTimeoutError",
        "NotPrimaryError",
        "ExecutionTimeout",
        "WTimeoutError",
        "WaitQueueTimeoutError",
    )
)
"""Driver errors (by class name, so the core imports no driver) that mean the store is
unreachable or overloaded, never that a document's content is bad."""


def is_store_outage(exc: BaseException) -> bool:
    """Whether ``exc`` is a store failure rather than a refusal of a document's content: any
    :class:`StoreError`, or a driver error naming an outage (:data:`_OUTAGE_NAMES`). A
    caller that guards against bad content re-raises these, so an outage keeps its
    semantics (nothing is skipped, the work is retried)."""
    if isinstance(exc, StoreError):
        return True
    return any(cls.__name__ in _OUTAGE_NAMES for cls in type(exc).__mro__)


class VersionSkewError(StoreError):
    """A write involves a document of a newer major schema version than this node supports."""


class VariableVersionConflict(StoreError):
    """``put_variable(..., expected_version=n)`` found the variable at another version: someone
    else wrote it since it was read. Re-read and retry (a compare-and-set on the version)."""


class SchemaDowngradeError(StoreError):
    """A write would lower an existing document's ``schema_version``."""


@dataclass(frozen=True)
class UpdateResult:
    """Outcome of :meth:`StoreOps.update_if`.

    ``won`` is True iff this call's update was applied. ``document`` is the
    post-update document when won, otherwise the current document (e.g. the
    current claim holder), or None if no document exists.
    """

    won: bool
    document: Document | None


@dataclass(frozen=True)
class Change:
    """One committed write in a collection's change feed.

    ``op`` is ``"insert"`` (document did not exist), ``"update"`` (it was
    replaced or partially updated) or ``"delete"``. ``document`` is the full
    post-image as of this commit, or None for a delete. Resume after this
    change by passing ``token`` to :meth:`StoragePort.changes`.
    """

    token: str
    collection: str
    op: ChangeOp
    id: str
    document: Document | None


@runtime_checkable
class StoreOps(Protocol):
    """Document operations, available on the store and on a transaction handle."""

    def get(self, collection: str, id: str) -> Document | None:
        """Return the document with ``id``, or None."""

    def find(
        self,
        collection: str,
        where: Mapping[str, Any] | None = None,
        *,
        limit: int | None = None,
    ) -> list[Document]:
        """Return documents matching the equality filter ``where``, ordered by id."""

    def insert(self, collection: str, document: Mapping[str, Any]) -> Document:
        """Create a document; raise :class:`DuplicateKeyError` if the id exists.

        Returns the stored document (with its envelope).
        """

    def put(self, collection: str, document: Mapping[str, Any]) -> Document:
        """Create or fully replace the document with ``document["id"]``; return it."""

    def update_if(
        self,
        collection: str,
        id: str,
        expected: Mapping[str, Any],
        changes: Mapping[str, Any],
        *,
        upsert: bool = False,
    ) -> UpdateResult:
        """Atomically set ``changes`` on the document iff it matches ``expected``.

        This is the compare-and-set primitive for claims and leases: when many
        callers race on the same document, at most one whose ``expected`` held
        wins and every other caller is told it lost. ``changes`` are top-level
        field assignments; they cannot change ``id``. When no document exists,
        ``upsert=True`` creates one from ``changes`` provided every ``expected``
        value is None (a missing document matches nothing else); otherwise the
        call loses.
        """

    def delete(self, collection: str, id: str) -> bool:
        """Delete the document; return whether it existed."""


@runtime_checkable
class StoragePort(StoreOps, Protocol):
    """A culture-rules store: documents, change feeds, cursors and transactions."""

    @property
    def node_schema_version(self) -> Any:
        """The :class:`~culture_rules.store.versioning.SchemaVersion` this node writes."""

    def transaction(self) -> AbstractContextManager[StoreOps]:
        """Begin an atomic multi-document, multi-collection transaction."""

    def head(self, collection: str) -> str:
        """Return a token for the current end of ``collection``'s change feed."""

    def changes(self, collection: str, after: str, *, timeout: float = 0.0) -> Iterator[Change]:
        """Yield changes committed to ``collection`` strictly after token ``after``.

        If none are available, wait up to ``timeout`` seconds for the first
        one. Yields what is available, in commit order, then stops; call again
        with the last change's token to continue.
        """

    def save_cursor(self, consumer: str, collection: str, token: str) -> None:
        """Persist ``consumer``'s resume token for ``collection`` in the store."""

    def load_cursor(self, consumer: str, collection: str) -> str | None:
        """Return the token last saved by ``consumer`` for ``collection``, or None."""

    def find_events(
        self,
        *,
        types: Collection[str],
        after: tuple[str, str],
        until: str,
        limit: int,
    ) -> list[Document]:
        """Return stored events (:data:`EVENTS_COLLECTION`) in ``(received_at, id)`` order.

        Only events whose ``envelope.type`` is in ``types``, whose ``(received_at, id)`` is
        strictly greater than the ``after`` cursor and whose ``received_at`` is at most
        ``until`` match; at most ``limit`` (a positive int) are returned. Timestamps are the
        ``utc_timestamp`` ISO strings ``received_at`` is stored as, compared as strings, so
        ``(t, "")`` includes every event received at ``t``. Pass the last result's
        ``(received_at, id)`` as ``after`` to continue. On MongoDB an
        ``(envelope.type, received_at, _id)`` index serves it.
        """

    def put_variable(
        self,
        name: str,
        value: Any,
        *,
        updated_by: str,
        description: str | None = None,
        expected_version: int | None = None,
    ) -> Document:
        """Append a new version of variable ``name`` (version n+1, append-only).

        Validates ``name`` (must match :data:`~culture_rules.model.variable.VALID_VARIABLE_NAME_RE`)
        and ``value`` (must be a JSON scalar or list).  Returns the latest
        version document.  Raises :class:`ValueError` on validation failure.
        With ``expected_version`` (0 = the variable must not exist yet) the write is a
        compare-and-set: :class:`VariableVersionConflict` when the latest version differs.
        """

    def get_variable(self, name: str) -> Document | None:
        """Return the latest version document for variable ``name``, or ``None``."""

    def get_variable_version(self, name: str, version: int) -> Document | None:
        """Return a specific version document for variable ``name``, or ``None``."""

    def list_variables(self) -> list[Document]:
        """Return every variable's latest version document, ordered by name."""


def events_query(
    types: Collection[str], after: tuple[str, str], until: str, limit: int
) -> tuple[list[str], str, str]:
    """Validate :meth:`StoragePort.find_events` arguments; return ``(types, ts, id)``."""
    if isinstance(types, str) or not all(isinstance(t, str) and t for t in types):
        raise ValueError("types must be a collection of non-empty strings")
    if (
        not isinstance(after, tuple)
        or len(after) != 2
        or not all(isinstance(part, str) for part in after)
    ):
        raise ValueError("after must be a (received_at, id) tuple of strings")
    if not isinstance(until, str):
        raise ValueError("until must be a string timestamp")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive int")
    return sorted(set(types)), after[0], after[1]


def cursor_id(consumer: str, collection: str) -> str:
    """Document id under :data:`CURSOR_COLLECTION` for a consumer's cursor."""
    if not isinstance(consumer, str) or not consumer:
        raise ValueError("consumer must be a non-empty string")
    if not isinstance(collection, str) or not collection:
        raise ValueError("collection must be a non-empty string")
    return f"{consumer}/{collection}"


def init_cursor(store: StoragePort, consumer: str, collection: str) -> str:
    """``consumer``'s resume token for ``collection``, initialised once at the feed's head.

    A consumer's first poll pins the head. Two nodes initialising the same shared consumer
    at once must agree on one token, or the one that saved later (a newer head) would skip
    the changes in between for every node. So the first token is *inserted*: the loser of
    the insert loads the winner's token instead of overwriting it (d21 review)."""
    position = store.load_cursor(consumer, collection)
    if position is not None:
        return position
    head = store.head(collection)
    try:
        store.insert(
            CURSOR_COLLECTION,
            {
                "id": cursor_id(consumer, collection),
                "consumer": consumer,
                "collection": collection,
                "token": head,
            },
        )
    except DuplicateKeyError:
        won = store.load_cursor(consumer, collection)
        if won is None:
            raise TransientStoreError("cursor initialisation raced; retry") from None
        return won
    return head
