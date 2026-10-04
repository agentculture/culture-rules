"""Change-feed consumers that fire a handler exactly once per *settled* document.

:class:`~culture_rules.events.triggers.EventTriggers` fires once per inserted event. Rule
chains (must/may run after) need the same guarantee for things that happen *later* for an
event: a predecessor's run reaching a terminal state, a waiting decision being settled as a
final skip, a firing intent whose run could not start. :class:`FeedConsumer` watches one or
more collections' change feeds and, for every change whose post-image a :class:`Source`
keys (``key(doc)`` is not ``None``), commits in **one store transaction**:

- a marker ``event_fires/<consumer>/<collection>/<key>`` (inserted; a duplicate means
  this consumer already handled that key, on any host, so it is skipped),
- every write the handler makes through ``tx``, and
- the advanced per-collection resume token.

Either all of it commits or none of it does, exactly as for event triggers. A handler
exception (including :class:`~culture_rules.node.firing.Deferred`) rolls its change back
and leaves that collection's token before it; the other sources are still polled and the
first exception is re-raised afterwards. Changes nobody keys only move the token (saved
once, at the end of the poll). A keyed document whose rule no live rule depends on (its id
is in no rule's ``must_after`` / ``may_after``) is treated the same way: nothing could
continue a chain from it, so it opens no transaction. The dependants set is read from the
``rules`` collection at most once per poll of a source, lazily, so a rule saved between
polls is seen by the next poll. The first poll of a new consumer pins each feed's head.
Standard-library only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from culture_rules.events.triggers import FIRES_COLLECTION
from culture_rules.model.rule import Rule
from culture_rules.store.port import (
    CURSOR_COLLECTION,
    DuplicateKeyError,
    StoragePort,
    StoreOps,
    cursor_id,
)
from culture_rules.store.versioning import utc_timestamp

__all__ = ["FeedConsumer", "Source"]


@dataclass(frozen=True)
class Source:
    """One watched collection: which post-images matter and what to do with them.

    ``key(doc)`` answers the marker key of a document worth handling (``None``: ignore the
    change); ``handler(tx, doc, marker_id)`` writes through ``tx``.
    """

    collection: str
    key: Callable[[Mapping[str, Any]], str | None]
    handler: Callable[[StoreOps, Mapping[str, Any], str], None]


def _rule_of(doc: Mapping[str, Any]) -> str | None:
    """The rule id a run, decision or firing intent belongs to (``None``: unknown)."""
    rule = doc.get("rule")
    rid = rule.get("id") if isinstance(rule, Mapping) else None
    rid = rid or doc.get("rule_id")
    return rid if isinstance(rid, str) and rid else None


def _change_key(source: Source, change: Any) -> str | None:
    """The source key of a change's document (``None`` for a delete or a non-keyed doc)."""
    doc = change.document
    return source.key(doc) if doc is not None and change.op != "delete" else None


class _AlreadyHandled(Exception):
    """Internal: the marker exists; abort the transaction."""


class FeedConsumer:
    """Fires each :class:`Source` handler exactly once per keyed document (module doc)."""

    def __init__(
        self,
        store: StoragePort,
        sources: tuple[Source, ...],
        *,
        host: str,
        consumer: str,
        handler_collections: tuple[str, ...] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(consumer, str) or not consumer:
            raise ValueError("consumer must be a non-empty string")
        self.store = store
        self.sources = sources
        self.host = host
        self.consumer = consumer
        self._clock = clock or (lambda: datetime.now(UTC))
        ensure = getattr(store, "ensure_collections", None)
        if callable(ensure):  # Mongo: collections must exist before a transaction uses them
            ensure(
                FIRES_COLLECTION,
                CURSOR_COLLECTION,
                *(s.collection for s in sources),
                *handler_collections,
            )

    def marker_id(self, collection: str, key: str) -> str:
        """Id of the marker for ``key`` from ``collection`` under this consumer."""
        return f"{self.consumer}/{collection}/{key}"

    def _token(self, collection: str) -> str:
        position = self.store.load_cursor(self.consumer, collection)
        if position is None:
            position = self.store.head(collection)
            self.store.save_cursor(self.consumer, collection, position)
        return position

    def _fire(self, source: Source, doc: Mapping[str, Any], key: str, token: str) -> bool:
        marker_id = self.marker_id(source.collection, key)
        marker = {
            "id": marker_id,
            "consumer": self.consumer,
            "collection": source.collection,
            "key": key,
            "host": self.host,
            "fired_at": utc_timestamp(self._clock()),
        }
        cursor = {
            "id": cursor_id(self.consumer, source.collection),
            "consumer": self.consumer,
            "collection": source.collection,
            "token": token,
        }
        try:
            with self.store.transaction() as tx:
                if tx.get(FIRES_COLLECTION, marker_id) is not None:
                    raise _AlreadyHandled
                try:
                    tx.insert(FIRES_COLLECTION, marker)
                except DuplicateKeyError:
                    raise _AlreadyHandled from None
                source.handler(tx, doc, marker_id)
                tx.put(CURSOR_COLLECTION, cursor)
        except _AlreadyHandled:
            self.store.save_cursor(self.consumer, source.collection, token)
            return False
        return True

    def _dependencies(self) -> set[str]:
        """Rule ids some live rule must or may run after."""
        out: set[str] = set()
        for d in self.store.find("rules"):
            if d.get("deleted_at"):
                continue
            rule = Rule.from_dict(d, strict=False)
            out.update(rule.must_after)
            out.update(rule.may_after)
        return out

    def _poll_source(self, source: Source) -> list[str]:
        token = saved = self._token(source.collection)
        fired: list[str] = []
        depended: set[str] | None = None  # loaded on the first keyed document
        for change in self.store.changes(source.collection, token):
            token = change.token
            doc = change.document
            key = _change_key(source, change)
            if key is None:
                continue
            rid = _rule_of(doc)
            if rid is not None:
                if depended is None:
                    depended = self._dependencies()
                if rid not in depended:
                    continue  # nobody chains after it: only the cursor moves
            if self._fire(source, doc, key, token):
                fired.append(self.marker_id(source.collection, key))
            saved = token
        if token != saved:
            self.store.save_cursor(self.consumer, source.collection, token)
        return fired

    def poll(self) -> list[str]:
        """Process every source's pending changes; answer the marker ids this poll committed.

        A failure in one source does not stop the others; the first is re-raised at the end.
        """
        fired: list[str] = []
        failure: Exception | None = None
        for source in self.sources:
            try:
                fired += self._poll_source(source)
            except Exception as exc:  # noqa: BLE001 - re-raised below after the other sources
                failure = failure or exc
        if failure is not None:
            raise failure
        return fired
