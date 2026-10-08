"""Rule matching: event + rule snapshot + run facts -> one decision per candidate rule.

Pure: no store access, no clock, no randomness, inputs are never mutated. The same
snapshot and facts always give the same decisions, so replay and contextual history can
re-run matching and get identical answers.

Candidates are the rules whose trigger matches the event. Every candidate gets exactly
one :class:`Decision` (``fire`` or skip) with a reason code. Skip reasons are applied in
this precedence order:

1. ``paused`` -- the global pause flag is set; nothing fires.
2. ``disabled`` -- the rule is disabled.
3. ``variables_unsupported`` / ``variable_undefined`` -- the rule references a shared
   variable (in its condition or a ``{"$var": name}`` workflow input) and this node does
   not resolve variables, or the variable is not defined. Fail closed: a reference is never
   evaluated as missing, since a missing operand makes ``not(a in vars.x)`` true.
   A refusal never turns into an extra firing: a matched rule that a refused rule
   supersedes (directly or transitively), or that a refused member of its exclusive group
   would outrank, is refused the same way (``by`` names the refused rule), since the
   refused rule might have matched and suppressed it.
   ``condition_false`` -- the condition evaluated false (or could not be evaluated);
   ``vars.<name>`` reads the ``variables`` mapping the caller passes.
4. ``superseded_by`` -- a *matched* rule supersedes it, directly or transitively
   (A supersedes B supersedes C: a matched A also skips C). Per event: when the
   superseding rule's condition is false, the superseded rule fires.
5. ``group_lost`` -- another rule of its exclusive group won (highest ``priority``;
   ties go to the lexicographically smallest rule id). Superseded rules do not compete.
6. ``blocked_by_predecessor`` -- a ``must_after`` predecessor has not succeeded for
   this event (missing outcome, still running, failed, ...).

Last, a would-be fire on an event past the hop cap is refused as ``hop_limit`` (deviation
d21): the event's ``hops`` (:func:`~culture_rules.events.emit.event_hops`; 0 for an external
event, one more per derivation, e.g. a run's ``rules.run.*`` event) exceeds
:data:`~culture_rules.events.emit.MAX_EVENT_HOPS`, or is malformed (fail closed). So a loop of
rules firing on each other's runs ends; the node records the skip on the rule's history.

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

from culture_rules.events.emit import MAX_EVENT_HOPS, event_hops
from culture_rules.model import condition as cond
from culture_rules.model.rule import Rule, Trigger
from culture_rules.model.variable_refs import rule_variable_refs
from culture_rules.model.workflow import Workflow

__all__ = [
    "BLOCKED_BY_PREDECESSOR",
    "CONDITION_FALSE",
    "CONCURRENCY_KEY_UNRESOLVED",
    "DEDUPLICATED",
    "DISABLED",
    "FIRE",
    "GROUP_LOST",
    "HOP_LIMIT",
    "PAUSED",
    "PREDECESSOR_FAILED",
    "REASONS",
    "SUCCEEDED",
    "SUPERSEDED_BY",
    "VARIABLES_UNSUPPORTED",
    "VARIABLE_UNDEFINED",
    "ATTEMPT_BUDGET_EXHAUSTED",
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
VARIABLES_UNSUPPORTED = "variables_unsupported"
"""The rule references a shared variable and the evaluating node does not resolve them."""
VARIABLE_UNDEFINED = "variable_undefined"
"""The rule references a shared variable that is not defined."""
SUPERSEDED_BY = "superseded_by"
GROUP_LOST = "group_lost"
BLOCKED_BY_PREDECESSOR = "blocked_by_predecessor"
PREDECESSOR_FAILED = "predecessor_failed"
"""Final skip set by the node's sequencing: a ``must_after`` predecessor will not succeed."""
DEDUPLICATED = "deduplicated"
"""A firing dropped because a run with the same concurrency key is active."""
ATTEMPT_BUDGET_EXHAUSTED = "attempt_budget_exhausted"
"""A firing skipped because ``max_attempts`` runs were admitted for its concurrency key
since the last reset (a human push or green checks)."""
CONCURRENCY_KEY_UNRESOLVED = "concurrency_key_unresolved"
"""A firing skipped because the rule's concurrency key does not resolve on the event
(a missing or non-scalar value): fail closed, never fire without the protection."""
HOP_LIMIT = "hop_limit"
"""A firing refused because its event is more than ``MAX_EVENT_HOPS`` derivations from an
external one (or its hop count is malformed): an event chain never loops forever (d21)."""
REASONS = (
    FIRE,
    PAUSED,
    DISABLED,
    VARIABLES_UNSUPPORTED,
    VARIABLE_UNDEFINED,
    CONDITION_FALSE,
    SUPERSEDED_BY,
    GROUP_LOST,
    BLOCKED_BY_PREDECESSOR,
    PREDECESSOR_FAILED,
    DEDUPLICATED,
    ATTEMPT_BUDGET_EXHAUSTED,
    CONCURRENCY_KEY_UNRESOLVED,
    HOP_LIMIT,
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
            VARIABLES_UNSUPPORTED: "not evaluated: this node does not resolve shared "
            f"variables ({self.detail})",
            VARIABLE_UNDEFINED: f"not evaluated: shared variable not defined: {self.detail}",
            SUPERSEDED_BY: f"superseded by {who}",
            GROUP_LOST: f"lost exclusive group {self.detail} to {who}",
            BLOCKED_BY_PREDECESSOR: f"waiting for predecessor {who}",
            PREDECESSOR_FAILED: f"predecessor did not succeed: {self.detail or who}",
            DEDUPLICATED: "concurrency key active: run already exists",
            ATTEMPT_BUDGET_EXHAUSTED: "attempt budget exhausted",
            CONCURRENCY_KEY_UNRESOLVED: f"concurrency key unresolved: {self.detail}",
            HOP_LIMIT: f"hop limit: {self.detail}",
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
    """Default trigger test: same ``kind`` (event default ``"event"``).

    An ``event`` trigger must name a ``type`` and matches only that event type; one without a
    type matches nothing. Other kinds match on kind alone (plus ``type`` when named). An event
    whose ``data.self_authored`` is true (set by ingest) matches only triggers with
    ``params.include_self`` true.
    """
    if trigger.kind != event.get("kind", "event"):
        return False
    wanted = trigger.params.get("type")
    if wanted is None:
        if trigger.kind == "event":
            return False
    elif wanted != event.get("type"):
        return False
    data = event.get("data")
    if isinstance(data, Mapping) and data.get("self_authored") is True:
        return trigger.params.get("include_self") is True
    return True


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


def _unresolvable(rule: Rule, variables: Mapping[str, Any], supported: bool) -> Decision | None:
    """A fail-closed skip when ``rule`` references a variable this evaluation cannot read."""
    names = rule_variable_refs(rule)
    if not names:
        return None
    if not supported:
        return Decision(
            rule_id=rule.id,
            fire=False,
            reason=VARIABLES_UNSUPPORTED,
            detail="references " + ", ".join(sorted(names)),
        )
    missing = sorted(n for n in names if n not in variables)
    if missing:
        return Decision(
            rule_id=rule.id, fire=False, reason=VARIABLE_UNDEFINED, detail=", ".join(missing)
        )
    return None


def _screen(
    candidates: Iterable[Rule],
    event: Mapping[str, Any],
    variables: Mapping[str, Any],
    supported: bool,
    out: dict[str, Decision],
) -> dict[str, Rule]:
    """The enabled candidates whose condition holds; the others are decided into ``out``."""
    matched: dict[str, Rule] = {}
    for r in candidates:
        if not r.enabled:
            out[r.id] = Decision(rule_id=r.id, fire=False, reason=DISABLED)
            continue
        refused = _unresolvable(r, variables, supported)
        if refused is not None:
            out[r.id] = refused
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


_VARIABLE_REASONS = (VARIABLES_UNSUPPORTED, VARIABLE_UNDEFINED)


def _refused(out: Mapping[str, Decision], snapshot: Mapping[str, Rule]) -> list[Rule]:
    """Candidates refused for a variables reason (they might have matched)."""
    return [snapshot[rid] for rid, d in sorted(out.items()) if d.reason in _VARIABLE_REASONS]


def _gap(rule_id: str, cause: Decision, why: str) -> Decision:
    return Decision(
        rule_id=rule_id,
        fire=False,
        reason=cause.reason,
        by=(cause.rule_id,),
        detail=f"{why} {cause.rule_id}, which could not be evaluated: {cause.detail}",
    )


def _supersede_gaps(
    matched: Mapping[str, Rule],
    snapshot: Mapping[str, Rule],
    superseded: dict[str, list[str]],
    out: dict[str, Decision],
) -> None:
    """Refuse the matched rules a variables-refused rule supersedes (transitively)."""
    edges = {rid: r.supersedes for rid, r in snapshot.items()}
    for r in _refused(out, snapshot):
        cause = out[r.id]
        for bid in sorted(_closure(r.id, edges)):
            if bid in matched and bid not in out:
                out[bid] = _gap(bid, cause, "superseded by")
                superseded.setdefault(bid, []).append(r.id)


def _group_gaps(
    matched: Mapping[str, Rule],
    snapshot: Mapping[str, Rule],
    superseded: Mapping[str, list[str]],
    out: dict[str, Decision],
) -> None:
    """Refuse a group's would-be winner when a variables-refused member outranks it.

    A refused member that a *matched* rule supersedes (transitively) is no rival: it would
    be skipped whatever its variables hold, so it can never win the group."""
    refused = [r for r in _refused(out, snapshot) if r.exclusive_group is not None]
    if not refused:
        return
    edges = {rid: r.supersedes for rid, r in snapshot.items()}
    suppressed = {bid for aid in matched for bid in _closure(aid, edges)}
    refused = [r for r in refused if r.id not in suppressed]
    if not refused:
        return
    groups: dict[str, list[Rule]] = {}
    for rid, r in matched.items():
        if rid not in superseded and rid not in out and r.exclusive_group is not None:
            groups.setdefault(r.exclusive_group, []).append(r)
    for group, members in groups.items():
        winner = min(members, key=lambda r: (-r.priority, r.id))
        rivals = [r for r in refused if r.exclusive_group == group]
        if not rivals:
            continue
        best = min(rivals, key=lambda r: (-r.priority, r.id))
        if (-best.priority, best.id) < (-winner.priority, winner.id):
            out[winner.id] = _gap(winner.id, out[best.id], f"exclusive group {group} outranked by")


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
    variables_supported: bool = True,
    trigger_match: TriggerMatcher = trigger_matches,
) -> tuple[Decision, ...]:
    """Decide, for every rule whose trigger matches ``event``, whether it fires and why.

    ``rules`` is the rule snapshot, ``facts`` the run outcomes for this event, ``workflows``
    the workflow snapshot (id -> workflow) used for exported outputs, ``variables`` the
    current values of the shared variables the rules reference (name -> value; a name
    absent is undefined) and ``variables_supported`` whether the evaluating node resolves
    variables at all (when false, every rule referencing one is refused). Decisions are sorted
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

    matched = _screen(candidates, event, variables, variables_supported, out)
    superseded = _supersede(matched, snapshot, out)
    _supersede_gaps(matched, snapshot, superseded, out)
    _group_gaps(matched, snapshot, superseded, out)
    _group_losers(matched, superseded, out)
    for rid, r in matched.items():
        if rid not in out:
            out[rid] = _ordered(r, snapshot, facts, workflows)

    too_deep = _hop_limit_detail(event)
    if too_deep is not None:
        for rid, d in out.items():
            if d.fire:
                out[rid] = Decision(rule_id=rid, fire=False, reason=HOP_LIMIT, detail=too_deep)
    return tuple(out[r.id] for r in candidates)


def _hop_limit_detail(event: Mapping[str, Any]) -> str | None:
    """Why ``event`` is past the hop cap (the ``hop_limit`` detail), or ``None``."""
    hops = event_hops(event)
    if hops is None:
        return f"malformed hop count {event.get('hops')!r} (cap {MAX_EVENT_HOPS})"
    if hops > MAX_EVENT_HOPS:
        return f"event is {hops} hops from an external event (cap {MAX_EVENT_HOPS})"
    return None
