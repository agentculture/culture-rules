"""Restore-time reconciliation: repair the known gaps of a per-collection backup (d21).

A backup (:mod:`culture_rules.ops.backup`) is consistent per collection, not one point in
time across collections, and it leaves out the ``events`` collection and every consumer's
cursor and fire markers. :func:`reconcile_restored` runs once, on the restored store, before
any node starts, and repairs exactly these gaps:

1. **Undelivered run events** - every completion marked emitted whose event is missing is
   re-opened for delivery under its assigned id
   (:func:`~culture_rules.engine.run_completions.reopen_undelivered`).
2. **Orphan key reservations** - a concurrency reservation (``rule_attempt_budgets``) whose
   holder is neither a pending firing intent nor a run still running would hold its key
   forever (``deduplicated`` for every later firing). It is dropped - its holder, intent and
   pending event cleared - and logged. Its counted attempt is refunded only when its intent
   explicitly failed and no run exists (as the next reservation would have done); a
   finished run or an ambiguous orphan keeps it.
3. **Unfinished chain work** - a chain continues from three kinds of document: a finished
   run, a final skip decision and a failed firing intent. One whose rule has a must/may-run-
   after dependant undecided for its event (no firing intent, and no final decision record:
   none, or one still waiting) would never be handled, because the chain consumers start
   after the restore. Every chain consumer's cursor on all three sources is pinned **first**,
   then each such document is touched (``redriven_at``), so the first node's chain consumers
   handle it once - and what that handling writes (a dependant's final skip) is after the
   cursors too, so it propagates further down the chain. A decision continues from the
   trigger snapshot it carries (backups omit ``events``); one written by an older build
   without it is reported (``needs_review``), never guessed at. Deterministic ids keep this
   idempotent: a dependant that fired keeps its intent, and a second reconciliation finds
   nothing to do.

Anything else a per-collection backup can tear needs an operator's review
(``docs/operations/backup.md``, "Restore limits"). Standard-library only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, firing_key
from culture_rules.engine.decisions import RULE_DECISIONS, decision_key
from culture_rules.engine.matching import BLOCKED_BY_PREDECESSOR, DEDUPLICATED
from culture_rules.engine.run_completions import reopen_undelivered
from culture_rules.engine.runs import RUN_DONE, RUNS_COLLECTION
from culture_rules.machines.enrol import enrolled_machines
from culture_rules.node.chain import live_rules
from culture_rules.node.firing import (
    RULE_FIRES,
    SHARED_CHAIN,
    _failed_intent,
    _finished_run,
    _recover_trigger,
    _settled_skip,
    placed_chain,
)
from culture_rules.store.port import init_cursor
from culture_rules.store.versioning import utc_timestamp

__all__ = ["ReconcileReport", "reconcile_restored"]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconcileReport:
    """What one reconciliation repaired (and what it left for an operator)."""

    reopened: int = 0
    reservations_dropped: int = 0
    chains_redriven: int = 0
    needs_review: int = 0
    """Final decisions whose dependants are undecided but which carry no trigger snapshot
    (written by an older build): never guessed at, logged for an operator."""


CHAIN_SOURCES = (RUNS_COLLECTION, RULE_DECISIONS, RULE_FIRES)
"""The collections every chain consumer watches (:mod:`culture_rules.node.firing`)."""


def reconcile_restored(store: Any, *, now: datetime | None = None) -> ReconcileReport:
    """Repair the restored ``store`` (module doc); run it before any node starts.

    Every chain consumer's cursor on every chain source is pinned **first**, before any
    reconciliation write, so each write below - and each write a node makes while handling
    it (a dependant's final skip, say) - is after the cursors and is seen once."""
    now = now or datetime.now(UTC)
    for consumer in (SHARED_CHAIN, *(placed_chain(m.name) for m in enrolled_machines(store))):
        for collection in CHAIN_SOURCES:
            init_cursor(store, consumer, collection)
    reopened = reopen_undelivered(store)
    dropped = _drop_orphan_reservations(store)
    redriven, review = _redrive_chains(store, now)
    report = ReconcileReport(reopened, dropped, redriven, review)
    log.info("restore reconciled: %s", report)
    return report


def _holder_live(store: Any, budget: dict[str, Any]) -> bool:
    intent = (
        store.get(RULE_FIRES, budget.get("intent_id") or "") if budget.get("intent_id") else None
    )
    if intent is not None and intent.get("status") == "pending":
        return True
    run = store.get(RUNS_COLLECTION, budget.get("run_id") or "")
    return run is not None and run.get("status") not in RUN_DONE


def _refund(store: Any, budget: dict[str, Any]) -> bool:
    """Whether the dropped holder's attempt goes back, as :func:`reserve_concurrency`
    would have given it: its intent explicitly failed, no run exists, and it was counted.
    A finished run, or an orphan with no evidence either way, keeps its attempt."""
    if budget.get("counted") is False or not budget.get("intent_id"):
        return False
    intent = store.get(RULE_FIRES, budget["intent_id"])
    if intent is None or intent.get("status") != "failed":
        return False
    return store.get(RUNS_COLLECTION, budget.get("run_id") or "") is None


def _drop_orphan_reservations(store: Any) -> int:
    dropped = 0
    for budget in store.find(RULE_ATTEMPT_BUDGETS):
        if not budget.get("run_id") or _holder_live(store, budget):
            continue
        refund = _refund(store, budget)
        count = budget.get("count") or 0
        moved = store.update_if(
            RULE_ATTEMPT_BUDGETS,
            budget["id"],
            {"revision": budget.get("revision")},
            {
                "rule_id": None,
                "run_id": None,
                "intent_id": None,
                "pending_event_id": None,
                "pending_rule_id": None,
                "count": max(0, count - 1) if refund else count,
                "revision": (budget.get("revision") or 0) + 1,
            },
        )
        if moved.won:
            dropped += 1
            log.warning(
                "restore: dropped the reservation of key %r held by %s (intent %s): no "
                "pending intent and no running run hold it; %s; pending event %s not fired",
                budget.get("key"),
                budget.get("run_id"),
                budget.get("intent_id"),
                "its failed start's attempt refunded" if refund else "its attempt kept",
                budget.get("pending_event_id"),
            )
    return dropped


def _final(record: dict[str, Any] | None) -> bool:
    """Whether a decision record settles its (rule, event) for good."""
    if record is None:
        return False
    reason = record.get("reason")
    if reason == BLOCKED_BY_PREDECESSOR:
        return False
    return not (reason == DEDUPLICATED and not record.get("coalesced"))


def _undecided(store: Any, deps: list[str], event_id: str) -> list[str]:
    return [
        d
        for d in deps
        if store.get(RULE_FIRES, firing_key(d, event_id)) is None
        and not _final(store.get(RULE_DECISIONS, decision_key(d, event_id)))
    ]


def _redrive_chains(store: Any, now: datetime) -> tuple[int, int]:
    """Touch every chain source document a chain consumer would continue from - a finished
    event-fired run, a final skip decision, a failed firing intent - whose rule has a
    must/may-run-after dependant undecided for that event. Answer (touched, for review)."""
    rules = live_rules(store.find("rules"))
    dependants: dict[str, list[str]] = {}
    for r in rules:
        for pid in (*r.must_after, *r.may_after):
            dependants.setdefault(pid, []).append(r.id)
    redriven = review = 0
    stamp = utc_timestamp(now)
    for pid, deps in sorted(dependants.items()):
        for run in store.find(RUNS_COLLECTION, {"rule_id": pid}):
            if _finished_run(run) is None:
                continue
            event_id = run["trigger"]["id"]
            if (
                _undecided(store, deps, event_id)
                and store.update_if(
                    RUNS_COLLECTION, run["id"], {"rev": run.get("rev")}, {"redriven_at": stamp}
                ).won
            ):
                redriven += 1
                log.info("restore: re-driving the end of run %s", run["id"])
        for record in store.find(RULE_DECISIONS, {"rule_id": pid}):
            if _settled_skip(record) is None or not _undecided(store, deps, record["event_id"]):
                continue
            if not isinstance(record.get("trigger"), dict) and not _recover_trigger(
                store, rules, pid, record["event_id"]
            ):
                review += 1
                log.warning(
                    "restore: decision %s (%s) has undecided dependants, no trigger snapshot "
                    "and no run or intent holding it: left for an operator, not guessed",
                    record["id"],
                    record.get("reason"),
                )
                continue
            if store.update_if(
                RULE_DECISIONS, record["id"], {"reason": record["reason"]}, {"redriven_at": stamp}
            ).won:
                redriven += 1
                log.info("restore: re-driving decision %s", record["id"])
        for intent in store.find(RULE_FIRES, {"rule_id": pid, "status": "failed"}):
            if _failed_intent(intent) is None or not _undecided(store, deps, intent["event_id"]):
                continue
            if store.update_if(
                RULE_FIRES, intent["id"], {"status": "failed"}, {"redriven_at": stamp}
            ).won:
                redriven += 1
                log.info("restore: re-driving failed intent %s", intent["id"])
    return redriven, review
