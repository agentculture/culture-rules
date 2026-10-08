"""Triggers: fire once per event inserted into ``events``, across host failover.

:class:`EventTriggers` consumes :data:`~culture_rules.events.ingest.EVENTS_COLLECTION`
through the StoragePort change feed (Mongo change streams) and calls a handler
for every inserted event. The resume token is persisted in the store under the
trigger's ``consumer`` name, so when the active host dies another host
constructing an :class:`EventTriggers` with the same ``consumer`` resumes where
it left off.

Exactly once
------------
A resume token alone gives at-least-once: a host can fire and die before saving
the token. So each fire is committed in **one store transaction** together with

- a *fire marker* ``event_fires/<consumer>/<event id>`` (inserted; a duplicate
  key means another host - or this one, before a crash - already fired it, so
  the event is skipped),
- every write the handler makes through the transaction handle it is given
  (e.g. creating the run the rule starts), and
- the advanced resume token.

Either all of it commits or none of it does: a host that dies mid-handler
leaves nothing behind and the next host fires that event; a host that commits
has recorded both the fire and the token. A stale host replaying from an old
token finds the markers and skips. Handler side effects *outside* the store are
not covered - they must go through the run executor's idempotency keys.

Start position: the change feed has no "from the beginning" mode. The first
poll of a new consumer pins the current head as its token (inserted once, so two hosts
initialising together agree on it: :func:`~culture_rules.store.port.init_cursor`), so
events ingested before a trigger existed are never backfilled, and a failover
host never starts later than the first host did.

Only ``insert`` changes fire; culture-rules never updates or deletes a stored
event, and any such change is ignored (but still advances the token).
Standard-library only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.store.port import (
    CURSOR_COLLECTION,
    Document,
    DuplicateKeyError,
    StoragePort,
    StoreOps,
    cursor_id,
    init_cursor,
)
from culture_rules.store.versioning import utc_timestamp

FIRES_COLLECTION = "event_fires"
"""One marker per (consumer, event) that has fired."""

DEFAULT_CONSUMER = "triggers"

Handler = Callable[[StoreOps, Mapping[str, Any]], None]
"""``handler(tx, event_document)``; write through ``tx`` to commit with the fire."""


class _AlreadyFired(Exception):
    """Internal: abort the transaction because the fire marker already exists."""


@dataclass(frozen=True)
class TriggerResult:
    """One poll: events fired, events skipped as already fired, changes ignored."""

    fired: tuple[str, ...]
    skipped: tuple[str, ...]
    ignored: int
    token: str


class EventTriggers:
    """Fires ``handler`` exactly once per event inserted into the ``events`` collection."""

    def __init__(
        self,
        store: StoragePort,
        handler: Handler,
        *,
        host: str,
        consumer: str = DEFAULT_CONSUMER,
        handler_collections: tuple[str, ...] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """``handler_collections`` names every collection the handler writes through ``tx``;
        on Mongo they must exist before a transaction first touches them."""
        if not isinstance(host, str) or not host:
            raise ValueError("host must be a non-empty string")
        if not isinstance(consumer, str) or not consumer:
            raise ValueError("consumer must be a non-empty string")
        self.store = store
        self.handler = handler
        self.host = host
        self.consumer = consumer
        self._clock = clock or (lambda: datetime.now(UTC))
        ensure = getattr(store, "ensure_collections", None)
        if callable(ensure):  # Mongo: collections must exist before a transaction uses them
            ensure(EVENTS_COLLECTION, FIRES_COLLECTION, CURSOR_COLLECTION, *handler_collections)

    def fire_id(self, event_id: str) -> str:
        """Id of the fire marker for ``event_id`` under this consumer."""
        return f"{self.consumer}/{event_id}"

    def _token(self) -> str:
        return init_cursor(self.store, self.consumer, EVENTS_COLLECTION)

    def cursor(self) -> str:
        """This consumer's resume token, initialised at the feed's head if it has none."""
        return self._token()

    def _cursor_doc(self, token: str) -> Document:
        # Same shape StoragePort.save_cursor writes, so load_cursor reads it back.
        return {
            "id": cursor_id(self.consumer, EVENTS_COLLECTION),
            "consumer": self.consumer,
            "collection": EVENTS_COLLECTION,
            "token": token,
        }

    def _fire(self, event: Mapping[str, Any], token: str) -> bool:
        """Commit marker + handler writes + token atomically; False if already fired."""
        marker = {
            "id": self.fire_id(event["id"]),
            "consumer": self.consumer,
            "event_id": event["id"],
            "host": self.host,
            "fired_at": utc_timestamp(self._clock()),
        }
        try:
            with self.store.transaction() as tx:
                if tx.get(FIRES_COLLECTION, marker["id"]) is not None:
                    raise _AlreadyFired
                try:
                    tx.insert(FIRES_COLLECTION, marker)
                except DuplicateKeyError:
                    raise _AlreadyFired from None
                self.handler(tx, event)
                tx.put(CURSOR_COLLECTION, self._cursor_doc(token))
        except _AlreadyFired:
            self.store.save_cursor(self.consumer, EVENTS_COLLECTION, token)
            return False
        return True

    def poll(self, timeout: float = 0.0) -> TriggerResult:
        """Process every change available after the persisted token (waiting up to ``timeout``).

        A handler exception rolls its event's transaction back and propagates;
        the token stays before that event, so the next poll (on any host)
        retries it.
        """
        token = self._token()
        fired: list[str] = []
        skipped: list[str] = []
        ignored = 0
        for change in self.store.changes(EVENTS_COLLECTION, token, timeout=timeout):
            token = change.token
            if change.op != "insert" or change.document is None:
                ignored += 1
                self.store.save_cursor(self.consumer, EVENTS_COLLECTION, token)
                continue
            if self._fire(change.document, token):
                fired.append(change.id)
            else:
                skipped.append(change.id)
        return TriggerResult(tuple(fired), tuple(skipped), ignored, token)
