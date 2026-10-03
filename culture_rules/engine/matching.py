"""Rule matching: event + rule snapshot + run facts -> one decision per candidate rule.

Pure: no store access, no clock, no randomness, inputs are never mutated. The same
snapshot and facts always give the same decisions, so replay and contextual history can
re-run matching and get identical answers.

Candidates are the rules whose trigger matches the event. Every candidate gets exactly
one :class:`Decision` (``fire`` or skip) with a reason code. Skip reasons are applied in
this precedence order:

1. ``paused`` -- the global pause flag is set; nothing fires.
2. ``disabled`` -- the rule is disabled.
3. ``condition_false`` -- the condition evaluated false (or could not be evaluated).
4. ``superseded_by`` -- a *matched* rule supersedes it, directly or transitively
   (A supersedes B supersedes C: a matched A also skips C). Per event: when the
   superseding rule's condition is false, the superseded rule fires.
5. ``group_lost`` -- another rule of its exclusive group won (highest ``priority``;
   ties go to the lexicographically smallest rule id). Superseded rules do not compete.
6. ``blocked_by_predecessor`` -- a ``must_after`` predecessor has not succeeded for
   this event (missing outcome, still running, failed, ...).

Matching itself never produces ``predecessor_failed``: the node's sequencing
(:mod:`culture_rules.engine.chaining`) turns a ``blocked_by_predecessor`` whose predecessor can
no longer succeed for this event into that final skip.

Without an exclusive group every enabled match fires. ``may_after`` never blocks; it only
makes the predecessor's exported outputs visible if it succeeded. A firing decision's
``upstream`` holds, per ``must_after``/``may_after`` predecessor that succeeded, only the
outputs that predecessor's workflow explicitly exports.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from culture_rules.model import condition as cond
from culture_rules.model.rule import Rule, Trigger
from culture_rules.model.workflow import Workflow

__all__ = [
    "BLOCKED_BY_PREDECESSOR",
    "CONDITION_FALSE",
    "DISABLED",
    "FIRE",
    "GROUP_LOST",
    "PAUSED",
    "PREDECESSOR_FAILED",
    "REASONS",
    "SUCCEEDED",
    "SUPERSEDED_BY",
    "Decision",
    "RuleOutcome",
    "RunFacts",
    "exported_outputs",
    "fired",
    "match",
    "trigger_matches",
]

FIRE = "matched"
PAUSED = "paused"
DISABLED = "disabled"
CONDITION_FALSE = "condition_false"
SUPERSEDED_BY = "superseded_by"
GROUP_LOST = "group_lost"
BLOCKED_BY_PREDECESSOR = "blocked_by_predecessor"
PREDECESSOR_FAILED = "predecessor_failed"
"""Final skip set by the node's sequencing: a ``must_after`` predecessor will not succeed."""
REASONS = (
    FIRE,
    PAUSED,
    DISABLED,
    CONDITION_FALSE,
    SUPERSEDED_BY,
    GROUP_LOST,
    BLOCKED_BY_PREDECESSOR,
    PREDECESSOR_FAILED,
)

#: Run status that satisfies ``must_after`` and makes exports visible.
SUCCEEDED = "succeeded"


@dataclass(frozen=True, kw_only=True)
class RuleOutcome:
    """What a rule's run for this event did: its status and the outputs it produced."""

    status: str
    outputs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class RunFacts:
    """Facts about runs for the current event, keyed by rule id (supplied by the caller)."""

    outcomes: Mapping[str, RuleOutcome] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class Decision:
    """Fire or skip for one candidate rule, with a reason code and the rules responsible."""

    rule_id: str
    fire: bool
    reason: str
    by: tuple[str, ...] = ()
    detail: str = ""
    upstream: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @property
    def message(self) -> str:
        """Human-readable reason, as recorded in the rule's history."""
        who = ", ".join(self.by)
        text = {
            FIRE: "matched",
            PAUSED: "engine paused",
            DISABLED: "rule disabled",
            CONDITION_FALSE: "condition false",
            SUPERSEDED_BY: f"superseded by {who}",
            GROUP_LOST: f"lost exclusive group {self.detail} to {who}",
            BLOCKED_BY_PREDECESSOR: f"waiting for predecessor {who}",
            PREDECESSOR_FAILED: f"predecessor did not succeed: {self.detail or who}",
        }.get(self.reason, self.reason)
        if self.reason == CONDITION_FALSE and self.detail:
            text = f"{text} (condition error: {self.detail})"
        return text

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form for run history."""
        return {
            "rule_id": self.rule_id,
            "fire": self.fire,
            "reason": self.reason,
            "by": list(self.by),
            "message": self.message,
            "upstream": {k: dict(v) for k, v in self.upstream.items()},
        }


TriggerMatcher = Callable[[Trigger, Mapping[str, Any]], bool]


def trigger_matches(trigger: Trigger, event: Mapping[str, Any]) -> bool:
    """Default trigger test: same ``kind`` (event default ``"event"``) and, if the trigger
    names a ``type`` parameter, the same event ``type``."""
    if trigger.kind != event.get("kind", "event"):
        return False
    wanted = trigger.params.get("type")
    return wanted is None or wanted == event.get("type")


def exported_outputs(rule: Rule, workflows: Mapping[str, Workflow]) -> frozenset[str]:
    """Names a rule explicitly exports: its workflow's declared outputs (none without one)."""
    if rule.workflow is None:
        return frozenset()
    wf = workflows.get(rule.workflow.id)
    if wf is None:
        return frozenset()
    return frozenset(o.name for o in wf.outputs)


def fired(decisions: Iterable[Decision]) -> frozenset[str]:
    """The set of rule ids to fire."""
    return frozenset(d.rule_id for d in decisions if d.fire)


def _condition(rule: Rule, event: Mapping[str, Any], variables: Mapping[str, Any]) -> str | None:
    """``None`` when the condition holds; otherwise the error text (``""`` when just false)."""
    if rule.condition is None:
        return None
    ctx = {"trigger": dict(event), "variables": dict(variables)}
    try:
        return None if cond.evaluate(rule.condition, ctx) else ""
    except (cond.ConditionError, TypeError, ValueError) as exc:
        return str(exc) or type(exc).__name__


def _closure(start: str, edges: Mapping[str, tuple[str, ...]]) -> set[str]:
    """Rules ``start`` supersedes, transitively. Chains pass through any rule in the
    snapshot (matched or not): A supersedes B supersedes C means a matched A skips C even
    when B did not match."""
    seen: set[str] = set()
    todo = list(edges.get(start, ()))
    while todo:
        nxt = todo.pop()
        if nxt in seen:
            continue
        seen.add(nxt)
        todo.extend(edges.get(nxt, ()))
    return seen


def _upstream(
    rule: Rule,
    snapshot: Mapping[str, Rule],
    facts: RunFacts,
    workflows: Mapping[str, Workflow],
) -> dict[str, dict[str, Any]]:
    visible: dict[str, dict[str, Any]] = {}
    for pid in dict.fromkeys((*rule.must_after, *rule.may_after)):
        outcome = facts.outcomes.get(pid)
        pred = snapshot.get(pid)
        if outcome is None or outcome.status != SUCCEEDED or pred is None:
            continue
        names = exported_outputs(pred, workflows)
        visible[pid] = {k: v for k, v in sorted(outcome.outputs.items()) if k in names}
    return visible


def _screen(
    candidates: Iterable[Rule],
    event: Mapping[str, Any],
    variables: Mapping[str, Any],
    out: dict[str, Decision],
) -> dict[str, Rule]:
    """The enabled candidates whose condition holds; the others are decided into ``out``."""
    matched: dict[str, Rule] = {}
    for r in candidates:
        if not r.enabled:
            out[r.id] = Decision(rule_id=r.id, fire=False, reason=DISABLED)
            continue
        err = _condition(r, event, variables)
        if err is not None:
            out[r.id] = Decision(rule_id=r.id, fire=False, reason=CONDITION_FALSE, detail=err)
            continue
        matched[r.id] = r
    return matched


def _supersede(
    matched: Mapping[str, Rule], snapshot: Mapping[str, Rule], out: dict[str, Decision]
) -> dict[str, list[str]]:
    """Matched rules another matched rule supersedes (decided into ``out``), with by whom."""
    edges = {rid: r.supersedes for rid, r in snapshot.items()}
    superseded: dict[str, list[str]] = {}
    for aid in matched:
        for bid in _closure(aid, edges):
            if bid in matched:
                superseded.setdefault(bid, []).append(aid)
    for bid, by in superseded.items():
        out[bid] = Decision(rule_id=bid, fire=False, reason=SUPERSEDED_BY, by=tuple(sorted(by)))
    return superseded


def _group_losers(
    matched: Mapping[str, Rule], superseded: Mapping[str, list[str]], out: dict[str, Decision]
) -> None:
    """Decide into ``out`` every exclusive-group member that is not its group's winner."""
    groups: dict[str, list[Rule]] = {}
    for rid, r in matched.items():
        if rid not in superseded and r.exclusive_group is not None:
            groups.setdefault(r.exclusive_group, []).append(r)
    for group, members in groups.items():
        winner = min(members, key=lambda r: (-r.priority, r.id))
        for r in members:
            if r.id != winner.id:
                out[r.id] = Decision(
                    rule_id=r.id, fire=False, reason=GROUP_LOST, by=(winner.id,), detail=group
                )


def _ordered(
    r: Rule, snapshot: Mapping[str, Rule], facts: RunFacts, workflows: Mapping[str, Workflow]
) -> Decision:
    """Fire ``r`` unless a ``must_after`` predecessor has not succeeded for this event."""
    blocking = tuple(
        pid
        for pid in r.must_after
        if (o := facts.outcomes.get(pid)) is None or o.status != SUCCEEDED
    )
    if blocking:
        return Decision(rule_id=r.id, fire=False, reason=BLOCKED_BY_PREDECESSOR, by=blocking)
    return Decision(
        rule_id=r.id,
        fire=True,
        reason=FIRE,
        upstream=_upstream(r, snapshot, facts, workflows),
    )


def match(
    event: Mapping[str, Any],
    rules: Iterable[Rule],
    facts: RunFacts | None = None,
    *,
    workflows: Mapping[str, Workflow] | None = None,
    paused: bool = False,
    variables: Mapping[str, Any] | None = None,
    trigger_match: TriggerMatcher = trigger_matches,
) -> tuple[Decision, ...]:
    """Decide, for every rule whose trigger matches ``event``, whether it fires and why.

    ``rules`` is the rule snapshot, ``facts`` the run outcomes for this event, ``workflows``
    the workflow snapshot (id -> workflow) used for exported outputs. Decisions are sorted
    by rule id. Assumes a validated snapshot (see ``culture_rules.engine.ruleset``); a
    supersede cycle would skip every rule on it.
    """
    facts = facts or RunFacts()
    workflows = workflows or {}
    variables = variables or {}
    snapshot = {r.id: r for r in sorted(rules, key=lambda r: r.id)}
    candidates = [r for r in snapshot.values() if trigger_match(r.trigger, event)]
    out: dict[str, Decision] = {}

    if paused:
        return tuple(Decision(rule_id=r.id, fire=False, reason=PAUSED) for r in candidates)

    matched = _screen(candidates, event, variables, out)
    superseded = _supersede(matched, snapshot, out)
    _group_losers(matched, superseded, out)
    for rid, r in matched.items():
        if rid not in out:
            out[rid] = _ordered(r, snapshot, facts, workflows)

    return tuple(out[r.id] for r in candidates)
