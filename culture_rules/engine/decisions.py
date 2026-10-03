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

``condition_false``, ``disabled`` and ``paused`` are not recorded: they are the normal
"this rule did not apply" outcome and would flood the history. Standard-library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from culture_rules.engine.claims import firing_key
from culture_rules.engine.matching import (
    BLOCKED_BY_PREDECESSOR,
    GROUP_LOST,
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
]

RULE_DECISIONS = "rule_decisions"
"""Persisted skip decisions: one per (rule, event) whose skip reason is recorded."""
RECORDED_REASONS: tuple[str, ...] = (SUPERSEDED_BY, BLOCKED_BY_PREDECESSOR, GROUP_LOST)


def decision_key(rule_id: str, event_id: str) -> str:
    """The id of the decision record of ``rule_id`` on ``event_id`` (same on every host)."""
    return firing_key(rule_id, event_id)


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
    return tx.insert(
        RULE_DECISIONS,
        {
            "id": key,
            "rule_id": decision.rule_id,
            "event_id": event_id,
            "reason": decision.reason,
            "by": list(decision.by),
            "detail": decision.detail,
            "message": decision.message,
            "at": at,
            "host": host,
        },
    )


def decisions_for(store: StoreOps, rule_id: str) -> list[dict[str, Any]]:
    """Every decision record of ``rule_id``, newest first."""
    docs = store.find(RULE_DECISIONS, {"rule_id": rule_id})
    return sorted((dict(d) for d in docs), key=lambda d: d.get("at") or "", reverse=True)
