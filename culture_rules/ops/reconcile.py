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
   pending event cleared, its attempt count kept - and logged.
3. **Unfinished chain work** - a finished run whose must/may-run-after dependants had not
   been decided for its event when the backup was taken (no firing intent, and no final
   decision record: none, or one still waiting) would never be handled, because the chain
   consumers start after the restore at the feed's head. The chain consumers' cursors on
   ``runs`` are pinned first, then each such run is touched (``redriven_at``), so the first
   node's chain consumers handle its end once. Deterministic ids keep it idempotent: a
   dependant that fired keeps its intent, and a second reconciliation finds nothing to do.

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
from culture_rules.node.firing import RULE_FIRES, SHARED_CHAIN, placed_chain, run_id_for
from culture_rules.store.port import init_cursor
from culture_rules.store.versioning import utc_timestamp

__all__ = ["ReconcileReport", "reconcile_restored"]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconcileReport:
    """What one reconciliation repaired."""

    reopened: int = 0
    reservations_dropped: int = 0
    chains_redriven: int = 0


def reconcile_restored(store: Any, *, now: datetime | None = None) -> ReconcileReport:
    """Repair the restored ``store`` (module doc); run it before any node starts."""
    now = now or datetime.now(UTC)
    reopened = reopen_undelivered(store)
    dropped = _drop_orphan_reservations(store)
    redriven = _redrive_chains(store, now)
    report = ReconcileReport(reopened, dropped, redriven)
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


def _drop_orphan_reservations(store: Any) -> int:
    dropped = 0
    for budget in store.find(RULE_ATTEMPT_BUDGETS):
        if not budget.get("run_id") or _holder_live(store, budget):
            continue
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
                "revision": (budget.get("revision") or 0) + 1,
            },
        )
        if moved.won:
            dropped += 1
            log.warning(
                "restore: dropped the reservation of key %r held by %s (intent %s): no "
                "pending intent and no running run hold it; pending event %s not fired",
                budget.get("key"),
                budget.get("run_id"),
                budget.get("intent_id"),
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


def _redrive_chains(store: Any, now: datetime) -> int:
    rules = live_rules(store.find("rules"))
    dependants: dict[str, list[str]] = {}
    for r in rules:
        for pid in (*r.must_after, *r.may_after):
            dependants.setdefault(pid, []).append(r.id)
    if not dependants:
        return 0
    # pin the chain consumers first, so the touches below are after their cursors
    for consumer in (SHARED_CHAIN, *(placed_chain(m.name) for m in enrolled_machines(store))):
        init_cursor(store, consumer, RUNS_COLLECTION)
    redriven = 0
    for pid, deps in sorted(dependants.items()):
        for run in store.find(RUNS_COLLECTION, {"rule_id": pid}):
            if run.get("status") not in RUN_DONE:
                continue
            event_id = (run.get("trigger") or {}).get("id")
            if not event_id or run.get("id") != run_id_for(pid, event_id):
                continue  # started by hand: no chain
            undecided = [
                d
                for d in deps
                if store.get(RULE_FIRES, firing_key(d, event_id)) is None
                and not _final(store.get(RULE_DECISIONS, decision_key(d, event_id)))
            ]
            if not undecided:
                continue
            moved = store.update_if(
                RUNS_COLLECTION,
                run["id"],
                {"rev": run.get("rev")},
                {"redriven_at": utc_timestamp(now)},
            )
            if moved.won:
                redriven += 1
                log.info(
                    "restore: re-driving the end of run %s for %s", run["id"], ", ".join(undecided)
                )
    return redriven
