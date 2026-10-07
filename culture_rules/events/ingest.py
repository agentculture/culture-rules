"""Ingest: drain a durable events-cli subscription into the ``events`` collection.

Each host runs its own :class:`EventIngest` over its own durable subscription
(see :func:`culture_rules.events.events_cli_adapter.subscription_name`), so an
event published once reaches every host. The ``events`` collection is keyed by
the **envelope id** - the store's unique document key, a unique index on Mongo
(``_id``) - so however many hosts or redeliveries hand the same envelope over,
it is stored exactly once: the first insert wins and every later one is a
counted duplicate.

The stored document (the seam consumed by triggers, replay and human asks)::

    {"id": <envelope id>, "envelope": <the envelope, verbatim>,
     "received_at": <ISO-8601 UTC>, "host": <ingesting host>,
     "schema_version": ..., "updated_at": ...}   # store envelope fields

An envelope claiming the engine's own namespace (run-event ids and types, internal sources)
or carrying an ``envelope`` field is never stored: it is quarantined
(:data:`QUARANTINE_COLLECTION`, :func:`quarantine`) and counted on the result (deviation d21).

Nothing in culture-rules updates or deletes a stored event; a redelivered
envelope with the same id - even with different content - never rewrites it.

Ordering: insert the batch, *then* persist the source cursor (in the store, per
host and subscription). A crash between the two re-drains the batch on restart
and the unique id absorbs it, so nothing is lost and nothing doubles.
Standard-library only.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from culture_rules.events.emit import reserved_reason
from culture_rules.events.source import EventFabricError, EventSource
from culture_rules.store.port import (
    CURSOR_COLLECTION,
    EVENTS_COLLECTION,
    Document,
    DuplicateKeyError,
    StoragePort,
)
from culture_rules.store.versioning import utc_timestamp

# EVENTS_COLLECTION (the collection, keyed by envelope id) lives in the store port.

log = logging.getLogger(__name__)

QUARANTINE_COLLECTION = "event_quarantine"
"""Envelopes refused at ingest because they claim the engine's own namespace
(:func:`~culture_rules.events.emit.reserved_reason`): one document per (refused envelope id,
content), never evaluated by a trigger, kept so the refusal is visible."""

DEFAULT_BATCH = 100
MAX_BATCH = 1000
"""Upper bound on one drain; there is no unbounded batch."""


def event_document(
    envelope: Mapping[str, Any], *, host: str, received_at: datetime | None = None
) -> Document:
    """The ``events`` document for ``envelope``: a deep copy plus ``received_at`` and ``host``."""
    return {
        "id": envelope["id"],
        "envelope": copy.deepcopy(dict(envelope)),
        "received_at": utc_timestamp(received_at),
        "host": host,
    }


def quarantine(
    store: StoragePort,
    envelope: Mapping[str, Any],
    reason: str,
    *,
    host: str,
    at: datetime | None = None,
) -> bool:
    """Record a refused ``envelope`` in :data:`QUARANTINE_COLLECTION` (once per id and
    content: the same refusal redelivered, or from every host, is one record); answer
    whether it was new. It is never inserted into ``events``, so no trigger sees it."""
    body = json.dumps(envelope, sort_keys=True, default=str).encode("utf-8")
    doc_id = "q_" + hashlib.sha256(body).hexdigest()[:32]
    log.warning("quarantined event %r: %s", envelope.get("id"), reason)
    try:
        store.insert(
            QUARANTINE_COLLECTION,
            {
                "id": doc_id,
                "envelope": copy.deepcopy(dict(envelope)),
                "reason": reason,
                "host": host,
                "received_at": utc_timestamp(at),
            },
        )
    except DuplicateKeyError:
        return False
    return True


@dataclass(frozen=True)
class IngestResult:
    """What one bounded drain did."""

    received: int
    inserted: int
    duplicates: int
    rejected: int
    cursor: str | None
    has_more: bool
    quarantined: int = 0


def _usable(envelope: Any) -> bool:
    return (
        isinstance(envelope, Mapping)
        and isinstance(envelope.get("id"), str)
        and bool(envelope["id"])
    )


class EventIngest:
    """Drains one host's subscription into :data:`EVENTS_COLLECTION` exactly once per id."""

    def __init__(
        self,
        store: StoragePort,
        source: EventSource,
        *,
        host: str,
        batch_size: int = DEFAULT_BATCH,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(host, str) or not host:
            raise ValueError("host must be a non-empty string")
        if (
            not isinstance(batch_size, int)
            or isinstance(batch_size, bool)
            or not 1 <= batch_size <= MAX_BATCH
        ):
            raise ValueError(f"batch_size must be an int in 1..{MAX_BATCH}")
        self.store = store
        self.source = source
        self.host = host
        self.batch_size = batch_size
        self._clock = clock or (lambda: datetime.now(UTC))
        ensure = getattr(store, "ensure_collections", None)
        if callable(ensure):  # Mongo: create collections before first use
            ensure(EVENTS_COLLECTION, CURSOR_COLLECTION)

    @property
    def consumer(self) -> str:
        """Cursor owner: one per host."""
        return f"ingest@{self.host}"

    @property
    def cursor_key(self) -> str:
        """Cursor slot: one per subscription."""
        return f"source:{self.source.name}"

    def ingest_once(self, timeout: float = 0.0) -> IngestResult:
        """Drain one bounded batch, store it, then persist the source cursor."""
        after = self.store.load_cursor(self.consumer, self.cursor_key)
        batch = self.source.drain(after, max=self.batch_size, timeout=timeout)
        if len(batch.envelopes) > self.batch_size:
            raise EventFabricError(
                f"source {self.source.name!r} returned {len(batch.envelopes)} envelopes, "
                f"over the bound of {self.batch_size}"
            )
        inserted = duplicates = rejected = quarantined = 0
        for envelope in batch.envelopes:
            if not _usable(envelope):
                rejected += 1
                continue
            reason = reserved_reason(envelope)
            if reason is not None:
                quarantine(self.store, envelope, reason, host=self.host, at=self._clock())
                quarantined += 1
                continue
            doc = event_document(envelope, host=self.host, received_at=self._clock())
            try:
                self.store.insert(EVENTS_COLLECTION, doc)
            except DuplicateKeyError:
                duplicates += 1
            else:
                inserted += 1
        if batch.cursor is not None and batch.cursor != after:
            self.store.save_cursor(self.consumer, self.cursor_key, batch.cursor)
        return IngestResult(
            received=len(batch.envelopes),
            inserted=inserted,
            duplicates=duplicates,
            rejected=rejected,
            cursor=batch.cursor,
            has_more=batch.has_more,
            quarantined=quarantined,
        )

    def ingest(self, max_batches: int = 10, timeout: float = 0.0) -> list[IngestResult]:
        """Drain up to ``max_batches`` bounded batches, stopping when nothing more is queued."""
        if not isinstance(max_batches, int) or max_batches < 1:
            raise ValueError("max_batches must be a positive int")
        results = []
        for _ in range(max_batches):
            result = self.ingest_once(timeout)
            results.append(result)
            if not result.has_more:
                break
        return results
