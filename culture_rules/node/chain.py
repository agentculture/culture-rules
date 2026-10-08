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
is in no rule's ``must_after`` / ``may_after``, and the rule has no concurrency key) is
treated the same way: nothing could continue a chain from it, so it opens no transaction -
unless it is a run or firing intent still recorded as a concurrency budget's holder, whose
end releases the key even after its rule was deleted or unkeyed. The dependants set is read from the
``rules`` collection at most once per poll of a source, lazily, so a rule saved between
polls is seen by the next poll. The first poll of a new consumer pins each feed's head.
Standard-library only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.events.triggers import FIRES_COLLECTION
from culture_rules.model.rule import Rule
from culture_rules.store.port import (
    CURSOR_COLLECTION,
    DuplicateKeyError,
    StoragePort,
    StoreOps,
    cursor_id,
    init_cursor,
)
from culture_rules.store.versioning import utc_timestamp

log = logging.getLogger("culture_rules.node.chain")


def live_rules(docs: Iterable[Mapping[str, Any]]) -> list[Rule]:
    """The non-deleted rules of ``docs``; a document that does not parse is logged and
    skipped, so one malformed rule never stops every other rule from firing."""
    rules = []
    for doc in docs:
        if doc.get("deleted_at"):
            continue
        try:
            rules.append(Rule.from_dict(doc, strict=False))
        except ValueError as exc:  # ModelParseError
            log.warning("rule %s skipped: unparseable: %s", doc.get("id"), exc)
    return rules


__all__ = ["CHAIN_NEEDS_REVIEW", "FeedConsumer", "Source", "Unrecoverable", "live_rules"]

CHAIN_NEEDS_REVIEW = "chain_needs_review"
"""Chain continuations a consumer could not recover (d21): one record per (consumer,
collection, key), naming the rule, the event and the dependants left undecided. The change
is *not* marked handled, so touching the document again once the cause is fixed retries it;
the retry that succeeds deletes the record in its own transaction. ``health_status`` counts
the records left, i.e. the unresolved ones."""


class Unrecoverable(Exception):
    """A handler cannot continue the chain from this document (its trigger envelope is gone
    and no durable reference holds it): roll back, record :data:`CHAIN_NEEDS_REVIEW`, move
    the cursor on, and leave the change unhandled (no marker)."""

    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__(f"chain continuation needs review: {record}")
        self.record = record


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
                CHAIN_NEEDS_REVIEW,
                *(s.collection for s in sources),
                *handler_collections,
            )

    def marker_id(self, collection: str, key: str) -> str:
        """Id of the marker for ``key`` from ``collection`` under this consumer."""
        return f"{self.consumer}/{collection}/{key}"

    def _token(self, collection: str) -> str:
        return init_cursor(self.store, self.consumer, collection)

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
                if tx.get(CHAIN_NEEDS_REVIEW, marker_id) is not None:
                    # an earlier attempt could not continue; this one did: resolve it
                    tx.delete(CHAIN_NEEDS_REVIEW, marker_id)
                tx.put(CURSOR_COLLECTION, cursor)
        except _AlreadyHandled:
            self.store.save_cursor(self.consumer, source.collection, token)
            return False
        except Unrecoverable as stuck:
            # rolled back: no marker, so the change stays unhandled and can be retried by
            # touching the document; the cursor moves on so the feed is not wedged
            review = {
                **stuck.record,
                "id": marker_id,
                "consumer": self.consumer,
                "collection": source.collection,
                "key": key,
                "host": self.host,
                "at": utc_timestamp(self._clock()),
            }
            try:
                self.store.insert(CHAIN_NEEDS_REVIEW, review)
            except DuplicateKeyError:
                pass
            log.error("chain %s: %s needs review: %s", self.consumer, marker_id, stuck.record)
            self.store.save_cursor(self.consumer, source.collection, token)
            return False
        return True

    def _dependencies(self) -> set[str]:
        """Rule ids some live rule must or may run after, plus every keyed rule (its run
        ending releases its concurrency key, :mod:`culture_rules.node.firing`)."""
        out: set[str] = set()
        for rule in live_rules(self.store.find("rules")):
            if rule.concurrency_key is not None:
                out.add(rule.id)
            out.update(rule.must_after)
            out.update(rule.may_after)
        return out

    def _holds_reservation(self, source: Source, doc: Mapping[str, Any]) -> bool:
        """Whether ``doc`` (a run, or a firing intent) is the holder recorded on a
        concurrency budget: its end must release the key and fire the event coalesced
        meanwhile even when its rule was deleted or lost its key since it fired, which
        drops the rule from :meth:`_dependencies` (:mod:`culture_rules.node.firing`)."""
        if source.collection == RUNS_COLLECTION:
            run_id = doc.get("id")
        elif source.collection == "rule_fires":
            run_id = doc.get("run_id")
        else:
            return False
        if not isinstance(run_id, str) or not run_id:
            return False
        return bool(self.store.find(RULE_ATTEMPT_BUDGETS, {"run_id": run_id}))

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
                if rid not in depended and not self._holds_reservation(source, doc):
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
