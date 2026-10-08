"""Run lineage of the split PR fixer (d21): which runs a review or a push stands on.

The fixer is three trusted workflows chained by run events: ``pr-fix`` builds and gates a
commit, ``review-commit`` (fired on ``pr-fix`` succeeding) has it reviewed, ``publish-fix``
(fired on that review approving) pushes it. A review and a push therefore act on runs that
are not their own. Nothing here trusts a wired input or an event field for that: the chain
is walked **from the store**, one verified step at a time.

:func:`upstream` - the run whose ``rules.run.succeeded`` event started ``run``:

* ``run`` was started by its rule firing on that event (its id is the deterministic
  ``run_id_for(rule, event)``: a run started by hand with a pasted trigger is not);
* the trigger snapshot on ``run`` equals the event the upstream run's immutable completion
  record emitted (:func:`~culture_rules.node.run_events.verify_run_event`): no forged or
  edited event;
* that upstream run exists and ``succeeded``.

Anything else is ``chain_unverified``.

:func:`final_gate` - the gate attempt a finished ``pr-fix`` (or single ``pr-fixer``) run
ended on: its pinned definition has exactly one ``retry_until`` loop with exactly one
actor-less built-in ``gate`` step; the loop ``succeeded``; the gate of its last iteration
``succeeded``. That state (in the store) is the authority on the built commit, its start
and base, its diff and its bundle (``gate_missing`` / ``bad_config`` otherwise).

:func:`fix_ancestry` - a re-fix's earlier reviews and fixes, back to the fix an external
event started, each link verified the same way.

:func:`rules_live` - every rule of a chain is still live and enabled (``rule_disabled``):
disabling any fixer rule mid-chain - the initiating trigger rule included - stops the
chain's push. Standard-library only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CHAIN_UNVERIFIED",
    "FinalGate",
    "fix_ancestry",
    "LineageError",
    "RUN_SUCCEEDED",
    "final_gate",
    "rules_live",
    "step_state",
    "upstream",
]

CHAIN_UNVERIFIED = "chain_unverified"
RUN_SUCCEEDED = "rules.run.succeeded"
_RUNS = "runs"  # culture_rules.engine.runs.RUNS_COLLECTION (not imported: no engine cycle)
_RULES = "rules"
_ADHOC = "adhoc:"


class LineageError(Exception):
    """The chain cannot be verified: ``code`` (with ``detail``) is the refusal."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def step_state(run: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    """The state of step ``key`` in ``run`` (``None`` when absent)."""
    return next((s for s in run.get("steps", ()) if s.get("key") == key), None)


def upstream(store: Any, run: Mapping[str, Any]) -> Mapping[str, Any]:
    """The verified run whose ``rules.run.succeeded`` event started ``run`` (module doc)."""
    from culture_rules.node.firing import run_id_for  # noqa: PLC0415 - node layer, lazily
    from culture_rules.node.run_events import verify_run_event  # noqa: PLC0415

    trigger = run.get("trigger")
    if not isinstance(trigger, Mapping) or trigger.get("type") != RUN_SUCCEEDED:
        raise LineageError(CHAIN_UNVERIFIED, "the run was not started by a run succeeding")
    rule_id, event_id = run.get("rule_id"), trigger.get("id")
    if not (isinstance(rule_id, str) and isinstance(event_id, str) and rule_id and event_id):
        raise LineageError(CHAIN_UNVERIFIED, "the run names no rule or trigger event")
    if run.get("id") != run_id_for(rule_id, event_id):
        raise LineageError(CHAIN_UNVERIFIED, "the run was not started by its rule's firing")
    why = verify_run_event(store, trigger)
    if why is not None:
        raise LineageError(CHAIN_UNVERIFIED, f"its trigger is not a genuine run event: {why}")
    data = trigger.get("data") if isinstance(trigger.get("data"), Mapping) else {}
    up = store.get(_RUNS, data.get("run_id")) if isinstance(data.get("run_id"), str) else None
    if not up or up.get("status") != "succeeded":
        raise LineageError(CHAIN_UNVERIFIED, "the upstream run did not succeed")
    return up


def fix_ancestry(
    store: Any,
    fix: Mapping[str, Any],
    *,
    role_of: Any,
    review_role: str,
    fix_role: str,
) -> list[Mapping[str, Any]]:
    """The earlier runs of ``fix``'s chain, newest first (Codex #2): a re-fix was started
    by a review run requesting changes, itself started by an earlier fix run, and so on back
    to the fix an external event started (the chain's initiator). Each link is verified like
    :func:`upstream` and must be in its role (``workflow_not_trusted``). Bounded by the hop
    cap and cycle-safe: a longer or circular lineage is ``chain_unverified``."""
    from culture_rules.events.emit import MAX_EVENT_HOPS  # noqa: PLC0415

    out: list[Mapping[str, Any]] = []
    seen = {fix.get("id")}
    current = fix
    for _ in range(MAX_EVENT_HOPS + 1):
        trigger = current.get("trigger")
        kind = trigger.get("type") if isinstance(trigger, Mapping) else None
        if not (isinstance(kind, str) and kind.startswith("rules.run.")):
            return out  # started by an external event (or by hand): the initiator
        review = upstream(store, current)
        if role_of(review) != review_role:
            raise LineageError("workflow_not_trusted", "a re-fix not started by a review run")
        earlier = upstream(store, review)
        if role_of(earlier) != fix_role:
            raise LineageError("workflow_not_trusted", "a review not of a pr-fix run")
        if review.get("id") in seen or earlier.get("id") in seen:
            raise LineageError(CHAIN_UNVERIFIED, "the chain's lineage loops")
        seen.update((review.get("id"), earlier.get("id")))
        out += [review, earlier]
        current = earlier
    raise LineageError(CHAIN_UNVERIFIED, "the chain's lineage is longer than the hop cap")


def _definition(run: Mapping[str, Any]) -> Mapping[str, Any]:
    pin = run.get("workflow")
    definition = pin.get("definition") if isinstance(pin, Mapping) else None
    return definition if isinstance(definition, Mapping) else {}


def _is_gate(step: Mapping[str, Any]) -> bool:
    config = step.get("config") if isinstance(step.get("config"), Mapping) else {}
    return step.get("kind") == "code" and config.get("builtin") == "gate"


@dataclass(frozen=True)
class FinalGate:
    """The gate attempt a fix run ended on (:func:`final_gate`)."""

    parent: str
    iteration: int
    gate_id: str
    state: Mapping[str, Any]
    body: dict[str, Mapping[str, Any]] = field(default_factory=dict)

    @property
    def outputs(self) -> Mapping[str, Any]:
        out = self.state.get("outputs")
        return out if isinstance(out, Mapping) else {}


def final_gate(run: Mapping[str, Any]) -> FinalGate:
    """The gate state ``run`` (a finished fix run) ended on, from the store (module doc)."""
    loop, body, gate = _gate_loop(run)
    placement = gate.get("placement")
    if isinstance(placement, Mapping) and placement.get("actor"):
        raise LineageError("bad_config", "the gate must be the actor-less built-in gate")
    i = _final_iteration(run, loop)
    gate_state = step_state(run, f"{loop['id']}[{i}]/{gate['id']}")
    if not gate_state or gate_state.get("status") != "succeeded":
        raise LineageError("gate_missing", "the fix run's last gate did not succeed")
    return FinalGate(
        parent=str(loop["id"]),
        iteration=i,
        gate_id=str(gate["id"]),
        state=gate_state,
        body={str(b.get("id")): b for b in body},
    )


def _gate_loop(
    run: Mapping[str, Any],
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]], Mapping[str, Any]]:
    """The pinned workflow's one ``retry_until`` loop holding a built-in gate: the loop, its
    body and that gate, else ``bad_config`` (no such loop, or more than one loop or gate)."""
    loops = []
    for step in _definition(run).get("steps") or ():
        if not isinstance(step, Mapping) or step.get("kind") != "retry_until":
            continue
        body = [b for b in step.get("body") or () if isinstance(b, Mapping)]
        gates = [b for b in body if _is_gate(b)]
        if gates:
            loops.append((step, body, gates))
    if len(loops) != 1 or len(loops[0][2]) != 1:
        raise LineageError("bad_config", "the fix run has not exactly one gate in one loop")
    loop, body, (gate,) = loops[0]
    return loop, body, gate


def _final_iteration(run: Mapping[str, Any], loop: Mapping[str, Any]) -> int:
    """The iteration the fix loop succeeded on, else ``gate_missing``."""
    state = step_state(run, str(loop.get("id")))
    if (
        not state
        or state.get("status") != "succeeded"
        or not isinstance(state.get("iteration"), int)
    ):
        raise LineageError("gate_missing", "the fix run's loop did not succeed")
    return state["iteration"]


def _live(doc: Mapping[str, Any] | None) -> bool:
    return bool(doc) and not doc.get("deleted_at") and doc.get("enabled") is not False


def rules_live(store: Any, runs: Iterable[Mapping[str, Any]]) -> str | None:
    """``rule_disabled`` unless the rule of every run in ``runs`` is live and enabled now
    (read from the store, not the run's pinned copy); a direct workflow run checks its
    workflow (``workflow_disabled``)."""
    for run in runs:
        rule_id = run.get("rule_id")
        if isinstance(rule_id, str) and rule_id.startswith(_ADHOC):
            wf = run.get("workflow_id")
            if not _live(store.get("workflows", wf) if isinstance(wf, str) else None):
                return "workflow_disabled"
            continue
        if not _live(store.get(_RULES, rule_id) if isinstance(rule_id, str) else None):
            return "rule_disabled"
    return None
