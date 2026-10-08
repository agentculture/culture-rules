"""Run-lifecycle events: one ``rules.run.*`` event per finished run (deviation d21).

Rule relationships (must/may run after) chain rules that fire on the *same* event. d21 adds
the other composition: a rule that fires when another rule's run *finishes*. Every run that
reaches a terminal status emits exactly one event into the ``events`` collection, where the
trigger consumers evaluate it like any other event, so an ordinary ``event`` trigger with
``params.type`` set to one of these types (plus a condition over ``trigger.data.*``) fires on
it.

Types
=====
One type per terminal status, never a shared type with a status field:

* ``rules.run.succeeded`` - the run (its workflow and its action) succeeded;
* ``rules.run.failed`` - it failed (after its ``on_failure`` action, if any);
* ``rules.run.cancelled`` - an operator cancelled it (or stopped a disabled rule's runs);
* ``rules.run.superseded`` - its ``head_unchanged`` wait guard saw the PR head move.

Separate types keep supersession and cancellation out of downstream work by construction: a
rule on ``rules.run.failed`` (a hand-back, a re-fix) never sees a superseded or cancelled run,
and there is no condition an author could forget.

Data
====
``data`` holds ``run_id``, ``rule_id``, ``workflow_id``, ``workflow_version``, ``status``,
``concurrency_key``, ``outputs`` (only the explicitly exported outputs), ``error_code``,
``error_message``, ``trigger_event_id``, ``trigger_type`` and the trigger's subject fields
(:data:`~culture_rules.engine.run_completions.SUBJECT_FIELDS`) at the top level. Lineage:
``causationId`` = the run's trigger, ``correlationId`` inherited, ``runId`` = the run,
``hops`` = the trigger's hops plus one. See :mod:`culture_rules.engine.run_completions`.

Exactly once: an outbox
=======================
The event is built **once**, in the run's terminal transition, and stored with it in the same
transaction as an immutable completion record (:mod:`culture_rules.engine.run_completions`).
:class:`RunEventOutbox` - polled by every node, first in each cycle - delivers each record not
yet ``emitted``, in one transaction per record:

* the event is inserted under the record's id (read first: a duplicate key aborts a MongoDB
  transaction), and the record moves ``emitted: False -> True`` with the ``event_id`` it was
  stored under (compare-and-set);
* a node that dies before the commit leaves both untouched; the next poll, on any node,
  delivers it (no loss); two nodes racing write the same record and one transaction loses
  (no duplicate); a commit whose acknowledgement was lost is found ``emitted`` and skipped, and
  an identical event already stored is accepted as delivered;
* the outbox never consults change-feed history, so there is no head to pin and no upgrade
  window: a run finished by an older engine has no record and emits nothing; every terminal
  transition written by this engine has one and is delivered;
* during a global pause delivery is deferred (the run was accepted before the pause) and
  happens on resume.

The id namespace is the engine's alone: ingest and the webhook sink refuse ``runevt_*`` ids,
``rules.run.*`` types and internal sources (:func:`~culture_rules.events.emit.reserved_reason`;
refused envelopes are quarantined, visible, never evaluated). Should a *different* event still
occupy the id (written past those guards), it is quarantined as a conflict and the genuine
event is stored under the next free candidate (``<id>-genuine``, ``<id>-genuine-2`` ...,
each checked the same way); the record's ``event_id`` names it, and it is marked emitted
only once the stored envelope equals the genuine one.

Verification
============
Before a rule fires on a ``rules.run.*`` event the node compares it, field for field (the
whole envelope, extra keys included), with the envelope in the run's completion record under
the id that record says it was emitted as (:func:`verify_run_event`) and refuses every rule on
any difference - no record, not emitted, another id, any other byte - with the final skip
``run_event_unverified``. The record, not the mutable run document, is the reference.
Standard-library only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.engine.run_completions import (
    RUN_COMPLETIONS,
    RUN_EVENT_PREFIX,
    RUN_EVENT_SOURCE,
    RUN_EVENT_TYPES,
    SUBJECT_FIELDS,
    build_run_event,
    run_event_id,
)
from culture_rules.events.ingest import (
    EVENTS_COLLECTION,
    QUARANTINE_COLLECTION,
    QUARANTINE_RETENTION,
    bounded_payload,
    event_document,
)
from culture_rules.store.port import StoragePort, StoreOps
from culture_rules.store.versioning import utc_timestamp

__all__ = [
    "GENUINE_SUFFIX",
    "OUTBOX_BATCH",
    "PENDING_INDEX",
    "RUN_COMPLETIONS",
    "RUN_EVENTS_HOST",
    "RUN_EVENT_PREFIX",
    "RUN_EVENT_SOURCE",
    "RUN_EVENT_TYPES",
    "SUBJECT_FIELDS",
    "RunEventOutbox",
    "build_run_event",
    "DeliveryBlocked",
    "candidate_ids",
    "deliver",
    "is_run_event",
    "run_event_id",
    "verify_run_event",
]

log = logging.getLogger(__name__)

RUN_EVENTS_HOST = "run-events"
"""The ``host`` recorded on the stored event (it is not ingested from a host's subscription)."""
GENUINE_SUFFIX = "-genuine"
"""Appended to a run event's id when a different event already occupies it."""
MAX_CANDIDATES = 8
"""How many ids (:func:`candidate_ids`) a run event may be tried under before delivery is
refused as blocked."""
OUTBOX_BATCH = 100
"""At most this many completion records are delivered per poll (the rest on the next)."""


PENDING_INDEX = "run_completions_pending"
"""The partial index (``emitted``, id) over un-emitted completion records only."""


def ensure_pending_index(store: Any) -> None:
    """Create :data:`PENDING_INDEX` where the store supports indexes (MongoDB)."""
    ensure = getattr(store, "ensure_index", None)
    if callable(ensure):
        ensure(
            RUN_COMPLETIONS,
            [("emitted", 1), ("id", 1)],
            name=PENDING_INDEX,
            partial={"emitted": False},
        )


def is_run_event(envelope: Mapping[str, Any]) -> bool:
    """Whether ``envelope`` claims to be a run-lifecycle event (by its type)."""
    kind = envelope.get("type")
    return isinstance(kind, str) and kind.startswith(RUN_EVENT_PREFIX)


def candidate_ids(event_id: str) -> list[str]:
    """The ids a run event may be stored under, in order: its own id, then
    ``<id>-genuine``, ``<id>-genuine-2`` ... up to :data:`MAX_CANDIDATES` in all."""
    return [event_id, event_id + GENUINE_SUFFIX] + [
        f"{event_id}{GENUINE_SUFFIX}-{n}" for n in range(2, MAX_CANDIDATES)
    ]


def deliver(tx: StoreOps, record_id: str) -> str | None:
    """Deliver completion record ``record_id`` through ``tx``: store its event and mark it
    emitted. Answer the stored event id, or ``None`` when there was nothing to deliver.

    Every candidate id is checked: an id holding exactly the genuine envelope (under that id)
    is the delivered event; one holding anything else is quarantined as a conflict and the
    next candidate is tried. The record is marked emitted only once the stored envelope
    equals the genuine one; with every candidate taken, the conflicts are quarantined, an
    error is logged and the record stays pending."""
    record = tx.get(RUN_COMPLETIONS, record_id)
    if record is None or record.get("emitted") is not False:
        return None
    genuine = dict(record["envelope"])
    for event_id in candidate_ids(genuine["id"]):
        envelope = {**genuine, "id": event_id}
        existing = tx.get(EVENTS_COLLECTION, event_id)
        if existing is None:
            tx.insert(EVENTS_COLLECTION, event_document(envelope, host=RUN_EVENTS_HOST))
        elif existing.get("envelope") != envelope:
            _quarantine_conflict(tx, existing, record_id)
            continue
        moved = tx.update_if(
            RUN_COMPLETIONS,
            record_id,
            {"emitted": False},
            {"emitted": True, "event_id": event_id},
        )
        if not moved.won:
            # Another delivery changed the record first. On MongoDB that normally surfaces
            # as a write conflict aborting one of the two transactions; this guard covers
            # any adapter where it does not.
            raise DeliveryBlocked(f"completion {record_id} changed while delivering")
        return event_id
    # Every candidate is held by a conflicting event: they are quarantined (this transaction
    # commits that), and the record stays pending - never marked with a wrong event.
    log.error("run event of %s not delivered: every candidate id is taken", record_id)
    return None


class DeliveryBlocked(RuntimeError):
    """A completion could not be delivered in this transaction; it stays pending."""


def _quarantine_conflict(tx: StoreOps, existing: Mapping[str, Any], record_id: str) -> None:
    """Record the event occupying a candidate id (once per id), bounded like every other
    quarantine record (:func:`~culture_rules.events.ingest.quarantine`)."""
    doc_id = f"conflict/{existing.get('id')}"
    if tx.get(QUARANTINE_COLLECTION, doc_id) is not None:
        return
    envelope = existing.get("envelope") if isinstance(existing.get("envelope"), Mapping) else {}
    now = datetime.now(UTC)
    tx.insert(
        QUARANTINE_COLLECTION,
        {
            "id": doc_id,
            "envelope_id": existing.get("id"),
            "type": envelope.get("type"),
            "source": envelope.get("source"),
            "reason": f"occupies a candidate event id of run {record_id}'s completion",
            "host": RUN_EVENTS_HOST,
            "count": 1,
            "received_at": utc_timestamp(now),
            "last_seen": utc_timestamp(now),
            "expires_at": now + QUARANTINE_RETENTION,
            **bounded_payload(envelope),
        },
    )


class RunEventOutbox:
    """Delivers un-emitted completion records (module doc, "Exactly once: an outbox").

    ``paused(tx)`` answers whether delivery is deferred; ``defer(record)`` builds the
    exception raised then (the node's :class:`~culture_rules.node.firing.Deferred`);
    ``before()`` runs before anything is delivered (the node initialises its event-trigger
    cursors there)."""

    def __init__(
        self,
        store: StoragePort,
        *,
        paused: Callable[[StoreOps], bool],
        defer: Callable[[Mapping[str, Any]], Exception],
        batch: int = OUTBOX_BATCH,
        before: Callable[[], Any] | None = None,
    ) -> None:
        self.store = store
        self._paused = paused
        self._defer = defer
        self._batch = batch
        self._before = before
        ensure = getattr(store, "ensure_collections", None)
        if callable(ensure):  # Mongo: collections must exist before a transaction uses them
            ensure(RUN_COMPLETIONS, EVENTS_COLLECTION, QUARANTINE_COLLECTION)
        ensure_pending_index(store)

    def pending(self) -> list[Mapping[str, Any]]:
        """Un-emitted records, at most one batch: an equality query on ``emitted`` the store
        orders (by id) and limits itself - on MongoDB through the partial index
        :data:`PENDING_INDEX`, so an empty queue costs nothing however long the history."""
        return self.store.find(RUN_COMPLETIONS, {"emitted": False}, limit=self._batch)

    def poll(self) -> list[str]:
        """Deliver every pending record (one transaction each); answer the stored event ids.
        A pause raises the deferral before anything is written."""
        delivered: list[str] = []
        pending = self.pending()
        if pending and self._before is not None:
            # the event-trigger cursors must exist before an event is emitted, or a cursor
            # pinned later would start after it and no rule would see it
            self._before()
        for record in pending:
            try:
                with self.store.transaction() as tx:
                    if self._paused(tx):
                        raise self._defer(record)
                    event_id = deliver(tx, record["id"])
            except DeliveryBlocked as blocked:
                # rolled back; the record stays pending and the others still go out
                log.error("run event of %s not delivered: %s", record["id"], blocked)
                continue
            if event_id is not None:
                delivered.append(event_id)
        return delivered


def verify_run_event(tx: StoreOps, envelope: Mapping[str, Any]) -> str | None:
    """``None`` when ``envelope`` is exactly the event its run's completion record emitted;
    otherwise why not (the ``run_event_unverified`` detail). Reads the record through ``tx``."""
    data = envelope.get("data")
    run_id = data.get("run_id") if isinstance(data, Mapping) else None
    if not isinstance(run_id, str) or not run_id:
        return "names no run"
    record = tx.get(RUN_COMPLETIONS, run_id)
    if record is None:
        return f"no completion is recorded for run {run_id}"
    if record.get("emitted") is not True or not record.get("event_id"):
        return f"run {run_id}'s completion has not been emitted"
    expected = {**dict(record["envelope"]), "id": record["event_id"]}
    if dict(envelope) == expected:
        return None
    for key in sorted(set(expected) | set(envelope)):
        if (key in envelope) != (key in expected) or envelope.get(key) != expected.get(key):
            return f"{key} differs from what run {run_id} emitted"
    return f"differs from what run {run_id} emitted"  # pragma: no cover - unequal, no key?
