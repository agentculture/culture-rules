"""Sequencing: turn one event's matching decisions into "fire now", "wait" or "never".

:func:`~culture_rules.engine.matching.match` is pure and answers per rule, given the run
facts *at that moment*. A node evaluating an event also has to know whether a predecessor
that has not finished yet *will* still run for this event, so a dependant can wait for it
instead of being decided too early. :func:`sequence` refines the matching decisions with
the predecessors' run states for the same event:

``must_after`` (must run after)
    The dependant fires only once every ``must_after`` predecessor's run for this event
    has **succeeded** (matching already blocks it otherwise). While a blocking predecessor
    is still live - its run is active, its firing intent is pending, or it fires or waits
    itself in this evaluation - the dependant stays ``blocked_by_predecessor`` (waiting).
    Once a blocking predecessor can no longer succeed for this event - its run failed or
    was cancelled, its run could not start, or it did not fire at all (did not match,
    condition false, superseded, lost its group, or was itself skipped) - the dependant
    gets the final skip ``predecessor_failed``, with ``by`` naming those predecessors and
    ``detail`` saying what happened to each.

``may_after`` (may run after)
    Matching never blocks on it, and makes the predecessor's exported outputs visible
    only when its run succeeded. Sequencing keeps both and adds *ordering*: when a
    ``may_after`` predecessor is live for this event (it fired or waits, or its run is
    active), the dependant waits (``blocked_by_predecessor``) and fires once that run is
    finished, **whatever its outcome**: after a success it sees the exported outputs,
    after a failure or cancellation it fires without them. When the predecessor did not
    fire for this event (did not match, condition false, superseded, ...), the dependant
    fires right away, as matching decided.

Every other decision (fire without predecessors, condition_false, superseded_by,
group_lost, paused, disabled) passes through unchanged, so supersession and exclusive
groups compose with both relationships without changing them (h78).

``may_after`` cycles are allowed at save time (the editor permits them). Sequencing
breaks one deterministically: while a rule's own decision is being worked out, a
``may_after`` edge back to it counts as not live, so on a cycle the rule that comes first
in the decision order waits for the other. Pure: no store, no clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from culture_rules.engine.matching import BLOCKED_BY_PREDECESSOR, PREDECESSOR_FAILED, Decision
from culture_rules.model.rule import Rule

__all__ = ["LIVE", "START_FAILED", "sequence"]

LIVE = "running"
"""Predecessor state: its run is active, or its firing intent is still pending."""
START_FAILED = "start_failed"
"""Predecessor state: it fired, but its run could not be started."""

_WORDS = {
    "failed": "failed",
    "cancelled": "was cancelled",
    "succeeded": "succeeded",
    START_FAILED: "could not start",
}


def sequence(
    decisions: Iterable[Decision],
    rules: Iterable[Rule],
    states: Mapping[str, str],
) -> tuple[Decision, ...]:
    """Refine ``decisions`` (one event's :func:`match` output) with predecessor states.

    ``states`` maps a rule id to the state of its run for this event: a run status
    (``running`` / ``succeeded`` / ``failed`` / ``cancelled``) or :data:`START_FAILED`;
    a rule without a run or intent for the event is absent. Returns the decisions in the
    same order (see the module docstring for the rules).
    """
    decisions = tuple(decisions)
    seq = _Sequencer(decisions, rules, states)
    return tuple(seq.adjust(d.rule_id) or d for d in decisions)


class _Sequencer:
    """One :func:`sequence` call's memoised walk over the predecessor relationships."""

    def __init__(
        self, decisions: tuple[Decision, ...], rules: Iterable[Rule], states: Mapping[str, str]
    ) -> None:
        self.states = states
        self.by_id = {d.rule_id: d for d in decisions}
        self.snapshot = {r.id: r for r in rules}
        self.memo: dict[str, Decision | None] = {}
        self.active: set[str] = set()

    def live(self, pid: str) -> bool:
        state = self.states.get(pid)
        if state is not None:
            return state == LIVE
        if pid in self.active:  # a may_after cycle back to a rule being decided
            return False
        d = self.adjust(pid)
        return d is not None and (d.fire or d.reason == BLOCKED_BY_PREDECESSOR)

    def word(self, pid: str) -> str:
        return f"{pid} {_WORDS.get(self.states.get(pid, ''), 'did not run')}"

    def adjust(self, rid: str) -> Decision | None:
        if rid in self.memo:
            return self.memo[rid]
        d = self.by_id.get(rid)
        rule = self.snapshot.get(rid)
        if d is None or rule is None:
            self.memo[rid] = d
            return d
        self.active.add(rid)
        try:
            out = self._refine(rid, d, rule)
        finally:
            self.active.discard(rid)
        self.memo[rid] = out
        return out

    def _refine(self, rid: str, d: Decision, rule: Rule) -> Decision:
        if d.fire:
            pending = tuple(p for p in rule.may_after if self.live(p))
            if pending:
                return Decision(
                    rule_id=d.rule_id,
                    fire=False,
                    reason=BLOCKED_BY_PREDECESSOR,
                    by=pending,
                    detail=d.detail,
                    upstream={},
                )
            return d
        if d.reason != BLOCKED_BY_PREDECESSOR:
            return d
        dead = tuple(p for p in d.by if not self.live(p))
        if dead:
            return Decision(
                rule_id=rid,
                fire=False,
                reason=PREDECESSOR_FAILED,
                by=dead,
                detail="; ".join(self.word(p) for p in dead),
            )
        pending = tuple(p for p in rule.may_after if p not in d.by and self.live(p))
        return Decision(
            rule_id=d.rule_id,
            fire=d.fire,
            reason=d.reason,
            by=d.by + pending,
            detail=d.detail,
            upstream=d.upstream,
        )
