"""Exactly-once claims on firings and workflow steps, across hosts.

Several engine instances (one per host) may see the same rule firing or the
same runnable step. Before doing the work, an instance *claims* it; exactly one
claimant wins. A claim is one document in :data:`CLAIMS_COLLECTION` whose id is
the work's **idempotency key**, and every state change is a compare-and-set via
:meth:`~culture_rules.store.port.StoreOps.update_if` - so the guarantee holds on
any StoragePort adapter, not just in one process. Standard-library only.

Idempotency keys
================

:func:`idempotency_key` is a stable hash of ``(run_id, step_id)`` and nothing
else: no attempt number, host, time or process state goes in, so a retry, a
reclaim on another host and an audit replay all derive the same key. Consumers
pass it to every actor invocation (so an actor can deduplicate a lost-ack
retry) and record it on the audit entry. :func:`firing_key` is the analogous
key for a rule firing, ``(rule_id, event_id)``, in a disjoint key space.

Leases
======

A won claim carries a lease (``lease_expires_at``). The holder renews it while
working (:meth:`Claims.renew`; the run executor keeps a step's lease alive with a
:class:`~culture_rules.engine.leasekeeper.LeaseKeeper` for as long as the actor
invocation blocks). If the holder crashes, the lease lapses and any instance may
reclaim the work; the claim's ``attempt`` counter increments on every acquisition
and acts as a fencing token, so a stale holder's late
``renew``/``complete``/``release`` loses instead of clobbering the new holder.

A claimant may narrow reclaiming further with a ``may_reclaim`` guard: it is asked
only about a *lapsed* claim someone still holds, and returning False keeps the claim
``"held"`` (the executor uses it to refuse taking over a step whose holder's machine
is still heartbeating).

Lease expiry is judged by the *claiming* instance's clock: hosts are expected
to keep NTP-synchronised clocks, and leases should be long relative to the
skew between them.

Completion is final
===================

:meth:`Claims.complete` marks the claim ``completed``. A completed claim is
never reclaimed, however old it is - reclaim never re-runs a step whose
completion was recorded. To record the completion atomically with the step's
output, call ``claims.with_ops(tx).complete(claim)`` inside
:meth:`~culture_rules.store.port.StoragePort.transaction`.

Claim document fields
=====================

``key`` (= ``id``), ``kind`` (``"step"`` or ``"firing"``), the subject ids
(``run_id``/``step_id`` or ``rule_id``/``event_id``), ``status``
(``"claimed"``, ``"released"`` or ``"completed"``), ``holder``,
``previous_holder``, ``attempt``, ``claimed_at``, ``lease_expires_at``,
``completed_at`` and, when given, ``result``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from culture_rules.store.port import Document, StoreOps, TransientStoreError
from culture_rules.store.retry import DEFAULT_ATTEMPTS
from culture_rules.store.versioning import utc_timestamp

CLAIMS_COLLECTION = "claims"
"""Collection holding one claim document per idempotency key."""

KEY_PREFIX = "ik1:"
"""Version tag of the key derivation; changing the derivation means a new tag."""

DEFAULT_LEASE = timedelta(seconds=30)

ClaimKind = Literal["step", "firing"]
ClaimReason = Literal["acquired", "reclaimed", "held", "completed"]

_KINDS = ("step", "firing")
Clock = Callable[[], datetime]
ReclaimGuard = Callable[[Document], bool]
"""Asked whether a lapsed, still-held claim may be taken over (True: take it)."""


def _require_part(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _digest(parts: list[str]) -> str:
    # A JSON array is an unambiguous encoding: ("ab", "c") != ("a", "bc").
    payload = json.dumps(parts, separators=(",", ":"), ensure_ascii=False)
    return KEY_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def idempotency_key(run_id: str, step_id: str) -> str:
    """The idempotency key of step ``step_id`` in run ``run_id``.

    A stable hash of the two ids only - identical across retries, attempts,
    hosts, processes and releases.
    """
    return _digest([_require_part("run_id", run_id), _require_part("step_id", step_id)])


def firing_key(rule_id: str, event_id: str) -> str:
    """The idempotency key of rule ``rule_id`` firing on event ``event_id``.

    Never equal to any :func:`idempotency_key` (it hashes a 3-element array).
    """
    return _digest(
        ["firing", _require_part("rule_id", rule_id), _require_part("event_id", event_id)]
    )


def _guarded(doc: Document, may_reclaim: ReclaimGuard | None) -> ClaimReason | None:
    """``"held"`` when ``may_reclaim`` refuses taking over the lapsed claim ``doc``."""
    held = doc.get("status") == "claimed" and doc.get("holder") is not None
    if held and may_reclaim is not None and not may_reclaim(doc):
        return "held"
    return None


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of a claim or renewal.

    ``won`` is True iff the caller now holds the claim. ``reason`` says why:
    ``"acquired"`` (first claim, or after a release), ``"reclaimed"`` (a lapsed
    lease was taken over), ``"held"`` (someone holds a live lease) or
    ``"completed"`` (the work is already done - never redo it). ``holder``,
    ``attempt`` and ``lease_expires_at`` describe the claim as it now stands;
    ``document`` is the claim document (None only if it vanished).
    """

    won: bool
    key: str
    reason: ClaimReason
    holder: str | None
    attempt: int
    lease_expires_at: str | None
    document: Document | None

    @classmethod
    def _from(cls, won: bool, key: str, reason: ClaimReason, doc: Document | None) -> ClaimResult:
        doc = doc or {}
        return cls(
            won=won,
            key=key,
            reason=reason,
            holder=doc.get("holder"),
            attempt=int(doc.get("attempt") or 0),
            lease_expires_at=doc.get("lease_expires_at"),
            document=doc or None,
        )


class Claims:
    """One engine instance's view of the claims collection.

    ``holder`` names this instance (e.g. the host or engine id) and must be
    unique among concurrently running instances. ``store`` may be a
    StoragePort or a transaction handle.
    """

    def __init__(
        self,
        store: StoreOps,
        holder: str,
        *,
        lease: timedelta = DEFAULT_LEASE,
        clock: Clock | None = None,
        collection: str = CLAIMS_COLLECTION,
    ) -> None:
        self._store = store
        self.holder = _require_part("holder", holder)
        if not isinstance(lease, timedelta) or lease <= timedelta(0):
            raise ValueError("lease must be a positive timedelta")
        self.lease = lease
        self._clock = clock or (lambda: datetime.now(UTC))
        self.collection = _require_part("collection", collection)

    def with_ops(self, ops: StoreOps) -> Claims:
        """The same instance operating through ``ops`` (e.g. a transaction handle)."""
        return Claims(
            ops, self.holder, lease=self.lease, clock=self._clock, collection=self.collection
        )

    # ------------------------------------------------------------- claiming

    def claim_step(
        self, run_id: str, step_id: str, *, may_reclaim: ReclaimGuard | None = None
    ) -> ClaimResult:
        """Claim step ``step_id`` of run ``run_id`` (``may_reclaim``: see :meth:`claim`)."""
        return self.claim(
            idempotency_key(run_id, step_id),
            kind="step",
            subject={"run_id": run_id, "step_id": step_id},
            may_reclaim=may_reclaim,
        )

    def claim_firing(self, rule_id: str, event_id: str) -> ClaimResult:
        """Claim the firing of rule ``rule_id`` on event ``event_id``."""
        return self.claim(
            firing_key(rule_id, event_id),
            kind="firing",
            subject={"rule_id": rule_id, "event_id": event_id},
        )

    def claim(
        self,
        key: str,
        *,
        kind: ClaimKind,
        subject: Mapping[str, Any] | None = None,
        may_reclaim: ReclaimGuard | None = None,
    ) -> ClaimResult:
        """Claim the work identified by ``key``; exactly one racing caller wins.

        ``may_reclaim``, when given, is called with the claim document of a lapsed lease
        that is still held; returning False refuses the takeover (reason ``"held"``).
        """
        _require_part("key", key)
        if kind not in _KINDS:
            raise ValueError(f"kind must be one of {_KINDS}")
        now = self._clock()
        current = self._store.get(self.collection, key)
        if current is None:
            changes = {
                "key": key,
                "kind": kind,
                **dict(subject or {}),
                **self._holding(now, attempt=1, previous=None),
            }
            outcome = self._store.update_if(
                self.collection, key, {"attempt": None}, changes, upsert=True
            )
            if outcome.won:
                return ClaimResult._from(True, key, "acquired", outcome.document)
            return self._lost(key, outcome.document)

        refusal = self._refusal(current, now) or _guarded(current, may_reclaim)
        if refusal is not None:
            return ClaimResult._from(False, key, refusal, current)
        reason: ClaimReason = "reclaimed" if current.get("status") == "claimed" else "acquired"
        attempt = int(current.get("attempt") or 0) + 1
        outcome = self._store.update_if(
            self.collection,
            key,
            self._observed(current),
            self._holding(now, attempt=attempt, previous=current.get("holder")),
        )
        if outcome.won:
            return ClaimResult._from(True, key, reason, outcome.document)
        return self._lost(key, outcome.document)

    def renew(self, claim: ClaimResult) -> ClaimResult:
        """Extend the lease of a claim this instance still holds."""
        now = self._clock()
        outcome = self._store.update_if(
            self.collection,
            claim.key,
            self._fence(claim),
            {"lease_expires_at": utc_timestamp(now + self.lease)},
        )
        if outcome.won:
            return ClaimResult._from(True, claim.key, "acquired", outcome.document)
        return self._lost(claim.key, outcome.document)

    def complete(self, claim: ClaimResult, result: Any = None) -> bool:
        """Record the work as done; return False if this instance no longer holds it.

        A completed claim is final: it is never reclaimed or re-run.
        """
        changes: dict[str, Any] = {
            "status": "completed",
            "completed_at": utc_timestamp(self._clock()),
            "lease_expires_at": None,
        }
        if result is not None:
            changes["result"] = result
        return self._store.update_if(self.collection, claim.key, self._fence(claim), changes).won

    def release(self, claim: ClaimResult) -> bool:
        """Give up a held claim so another instance can take it immediately."""
        changes = {
            "status": "released",
            "holder": None,
            "previous_holder": claim.holder,
            "lease_expires_at": None,
        }
        return self._store.update_if(self.collection, claim.key, self._fence(claim), changes).won

    # -------------------------------------------------------------- queries

    def get(self, key: str) -> Document | None:
        """The claim document for ``key``, or None."""
        return self._store.get(self.collection, _require_part("key", key))

    def is_completed(self, key: str) -> bool:
        """Whether the work identified by ``key`` has a recorded completion."""
        doc = self.get(key)
        return doc is not None and doc.get("status") == "completed"

    # ------------------------------------------------------------ internals

    def _holding(self, now: datetime, *, attempt: int, previous: str | None) -> dict[str, Any]:
        return {
            "status": "claimed",
            "holder": self.holder,
            "previous_holder": previous,
            "attempt": attempt,
            "claimed_at": utc_timestamp(now),
            "lease_expires_at": utc_timestamp(now + self.lease),
        }

    @staticmethod
    def _observed(doc: Document) -> dict[str, Any]:
        """CAS expectation pinning the exact claim state the caller judged."""
        return {
            "attempt": doc.get("attempt"),
            "status": doc.get("status"),
            "holder": doc.get("holder"),
            "lease_expires_at": doc.get("lease_expires_at"),
        }

    def _fence(self, claim: ClaimResult) -> dict[str, Any]:
        if not claim.won or claim.holder != self.holder:
            # Never matches a stored claim: attempts start at 1.
            return {"attempt": -1}
        return {"attempt": claim.attempt, "holder": self.holder, "status": "claimed"}

    @staticmethod
    def _refusal(doc: Document, now: datetime) -> ClaimReason | None:
        status = doc.get("status")
        if status == "completed":
            return "completed"
        if status == "claimed" and doc.get("holder") is not None:
            expires = doc.get("lease_expires_at")
            # A claim without a lease (malformed) is treated as lapsed, never stuck.
            if expires is not None and now < datetime.fromisoformat(expires):
                return "held"
        return None

    @staticmethod
    def _lost(key: str, current: Document | None) -> ClaimResult:
        reason: ClaimReason = (
            "completed" if current is not None and current.get("status") == "completed" else "held"
        )
        return ClaimResult._from(False, key, reason, current)


RULE_ATTEMPT_BUDGETS = "rule_attempt_budgets"
"""Durable key reservations and admitted attempt counters, one per resolved key.

The key is **global** (deviation d13): one document per resolved concurrency key string,
whichever rule resolved it, id :func:`budget_id` (a digest of the key alone). Rules whose
templates resolve to the same key share one active run and one budget; a rule that wants
isolation uses a distinct template (a namespace prefix such as ``pr-fixer:``). Fields:
``key``; ``rule_id`` / ``run_id`` / ``intent_id`` of the rule and run holding the key;
``count`` (runs admitted since the last reset, by any rule sharing the key - every admitted
run counts, whatever its outcome); ``pending_event_id`` / ``pending_rule_id`` (the newest
firing deduplicated while the key was held, and the rule that recorded it: it is fired once,
through that rule, when the holding run ends, so the latest event is never lost); and
``revision`` (the compare-and-set token every write bumps). The counter resets only on an
explicit signal (:func:`reset_attempt_budget`, once per key and reset event): a human push
or green checks.
"""


def resolve_concurrency_key(template: str, envelope: Mapping[str, Any]) -> str:
    """Render dotted trigger paths without attribute access or executable expressions.

    Preserve literal separators (and escaped braces). Missing or non-scalar values
    fail closed: an invalid key must never silently disable concurrency protection.
    """
    from string import Formatter

    parts = []
    for literal, name, spec, conversion in Formatter().parse(template):
        parts.append(literal)
        if name is None:
            continue
        if spec or conversion or not name.startswith("trigger."):
            raise ValueError(f"invalid concurrency key placeholder: {name}")
        value: Any = {"trigger": envelope}
        for segment in name.split("."):
            if not isinstance(value, Mapping) or segment not in value:
                raise ValueError(f"missing concurrency key value: {name}")
            value = value[segment]
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"non-scalar concurrency key value: {name}")
        parts.append(str(value))
    return "".join(parts)


def budget_id(key: str) -> str:
    """The budget document id of the resolved concurrency ``key`` (global: no rule id).

    A sha256 over an unambiguous JSON encoding, in its own ``"concurrency"`` key space, so
    it never collides with a firing or step key and any key string fits a document id."""
    return _digest(["concurrency", key])


RESET_MARKERS = "event_fires"
"""Where :func:`reset_attempt_budget` notes each (key, reset event) it applied: the
exactly-once marker collection the trigger consumers already write
(:data:`culture_rules.events.triggers.FIRES_COLLECTION`)."""


def reset_marker_id(key: str, event_id: str) -> str:
    """The marker id noting that reset event ``event_id`` was applied to ``key``."""
    return f"budget-reset/{budget_id(key)}/{event_id}"


def reset_attempt_budget(store: StoreOps, key: str, event_id: str) -> None:
    """Reset ``key``'s counter on the reset event ``event_id`` (human push or green checks),
    preserving a reservation - at most once per (key, event), whatever the order.

    Called once per key in the event's exactly-once transaction, whichever and however many
    rules share the key, including when the event does not match their triggers or the key
    has an active run. Placed and shared rules sharing a key reach it through different
    consumers that may progress at different speeds, so the (key, event) pair is noted with
    a marker document (:func:`reset_marker_id`) inserted in the same transaction: a second
    consumer handling the same event - even after the first has handled newer resets and
    admitted another attempt - finds the marker and resets nothing. The marker is written
    even when the key has no budget yet (nothing to reset), so an attempt admitted later is
    not erased by a lagging consumer either. A per-(key, event) marker rather than a
    watermark on the budget: the consumers share no comparable order (feed tokens are
    opaque and envelope times may tie or skew), and the budget document stays bounded (the
    markers grow like the per-event fire markers beside them).
    """
    marker_id = reset_marker_id(key, event_id)
    if store.get(RESET_MARKERS, marker_id) is not None:
        return
    # Two consumers racing on one event both insert: one write conflicts and its retry
    # sees the marker (the same serialisation as the trigger fire markers).
    store.insert(
        RESET_MARKERS,
        {"id": marker_id, "kind": "budget-reset", "key": key, "event_id": event_id},
    )
    _cas_budget(store, budget_id(key), lambda _current: {"count": 0}, "attempt budget reset")


def reserve_concurrency(
    store: StoreOps,
    rule_id: str,
    key: str,
    run_id: str,
    intent_id: str,
    max_attempts: int | None,
) -> str | None:
    """Atomically reserve a key and consume one attempt, or return a skip reason.

    The reservation commits with the firing intent, before starting the run. A
    crashed node's pending intent therefore retains the key until another node
    starts it. Read the actual run's terminal status on admission: cancellation,
    supersession and completion need no fallible secondary completion callback.
    CAS losers re-read *all* admission facts, never just increment the counter.
    """
    from culture_rules.engine.runs import RUN_DONE, RUNS_COLLECTION

    doc_id = budget_id(key)
    for _ in range(DEFAULT_ATTEMPTS):
        current = store.get(RULE_ATTEMPT_BUDGETS, doc_id) or {}
        count = current.get("count", 0)
        active = current.get("run_id")
        if active:
            run = store.get(RUNS_COLLECTION, active)
            if run is None:
                intent = store.get("rule_fires", current["intent_id"])
                if not intent or intent.get("status") != "failed":
                    return "deduplicated"
                # A failed start never produced a run.
                count = max(0, count - 1)
            elif run.get("status") not in RUN_DONE:
                return "deduplicated"
        if max_attempts is not None and count >= max_attempts:
            return "attempt_budget_exhausted"
        revision = current.get("revision")
        outcome = store.update_if(
            RULE_ATTEMPT_BUDGETS,
            doc_id,
            {"revision": revision},
            {
                "rule_id": rule_id,
                "key": key,
                "count": count + 1,
                "run_id": run_id,
                "intent_id": intent_id,
                "pending_event_id": None,
                "pending_rule_id": None,
                "revision": (revision or 0) + 1,
            },
            upsert=not current,
        )
        if outcome.won:
            return None

    raise TransientStoreError("concurrency reservation contention")


def _cas_budget(
    store: StoreOps,
    doc_id: str,
    change: Callable[[Document], dict[str, Any] | None],
    what: str,
) -> Document | None:
    """Apply ``change`` to a budget document under its revision (bounded retries).

    ``change`` answers the fields to write, or ``None`` to leave the document alone; the
    answer is ``None`` then, else the written document.
    """
    for _ in range(DEFAULT_ATTEMPTS):
        current = store.get(RULE_ATTEMPT_BUDGETS, doc_id)
        if current is None:
            return None
        fields = change(current)
        if fields is None:
            return None
        revision = current.get("revision")
        outcome = store.update_if(
            RULE_ATTEMPT_BUDGETS,
            doc_id,
            {"revision": revision},
            {**fields, "revision": (revision or 0) + 1},
        )
        if outcome.won:
            return outcome.document or {**current, **fields}
    raise TransientStoreError(f"{what} contention")


def note_deduplicated(store: StoreOps, rule_id: str, key: str, event_id: str) -> str | None:
    """Remember ``event_id`` (recorded by ``rule_id``) as the key's newest deduplicated
    firing; answer the holding run.

    Called in the trigger transaction right after :func:`reserve_concurrency` answered
    ``"deduplicated"``. A newer deduplicated event - from any rule sharing the key -
    replaces an older one (coalescing: the holding run's successor handles the latest
    state, never a stale one).
    """
    doc = _cas_budget(
        store,
        budget_id(key),
        lambda _current: {"pending_event_id": event_id, "pending_rule_id": rule_id},
        "deduplicated-event note",
    )
    return None if doc is None else doc.get("run_id")


def release_concurrency(store: StoreOps, doc_id: str, run_id: str) -> tuple[str, str] | None:
    """The holding run ``run_id`` ended: clear and answer the key's pending event, if any,
    as ``(event_id, rule_id)`` - the rule that recorded it fires it.

    Always writes the budget document while ``run_id`` still holds the key, even with no
    pending event: a concurrent trigger transaction that read the run as active and is
    about to note a deduplicated event then write-conflicts with this one instead of
    committing an event nobody would fire (MongoDB snapshot isolation, write skew).
    """
    taken: list[tuple[str, str]] = []

    def change(current: Document) -> dict[str, Any] | None:
        taken.clear()
        if current.get("run_id") != run_id:
            return None  # another run already holds the key: it cleared the pending event
        event, rule = current.get("pending_event_id"), current.get("pending_rule_id")
        if event and rule:
            taken.append((event, rule))
        return {"pending_event_id": None, "pending_rule_id": None}

    _cas_budget(store, doc_id, change, "concurrency release")
    return taken[0] if taken else None
