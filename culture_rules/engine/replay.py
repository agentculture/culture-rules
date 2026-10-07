"""Replay: run recorded event envelopes through rule matching with no side effects.

Replay answers "what *would* this ruleset have done with these events?". It feeds each
recorded envelope through :func:`culture_rules.engine.matching.match` (pure) and reports,
per event, which rules would fire and why the others would not, using the same reason
codes as live matching.

It never executes anything: there is no actor, no executor and no store write in this
module. The ``events`` collection (written by ``culture_rules.events.ingest``) is only
read, and ``actions_executed`` in the report is always 0. ``must_after`` predecessors have
no recorded runs during replay, so a rule with one is reported as blocked unless the caller
supplies ``facts_for(envelope)`` with hypothetical outcomes.

Shared variables (``vars.<name>`` conditions, ``{"$var": name}`` inputs) are evaluated as
live matching evaluates them: replaying a store's ``events`` reads the *current* value of
each variable the rules reference from that store (not its value when the event arrived);
an envelope list has no store, so the caller passes ``variables``. A referenced variable
that is not defined is reported ``variable_undefined``, as live.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from culture_rules.engine.matching import Decision, RunFacts, match
from culture_rules.engine.variables import variable_values
from culture_rules.model.rule import Rule
from culture_rules.model.variable_refs import rule_variable_refs
from culture_rules.model.workflow import Workflow
from culture_rules.store.port import StoreOps

__all__ = ["ReplayError", "ReplayReport", "ReplayedDecision", "replay"]

_EVENTS = "events"  # culture_rules.events.ingest.EVENTS_COLLECTION, kept import-free


class ReplayError(ValueError):
    """Replay input was unusable (bad envelope, bad limit, unknown rule id)."""


@dataclass(frozen=True, kw_only=True)
class ReplayedDecision:
    """One rule's decision for one recorded event."""

    event_id: str
    rule_id: str
    fire: bool
    reason: str
    message: str
    by: tuple[str, ...] = ()
    upstream: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "rule_id": self.rule_id,
            "fire": self.fire,
            "reason": self.reason,
            "message": self.message,
            "by": list(self.by),
            "upstream": {k: dict(v) for k, v in self.upstream.items()},
        }


@dataclass(frozen=True, kw_only=True)
class ReplayReport:
    """Outcome of a replay: would-fire runs, skips with reasons, and unmatched events."""

    events: int
    would_fire: tuple[ReplayedDecision, ...]
    skipped: tuple[ReplayedDecision, ...]
    unmatched: tuple[str, ...]
    actions_executed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": self.events,
            "would_fire_count": len(self.would_fire),
            "would_fire": [d.to_dict() for d in self.would_fire],
            "skipped": [d.to_dict() for d in self.skipped],
            "unmatched": list(self.unmatched),
            "actions_executed": self.actions_executed,
        }


def _load(source: StoreOps | Iterable[Mapping[str, Any]], limit: int | None) -> list[Mapping]:
    if hasattr(source, "find"):
        docs = source.find(_EVENTS, {})
        docs = sorted(docs, key=lambda d: (str(d.get("received_at", "")), str(d.get("id", ""))))
        envelopes = [d["envelope"] for d in docs if isinstance(d.get("envelope"), Mapping)]
    else:
        envelopes = list(source)
    return envelopes if limit is None else envelopes[:limit]


def _variables(source: Any, snapshot: list[Rule]) -> dict[str, Any]:
    """The store's current values of the variables ``snapshot`` references (live's read)."""
    if not hasattr(source, "get"):
        return {}
    wanted = set().union(*(rule_variable_refs(r) for r in snapshot)) if snapshot else set()
    return variable_values(source, wanted) if wanted else {}


def _event_id(envelope: Mapping[str, Any]) -> str:
    eid = envelope.get("id")
    if not isinstance(eid, str) or not eid:
        raise ReplayError("recorded envelope has no string id")
    return eid


def _decide(
    envelope: Mapping[str, Any],
    snapshot: list[Rule],
    workflows: Mapping[str, Workflow] | None,
    paused: bool,
    facts_for: Callable[[Mapping[str, Any]], RunFacts] | None,
    rule_id: str | None,
    variables: Mapping[str, Any],
) -> tuple[Decision, ...]:
    """Match one envelope against the whole snapshot; only ``rule_id``'s decision if given."""
    facts = facts_for(envelope) if facts_for else None
    decisions = match(
        envelope, snapshot, facts, workflows=workflows, paused=paused, variables=variables
    )
    if rule_id is not None:
        decisions = tuple(d for d in decisions if d.rule_id == rule_id)
    return decisions


def _replayed(eid: str, d: Decision) -> ReplayedDecision:
    return ReplayedDecision(
        event_id=eid,
        rule_id=d.rule_id,
        fire=d.fire,
        reason=d.reason,
        message=d.message,
        by=d.by,
        upstream=d.upstream,
    )


def replay(
    source: StoreOps | Iterable[Mapping[str, Any]],
    rules: Iterable[Rule],
    *,
    workflows: Mapping[str, Workflow] | None = None,
    rule_id: str | None = None,
    limit: int | None = None,
    paused: bool = False,
    facts_for: Callable[[Mapping[str, Any]], RunFacts] | None = None,
    variables: Mapping[str, Any] | None = None,
) -> ReplayReport:
    """Replay recorded envelopes (a list, or a store's ``events`` collection) through matching.

    With ``rule_id`` only that rule's decisions are reported, but matching still sees the
    whole snapshot so supersede and exclusive-group effects are faithful. ``variables``
    (name -> value) defaults to the store's current values of the variables the rules
    reference (none for an envelope list). Pure and read-only: nothing is executed and
    nothing is written.
    """
    if limit is not None and limit < 1:
        raise ReplayError("limit must be at least 1")
    snapshot = list(rules)
    if rule_id is not None and rule_id not in {r.id for r in snapshot}:
        raise ReplayError(f"unknown rule {rule_id!r}")
    envelopes = _load(source, limit)
    if variables is None:
        variables = _variables(source, snapshot)
    fire: list[ReplayedDecision] = []
    skipped: list[ReplayedDecision] = []
    unmatched: list[str] = []
    for envelope in envelopes:
        eid = _event_id(envelope)
        decisions = _decide(envelope, snapshot, workflows, paused, facts_for, rule_id, variables)
        if not decisions:
            unmatched.append(eid)
            continue
        for d in decisions:
            (fire if d.fire else skipped).append(_replayed(eid, d))
    return ReplayReport(
        events=len(envelopes),
        would_fire=tuple(fire),
        skipped=tuple(skipped),
        unmatched=tuple(unmatched),
    )
