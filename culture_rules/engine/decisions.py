"""A rule's contextual history: the skips that matter, persisted as decision records.

Matching (:mod:`culture_rules.engine.matching`) decides fire-or-skip per (rule, event). A
fire leaves a run; a skip used to live only in memory and in replay reports. The node now
records the skips an operator needs to see ("superseded by A", "lost exclusive group g to
B", "waiting for predecessor X") in the ``rule_decisions`` collection:

* one document per (rule, event), id :func:`decision_key`, so a redelivered event is
  idempotent (the record is written once, never duplicated);
* written in the same trigger transaction as the firing intents, so it commits or rolls
  back with them;
* ttl-free and uncapped: it grows by one small record per recorded skip.

Waiting is not final. A ``blocked_by_predecessor`` record ("waiting for predecessor X") is
**superseded** by the eventual outcome once the predecessor's run for the event finishes
(:func:`settle_decision`): the record keeps its id and takes the new reason - ``matched``
with ``fire: true`` and the ``run_id`` when the rule then fired ("ran after X"),
``predecessor_failed`` when X did not succeed, or whatever else the re-evaluation decided -
and the waiting state moves to its ``superseded`` list, so the rule's history shows both
what it waited for and how that ended. Every other recorded reason is final and is never
rewritten. A fired record is not a skip: :func:`decisions_for` leaves it out with
``skips_only`` (the run itself is the history entry).

``condition_false``, ``disabled`` and ``paused`` are not recorded: they are the normal
"this rule did not apply" outcome and would flood the history. Standard-library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from culture_rules.engine.claims import firing_key
from culture_rules.engine.matching import (
    BLOCKED_BY_PREDECESSOR,
    FIRE,
    GROUP_LOST,
    PAUSED,
    PREDECESSOR_FAILED,
    SUPERSEDED_BY,
    Decision,
)
from culture_rules.store.port import StoreOps

__all__ = [
    "RECORDED_REASONS",
    "RULE_DECISIONS",
    "decision_key",
    "decisions_for",
    "record_decision",
    "settle_decision",
]

RULE_DECISIONS = "rule_decisions"
"""Persisted skip decisions: one per (rule, event) whose skip reason is recorded."""
RECORDED_REASONS: tuple[str, ...] = (
    SUPERSEDED_BY,
    BLOCKED_BY_PREDECESSOR,
    GROUP_LOST,
    PREDECESSOR_FAILED,
)


def decision_key(rule_id: str, event_id: str) -> str:
    """The id of the decision record of ``rule_id`` on ``event_id`` (same on every host)."""
    return firing_key(rule_id, event_id)


def _record(
    decision: Decision, *, event_id: str, host: str, at: str, run_id: str | None = None
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": decision_key(decision.rule_id, event_id),
        "rule_id": decision.rule_id,
        "event_id": event_id,
        "fire": decision.fire,
        "reason": decision.reason,
        "by": list(decision.by),
        "detail": decision.detail,
        "message": decision.message,
        "at": at,
        "host": host,
    }
    if decision.fire:
        doc["run_id"] = run_id
        doc["message"] = f"ran after {', '.join(decision.by)}" if decision.by else "matched"
    return doc


def record_decision(
    tx: StoreOps, decision: Decision, *, event_id: str, host: str, at: str
) -> Mapping[str, Any] | None:
    """Persist ``decision`` when it is a recorded skip; answer the record (or ``None``).

    An existing record for the same (rule, event) is left as it is (redelivery).
    """
    if decision.fire or decision.reason not in RECORDED_REASONS:
        return None
    key = decision_key(decision.rule_id, event_id)
    existing = tx.get(RULE_DECISIONS, key)
    if existing is not None:
        return existing
    return tx.insert(RULE_DECISIONS, _record(decision, event_id=event_id, host=host, at=at))


def settle_decision(
    tx: StoreOps,
    decision: Decision,
    *,
    event_id: str,
    host: str,
    at: str,
    run_id: str | None = None,
    always: bool = False,
) -> Mapping[str, Any] | None:
    """Record ``decision``, superseding a waiting (``blocked_by_predecessor``) record.

    * no record yet: a recorded skip is inserted (as :func:`record_decision` does); a
      fire or an unrecorded skip is inserted only with ``always`` - the node passes it
      when re-evaluating a dependant after its predecessor finished, so that evaluation
      always writes this (rule, event) record and conflicts with a concurrent first
      evaluation of the same rule instead of racing past it;
    * a waiting record and a still-waiting decision: the record stays (its ``by`` is
      refreshed when the set of awaited predecessors changed);
    * a waiting record and any other decision: the record takes the new outcome and the
      waiting state is appended to its ``superseded`` list. A fire names the
      predecessors it waited for (``by``) and its ``run_id``. A ``paused`` decision is
      not an outcome and leaves a waiting record as it is (the node defers a chain
      re-evaluation during a pause, :mod:`culture_rules.node.firing`, "Pause");
    * a final record: left as it is (redelivery).
    """
    key = decision_key(decision.rule_id, event_id)
    existing = tx.get(RULE_DECISIONS, key)
    waiting = not decision.fire and decision.reason == BLOCKED_BY_PREDECESSOR
    if existing is None:
        if not always and (decision.fire or decision.reason not in RECORDED_REASONS):
            return None
        if decision.fire:
            decision = replace(decision, by=tuple(decision.upstream))
        return tx.insert(
            RULE_DECISIONS,
            _record(decision, event_id=event_id, host=host, at=at, run_id=run_id),
        )
    if existing.get("reason") != BLOCKED_BY_PREDECESSOR or decision.reason == PAUSED:
        return existing
    if waiting:
        return _refresh_waiting(tx, key, decision, existing)
    if decision.fire:  # it waited for these and then ran
        decision = replace(decision, by=tuple(existing.get("by") or ()))
    prior = {k: existing.get(k) for k in ("reason", "by", "detail", "message", "at", "host")}
    new = _record(decision, event_id=event_id, host=host, at=at, run_id=run_id)
    new["superseded"] = [*(existing.get("superseded") or ()), prior]
    changes = {k: v for k, v in new.items() if k != "id"}
    return tx.update_if(RULE_DECISIONS, key, {"reason": BLOCKED_BY_PREDECESSOR}, changes).document


def _refresh_waiting(
    tx: StoreOps, key: str, decision: Decision, existing: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """A still-waiting decision: refresh the record's ``by`` only when it changed."""
    if list(decision.by) == list(existing.get("by") or ()):
        return existing
    changes = {"by": list(decision.by), "message": decision.message}
    return tx.update_if(RULE_DECISIONS, key, {"reason": BLOCKED_BY_PREDECESSOR}, changes).document


def decisions_for(
    store: StoreOps, rule_id: str, *, skips_only: bool = False
) -> list[dict[str, Any]]:
    """Every decision record of ``rule_id``, newest first (``skips_only``: not the fired
    ones, whose history entry is their run)."""
    docs = [
        d
        for d in store.find(RULE_DECISIONS, {"rule_id": rule_id})
        if not (skips_only and (d.get("fire") or d.get("reason") == FIRE))
    ]
    return sorted((dict(d) for d in docs), key=lambda d: d.get("at") or "", reverse=True)
