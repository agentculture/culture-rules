"""Run executor: every run is an explicit, persisted state machine document.

A run lives in :data:`RUNS_COLLECTION` as one document. The engine keeps **no run state
in memory**: every tick re-reads the document, applies exactly one transition and writes
it back with a compare-and-set on ``rev``, so any engine instance on any host - or a
restarted one - can continue any run. Completed steps are never re-executed: a step's
completion is recorded in the same transaction that completes its claim
(:mod:`culture_rules.engine.claims`), and dispatch only ever picks up steps that are not
done. Work is dispatched only through the :class:`~culture_rules.engine.actorport.ActorPort`
seam. Standard-library only.

Run document
============

``id``, ``status`` (``running`` -> ``succeeded`` | ``failed`` | ``cancelled`` |
``superseded``), ``rev``
(incremented by every transition), ``history`` (one entry per state change: ``rev``,
``at``, ``host``, ``event``, ``step``; a queued step's repeat polls move ``rev`` without an
entry, see *Queued work*), ``rule_id`` / ``workflow_id`` (top-level copies of
the pinned ids for filtering; ``workflow_id`` is null for a rule without a workflow; a
direct workflow run's rule id is ``adhoc:<workflow id>``), ``rule`` / ``workflow`` (the **pinned**
definitions: ``id``, ``digest``, ``version`` and the full ``definition`` the run started
with - editing a workflow later never changes an in-flight run), ``trigger``,
``upstream``, ``inputs`` (workflow inputs, type-checked at start), ``outputs`` (the
workflow's explicitly exported outputs), ``error``, ``started_by``, ``created_at``,
``finished_at`` and ``steps``: a list of step states.

Step state: ``key`` (the step id; ``<loop>[<i>]/<body id>`` for a loop body step in
iteration ``i``; :data:`ACTION_STEP` for the rule's terminal action), ``def`` (step id in
the pinned workflow), ``loop`` (``{"parent", "iteration"}`` for body steps), ``status``,
``attempt``, ``host``, ``inputs``, ``outputs``, ``error`` (``{"code", "message"}``),
``deadline``, ``next_attempt_at``, ``placement_error``, ``queue`` (the last spell in an
actor's queue: ``since``, ``deadline`` - its bound -, ``polls``, ``last_at``, ``left_at``;
null until the step is first ``blocked``) and, for loops, ``iteration`` and ``results``.
A guarded wait step also carries ``lookup_retries``, ``lookup_blocked`` and
``lookup_blocked_since``.

Step statuses: ``pending`` -> ``dispatching`` (claimed, the actor is being invoked) ->
``waiting`` (actor accepted; completes later via :meth:`Executor.deliver`) | ``blocked``
(actor busy - queued; asked again with a backoff, without using an attempt) |
``retry_wait`` (attempt failed or timed out; re-dispatched after the backoff) |
``succeeded`` | ``failed`` | ``skipped`` (disabled step) | ``cancelled``. Loop steps use
``running`` while their iterations execute. A ``wait`` step is never dispatched: it goes
``pending`` -> ``sleeping`` (``deadline`` = the wake time, persisted in the run document, so
no worker holds it and a restarted or other node resumes it) -> ``succeeded``.

Semantics
=========

* **Timeouts and retries** - ``timeout_s`` (default :data:`DEFAULT_TIMEOUT_S`) is the
  working budget: every dispatch that can start work gets ``deadline = now + timeout_s``
  and hands that deadline to the actor. Accepted work whose deadline passes, an exception from
  ``invoke`` (no acknowledgement) and a retryable ``failed`` outcome all consume an
  attempt; the step is retried after ``backoff_s * backoff_multiplier ** (attempt - 1)``
  until ``retry.max_attempts`` is used up. Every attempt uses the same idempotency key, so
  a target that already did the work (ack lost) deduplicates instead of repeating it. If
  the target cannot deduplicate (``supports_idempotency_key = False``) and the work is not
  declared idempotent, an attempt with an unknown outcome is never retried: the step fails
  with ``unsafe_retry``. Only a resumed ``dispatching`` step (it may have started) keeps
  its deadline. Wrongly typed outputs fail the step at once
  (``output_type_mismatch``; retrying cannot fix a deterministic mismatch).
* **Queued work (``blocked``)** - an actor at a limit (a concurrency cap) answers
  ``blocked``: the work never started, so that time is not working time. The step holds
  no working ``deadline`` while queued; each re-ask is a fresh dispatch with the same
  attempt and idempotency key, and the dispatch the actor accepts gets the full
  ``timeout_s`` from then (re-asking is never an unknown outcome, so it is safe on a
  target that cannot deduplicate). Re-asks back off: :data:`BLOCKED_RETRY_S` after the
  first ``blocked``, doubling per answer up to :data:`BLOCKED_RETRY_MAX_S`, from whichever
  node's tick finds it due. Waiting has its own bound, ``queue.deadline`` =
  :func:`queue_limit_s` (``timeout_s * QUEUE_LIMIT_FACTOR``) after the first ``blocked``
  of the spell; past it the step fails ``queue_timeout`` without a retry (a retry would
  only queue again). History records a spell once - ``dispatched``, ``blocked``, then the
  outcome that ends it - while the repeats only move ``rev`` and update ``queue``
  (``polls``, ``last_at``), so a long queue cannot grow the run document. A guarded
  wake whose lookup is ``blocked`` re-arms with the same backoff and records
  ``wait_blocked`` once (``lookup_blocked`` counts the repeats); past the same bound from
  ``lookup_blocked_since`` (the wait step's ``timeout_s``, default
  :data:`DEFAULT_TIMEOUT_S`) it fails ``queue_timeout``, checked in housekeeping on any
  node (drained included) before another lookup, so an expired wait never looks again.
  The bound also holds while a re-poll left the step ``pending`` (a drained or
  unavailable node), but never once it is ``dispatching``: that work may have started,
  and its resumed dispatch settles it.
  Upgrade: a queued step or refused guarded wake an older engine persisted without a
  queue record is adopted first (``queue_adopted``): its spell is dated from the old
  working deadline minus ``timeout_s`` (its first blocked dispatch), else its first
  ``blocked`` / ``wait_blocked`` entry, and bounded as above - one past the bound fails
  ``queue_timeout`` without another dispatch or lookup.
* **Exactly-once dispatch** - a step is invoked only under its claim
  (:mod:`culture_rules.engine.claims`). While ``invoke`` blocks, a
  :class:`~culture_rules.engine.leasekeeper.LeaseKeeper` renews the claim's lease every
  third of the lease (until the step's deadline), so a slow actor never looks abandoned.
  Another host takes over a ``dispatching`` step only when its lease lapsed *and* the
  holder's machine is offline (no heartbeat for ``holder_offline_after``) or the step's
  deadline has passed; the holder's own host may always reclaim it (a restarted node). A
  placed step - its own placement, or the actor-derived one of the rule action and of an
  action step - is never taken over by another host: it resumes only on its holder.
* **Placement** - each step resolves its own placement
  (:func:`~culture_rules.engine.placement.resolve_placement` over enrolled machines,
  heartbeats and drain flags) and only the engine on that host dispatches it. A step with
  no placement runs on any engine node that is not drained (the first claimant wins).
  Configuration errors (unknown machine/actor, unmet requirement) fail the step; a
  temporarily unavailable host (offline, drained) leaves it pending with
  ``placement_error`` recorded.
* **Typed data flow** - inputs are assembled from the pinned workflow's edges (workflow
  inputs or upstream step outputs) and checked against the step's typed input ports;
  outputs are checked against its output ports. Static wiring mismatches are refused at
  start by :func:`culture_rules.model.validate.validate`.
* **Loops** - ``for_each`` runs its body once per item of its ``items`` input (port name
  in ``config["items"]``, default ``items``), one iteration after another; a list longer
  than ``max_iterations`` fails the loop before any iteration runs. ``retry_until`` runs
  its body until ``config["until"]`` (a condition tree; ``field``/``var`` operands read the
  iteration's final body outputs, ``var: iteration`` the 0-based iteration) holds, and
  fails with ``loop_max_exceeded`` after ``max_iterations`` iterations; its
  ``config["carry"]`` (``{input: result field}``) hands fields of one iteration's result to
  the next iteration's body inputs (the fixer's gate instruction to its agent). Body steps run in
  declared order; a body step's input port named ``item``/``index`` (for_each) or
  ``iteration`` (retry_until), or named like one of the loop's inputs, is filled
  implicitly when no edge feeds it. A ``for_each`` loop's outputs are, per declared port
  ``p``, the list of each iteration's final body output ``p`` (``results`` = the whole
  output objects); a ``retry_until`` loop's outputs are its last iteration's. Loops do not
  nest.
* **Wait steps** - ``config["seconds"]`` parks the step as ``sleeping`` until
  ``deadline``; any engine whose tick finds it due wakes it with a compare-and-set, so it
  completes once however many nodes restart. With ``config["guard"]`` = ``head_unchanged``
  the wake first reads the PR's current head SHA through the ``head_lookup`` seam (the
  repo/number/actor come from the guard's ``repo``, ``number``, ``actor``, else the run's
  inputs or trigger) and compares it with the SHA the guard's ``ref`` names (``inputs.x`` /
  ``vars.x``). A moved head ends the run ``superseded`` (unfinished steps cancelled, nothing
  later runs). Fail-safe: a lookup that raises, returns nothing, is not configured, or an
  expected SHA that cannot be resolved FAILS the step (``head_lookup_failed`` /
  ``guard_unresolved``) - the run never proceeds as if the head were unchanged. The lookup
  port gets a :data:`HEAD_LOOKUP_TIMEOUT_S` deadline (it runs inside the tick); a lookup
  that ran out of time (``deadline_exceeded`` / ``lookup_busy``, retryable) re-arms the
  wake after :data:`BLOCKED_RETRY_S`, at most :data:`HEAD_LOOKUP_RETRIES` times, then
  fails. The guarded
  lookup runs on the guard actor's machine; when that placement resolves nowhere, a fatal
  error fails the step at once and an unavailable host (offline, drained) is waited for up
  to :data:`PLACEMENT_ABANDON_AFTER` past the wake, then the step fails
  ``placement_unavailable`` (any node may do it) - never asleep forever. Wait steps
  are top-level only (a wait inside a loop body is refused at start).
* **Rule action** - after the workflow succeeds, the rule's action runs as the terminal
  step :data:`ACTION_STEP` (kind ``"action"``), its params resolved against
  ``workflow.outputs.*``, ``trigger.*``, ``rules.<id>.outputs.*`` and ``run.id`` (the run's
  own id; whole-string references or ``{{ ref }}`` templates). A rule without a workflow
  runs only its action.
  A whole string is a reference only when its path fits a namespace's shape; any other
  string (``rules.yaml``, ``workflow.md``, ``trigger.sh``) is a literal.
  ``{"$ref": path}`` always references and ``{"$literal": value}`` never does (see
  :mod:`culture_rules.model.refs`). Workflow-input mappings resolve the same way.
  The action's ``params.actor`` (a literal actor id, never resolved) is the invocation
  context's ``actor``; a port answering ``failed`` with error :data:`ACTOR_UNAVAILABLE`
  (that actor is unknown or disabled) fails the step at once with that code.
* **On failure** (d16) - when the run would end ``failed`` (a failed top-level step, a failed
  rule action, a mistyped workflow output) and the rule has an ``on_failure`` action, the
  unfinished steps are cancelled and the step :data:`FAILURE_STEP` is added, routed exactly
  like the rule action. Its params also resolve ``run.error.step`` / ``.code`` /
  ``.message``. The state keeps the original error as ``failure``. Once that step is done,
  whatever its outcome (its own retry policy only), the run ends ``failed`` with that error;
  it is added at most once. Superseded, cancelled and successful runs never run it.
* **Action steps** (d12, :mod:`culture_rules.model.action_step`) - a ``code`` step with
  ``config = {"builtin": "action", "action": {"kind", "params"}}`` dispatches exactly like
  the rule action: its invocation context is an ``"action"`` one (``config`` = the kind, name
  and *unresolved* params, ``actor`` = the literal ``params.actor``), so the same ``ports``
  routing (``action:<kind>``, and through a router the named actor's limits or
  ``actor_unavailable``) and the same action ports' refusals apply. Its params resolve as the
  rule action's do, against one namespace: ``inputs.*``, the step's input ports (including a
  loop body's implicit ``item``/``index``) - a workflow never sees its trigger. The resolved
  params are the step's persisted ``inputs`` and what the port is invoked with. With no
  placement of its own it runs (and resumes) where its actor lives, like the rule action.
  Its idempotency key is the step's (run id, step key - ``<loop>[<i>]/<id>`` per
  iteration), its retry and timeout the step's, ``config.action.idempotent`` counts like
  ``Action.idempotent`` (only a boolean ``true`` - a wrong type fails closed); the
  port's result becomes the step's outcome (outputs checked against its output ports).
* **Containment** (:class:`Containment`, every verb audited) - a global pause stops new
  runs and all new dispatch (accepted work may still complete); draining a machine stops
  new placements on it while its running steps finish; cancelling a run cancels every
  unfinished step and ignores late results.

Every transition that finishes a run (``succeeded``, ``failed``, ``cancelled``,
``superseded``) also inserts the run's immutable completion record in the same transaction
(:mod:`culture_rules.engine.run_completions`, deviation d21).

Collections written in transactions are listed in :data:`RUN_COLLECTIONS`; on MongoDB they
are created up front (``ensure_collections``).
"""

from __future__ import annotations

import copy
import hashlib
import logging
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.engine.actorport import (
    ACCEPTED,
    BLOCKED,
    COMPLETED,
    FAILED,
    ActorPort,
    InvocationContext,
    InvocationResult,
)
from culture_rules.engine.audit import AUDIT_COLLECTION, AuditLog, mutating_verb, require_identity
from culture_rules.engine.claims import (
    CLAIMS_COLLECTION,
    DEFAULT_LEASE,
    RULE_ATTEMPT_BUDGETS,
    ClaimResult,
    Claims,
    ReclaimGuard,
    idempotency_key,
)
from culture_rules.engine.leasekeeper import KeeperFactory, LeaseKeeper
from culture_rules.engine.placement import MachineState, PlacementError, resolve_placement
from culture_rules.engine.run_completions import RUN_COMPLETIONS, record_completion
from culture_rules.engine.variables import variable_values
from culture_rules.machines.enrol import enrolled_machines
from culture_rules.machines.heartbeat import (
    HEARTBEAT_COLLECTION,
    MISSED_BEATS_OFFLINE,
    offline_after,
    online_machines,
)
from culture_rules.model import condition as cond
from culture_rules.model.action import Action
from culture_rules.model.action_step import action_spec, spec_idempotent
from culture_rules.model.actor import Actor
from culture_rules.model.common import RetryPolicy
from culture_rules.model.placement import Placement
from culture_rules.model.refs import LITERAL_KEY, resolve_refs, var_name
from culture_rules.model.rule import Rule, Trigger, WorkflowRef
from culture_rules.model.validate import validate
from culture_rules.model.variable_refs import rule_variable_refs
from culture_rules.model.workflow import LOOP_KINDS, Port, Step, Workflow
from culture_rules.store.port import Document, StoragePort, StoreOps

log = logging.getLogger(__name__)

__all__ = [
    "ACTION_STEP",
    "ACTOR_UNAVAILABLE",
    "FAILURE_STEP",
    "BLOCKED_RETRY_MAX_S",
    "BLOCKED_RETRY_S",
    "CONTROLS_COLLECTION",
    "DEFAULT_TIMEOUT_S",
    "HEAD_LOOKUP_PORT",
    "QUEUE_LIMIT_FACTOR",
    "QUEUE_TIMEOUT",
    "RUNS_COLLECTION",
    "RUN_COLLECTIONS",
    "SLEEPING",
    "STOP_RUNS_LIMIT",
    "SUPERSEDED",
    "Containment",
    "Executor",
    "RunError",
    "active_runs",
    "drained_machines",
    "due_steps",
    "ensure_collections",
    "is_paused",
    "queue_limit_s",
    "step_key",
    "step_state",
    "type_ok",
]

RUNS_COLLECTION = "runs"
CONTROLS_COLLECTION = "controls"
"""Containment flags: ``global`` (pause) and ``drain/<machine>`` documents."""
RUN_COLLECTIONS: tuple[str, ...] = (
    RUNS_COLLECTION,
    CONTROLS_COLLECTION,
    CLAIMS_COLLECTION,
    AUDIT_COLLECTION,
    RUN_COMPLETIONS,
    RULE_ATTEMPT_BUDGETS,  # a terminal transition may hold its key (chain_hold, d21)
)
RULES_COLLECTION = "rules"
WORKFLOWS_COLLECTION = "workflows"
ACTORS_COLLECTION = "actors"

ACTION_STEP = "@action"
FAILURE_STEP = "@on_failure"
"""Step key of a rule's ``on_failure`` action (d16), run once when the run fails."""
TERMINAL_STEPS = (ACTION_STEP, FAILURE_STEP)
ACTOR_UNAVAILABLE = "actor_unavailable"
"""Failure code (and the ``error`` an actor port returns) when the actor a rule action
names in ``params.actor`` is unknown or disabled; the step fails without a retry."""
"""Step key of a rule's terminal action."""
DEFAULT_TIMEOUT_S = 3600.0
BLOCKED_RETRY_S = 5.0
"""First re-ask delay of a ``blocked`` step (and of a guarded wake whose lookup is blocked);
each further blocked answer doubles it, up to :data:`BLOCKED_RETRY_MAX_S`."""
BLOCKED_RETRY_MAX_S = 60.0
"""Cap of the blocked re-ask backoff: a freed seat is taken within a minute at most."""
QUEUE_LIMIT_FACTOR = 2.0
"""How long a step may wait in an actor's queue (``blocked``), as a multiple of its working
budget ``timeout_s`` (:func:`queue_limit_s`): room for the work ahead of it - one holder of
a capped seat with the same budget plus as much again - without waiting forever."""
QUEUE_TIMEOUT = "queue_timeout"
"""Error code of a step that stayed ``blocked`` past its queue bound (never started)."""
_BACKOFF_MAX_EXP = 16

ACTIVE = "running"
SUPERSEDED = "superseded"
"""Run end state: a wait step's head_unchanged guard found the PR head had moved."""
SLEEPING = "sleeping"
"""Step status of a wait step parked until its ``deadline``."""
HEAD_BLOCKED = "head_blocked"
"""Internal outcome: the head lookup's actor was at a limit; the wake is retried later."""
HEAD_LOOKUP_PORT = "action:github.pr_head"
"""Port key the default head lookup routes through (see :class:`Executor`)."""
HEAD_LOOKUP_TIMEOUT_S = 10.0
"""The invocation deadline of a guarded wake's head lookup. It runs inside the executor's
tick, so the port honours it (secret resolve and HTTP calls) rather than block the tick."""
HEAD_RETRY = "head_retry"
"""Internal outcome: the head lookup timed out (``deadline_exceeded``/``lookup_busy``)."""
HEAD_LOOKUP_RETRIES = 5
"""How many timed-out head lookups a guarded wake re-arms for before it fails
``head_lookup_failed`` (fail-safe: never proceeds as if the head were unchanged)."""
_HEAD_RETRY_CODES = frozenset({"deadline_exceeded", "lookup_busy"})
RUN_DONE = ("succeeded", "failed", "cancelled", SUPERSEDED)
STEP_DONE = ("succeeded", "failed", "skipped", "cancelled")
STEP_OK = ("succeeded", "skipped")

PLACEMENT_UNAVAILABLE = "placement_unavailable"
"""Failure code of work whose placed host stayed unavailable past
:data:`PLACEMENT_ABANDON_AFTER`: a guarded wait whose lookup host is offline or drained, and
a placed rule's firing intent whose evaluating host went offline before starting it."""
PLACEMENT_ABANDON_AFTER = timedelta(minutes=10)
"""How long a placed host may stay unavailable before work only it may do is failed by any
node (``placement_unavailable``). Far above the heartbeat's offline threshold (30 s), so a
slow or restarting host keeps its work; bounded, so a dead one never strands it."""

#: Placement failures that waiting cannot fix: the step fails instead of staying pending.
FATAL_PLACEMENT = frozenset(
    {
        "placement.machine_unknown",
        "placement.machine_disabled",
        "placement.actor_unknown",
        "placement.actor_disabled",
        "placement.actor_unplaced",
        "placement.requirement_unmet",
        "placement.invalid_form",
        "placement.address_refused",
    }
)

#: Id prefix of the synthetic rule a direct workflow run pins (:meth:`Executor.start_workflow`).
ADHOC_RULE_PREFIX = "adhoc:"

_MAX_TRANSITIONS_PER_TICK = 10_000
_MAX_CAS_RETRIES = 50
Clock = Callable[[], datetime]
Fence = Callable[[StoreOps], None]
"""Run inside a run's insert transaction before the insert (see :meth:`Executor.start`)."""
HeadLookup = Callable[[str | None, str, int], str]
"""``lookup(actor_id, repo, number) -> head sha``; raises (or returns a non-string) on failure."""
Ports = Mapping[str, ActorPort] | Callable[[InvocationContext], ActorPort | None]


class RunError(ValueError):
    """A run could not be started or a containment verb was refused."""

    def __init__(self, code: str, message: str, details: Iterable[Any] = ()) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = list(details)


class _HeadBlocked(Exception):
    """The head lookup's actor refused for now (a limit); try again later."""


class _HeadRetry(Exception):
    """The head lookup ran out of time (or lookup workers); try again, a bounded number of
    times (:data:`HEAD_LOOKUP_RETRIES`)."""


class _Conflict(Exception):
    """A compare-and-set lost inside a transaction; the caller re-reads and retries."""


# --------------------------------------------------------------------------- helpers


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _parse(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _digest(model: Any) -> str:
    return "sha256:" + hashlib.sha256(model.to_json().encode("utf-8")).hexdigest()


def step_key(loop: str, iteration: int, body_step: str) -> str:
    """Key of body step ``body_step`` of loop ``loop`` in iteration ``iteration`` (0-based)."""
    return f"{loop}[{iteration}]/{body_step}"


def step_state(doc: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    """The state of step ``key`` in run document ``doc``, or None."""
    return next((s for s in doc.get("steps", ()) if s.get("key") == key), None)


def type_ok(port_type: str, value: Any) -> bool:
    """Whether ``value`` is a valid value of port type ``port_type``."""
    if port_type == "any":
        return True
    if port_type == "string":
        return isinstance(value, str)
    if port_type == "boolean":
        return isinstance(value, bool)
    if port_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if port_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if port_type == "object":
        return isinstance(value, dict)
    if port_type == "array":
        return isinstance(value, list)
    return False


def _check_ports(ports: Iterable[Port], values: Mapping[str, Any], what: str) -> dict | None:
    for p in ports:
        value = values.get(p.name)
        if value is None:
            if p.required:
                return _error(f"{what}_missing", f"{what} {p.name!r} ({p.type}) is missing")
            continue
        if not type_ok(p.type, value):
            return _error(
                f"{what}_type_mismatch",
                f"{what} {p.name!r} expects {p.type}, got {type(value).__name__}",
            )
    return None


def _check_direct_inputs(workflow: Workflow, inputs: Any) -> dict[str, Any]:
    """The non-null ``inputs`` of a direct workflow run, or ``RunError("invalid_inputs")``."""
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, Mapping):
        raise RunError("invalid_inputs", "inputs must be an object of port name -> value")
    declared = {p.name for p in workflow.inputs}
    unknown = sorted(str(k) for k in inputs if k not in declared)
    if unknown:
        raise RunError(
            "invalid_inputs",
            f"workflow {workflow.id!r} declares no input {', '.join(map(repr, unknown))}",
            [{"port": name, "code": "unknown"} for name in unknown],
        )
    values = {k: v for k, v in inputs.items() if v is not None}
    for p in workflow.inputs:
        problem = _check_ports((p,), values, "input")
        if problem:
            detail = {"port": p.name, "code": problem["code"]}
            raise RunError("invalid_inputs", problem["message"], [detail])
    return values


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def ensure_collections(store: Any) -> None:
    """Create the run collections up front where the adapter needs it (MongoDB)."""
    ensure = getattr(store, "ensure_collections", None)
    if callable(ensure):
        ensure(*RUN_COLLECTIONS)


def is_paused(store: StoreOps) -> bool:
    """Whether the global pause flag is set."""
    return bool((store.get(CONTROLS_COLLECTION, "global") or {}).get("paused"))


def drained_machines(store: StoreOps) -> set[str]:
    """Names of machines currently drained."""
    found = store.find(CONTROLS_COLLECTION, {"kind": "drain", "drained": True})
    return {d["machine"] for d in found if isinstance(d.get("machine"), str)}


def _retry_of(policy: RetryPolicy | None) -> RetryPolicy:
    return policy or RetryPolicy()


# --------------------------------------------------------------------------- pinned plan


@dataclass
class _Plan:
    """The pinned rule + workflow of one run, parsed from its document (no run state)."""

    rule: Rule
    workflow: Workflow | None
    top: dict[str, Step] = field(default_factory=dict)
    body_parent: dict[str, str] = field(default_factory=dict)
    body: dict[str, Step] = field(default_factory=dict)

    @classmethod
    def of(cls, doc: Mapping[str, Any]) -> _Plan:
        rule = Rule.from_dict(doc["rule"]["definition"], strict=False)
        wf_pin = doc.get("workflow")
        wf = Workflow.from_dict(wf_pin["definition"], strict=False) if wf_pin else None
        plan = cls(rule, wf)
        for s in wf.steps if wf else ():
            plan.top[s.id] = s
            for b in s.body:
                plan.body_parent[b.id] = s.id
                plan.body[b.id] = b
        return plan

    def step(self, state: Mapping[str, Any]) -> Step | None:
        sid = state.get("def")
        return self.body.get(sid) if state.get("loop") else self.top.get(sid)

    def edges_into(self, step_id: str) -> list[Any]:
        return [e for e in (self.workflow.edges if self.workflow else ()) if e.target == step_id]


# --------------------------------------------------------------------------- containment


class Containment:
    """Global pause, machine drain and run cancel - each audited in its own transaction."""

    def __init__(
        self, store: StoragePort, audit: AuditLog | None = None, *, clock: Clock | None = None
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._audit = audit or AuditLog(clock=self._clock)
        ensure_collections(store)

    def _flag(
        self,
        doc_id: str,
        field_name: str,
        value: bool,
        verb: str,
        identity: str,
        extra: Mapping[str, Any],
    ) -> Document:
        require_identity(identity)
        with self._store.transaction() as tx:
            before = tx.get(CONTROLS_COLLECTION, doc_id)
            if bool((before or {}).get(field_name)) == value:
                state = "already" if value else "not"
                raise RunError(f"{verb.split('.')[1]}_refused", f"{doc_id} is {state} {field_name}")
            after = tx.put(
                CONTROLS_COLLECTION,
                {
                    **(before or {}),
                    **extra,
                    "id": doc_id,
                    field_name: value,
                    "changed_by": identity,
                    "changed_at": _iso(self._clock()),
                },
            )
            self._audit.write(
                tx,
                identity=identity,
                verb=verb,
                collection=CONTROLS_COLLECTION,
                target_id=doc_id,
                before=before,
                after=after,
            )
        return after

    @mutating_verb("engine.pause", "Globally pause rule firing and step dispatch")
    def pause(self, identity: str) -> Document:
        return self._flag("global", "paused", True, "engine.pause", identity, {"kind": "pause"})

    @mutating_verb("engine.resume", "Lift the global pause")
    def resume(self, identity: str) -> Document:
        return self._flag("global", "paused", False, "engine.resume", identity, {"kind": "pause"})

    @mutating_verb("machine.drain", "Drain a machine: no new placements, running steps finish")
    def drain(self, machine: str, identity: str) -> Document:
        return self._flag(
            f"drain/{machine}",
            "drained",
            True,
            "machine.drain",
            identity,
            {"kind": "drain", "machine": machine},
        )

    @mutating_verb("machine.undrain", "Return a drained machine to service")
    def undrain(self, machine: str, identity: str) -> Document:
        return self._flag(
            f"drain/{machine}",
            "drained",
            False,
            "machine.undrain",
            identity,
            {"kind": "drain", "machine": machine},
        )

    @mutating_verb("runs.cancel", "Cancel a run; unfinished steps are cancelled")
    def cancel(self, run_id: str, identity: str, reason: str = "") -> Document:
        require_identity(identity)
        now = self._clock()
        with self._store.transaction() as tx:
            before = tx.get(RUNS_COLLECTION, run_id)
            if before is None:
                raise RunError("run_not_found", f"run {run_id!r} does not exist")
            if before.get("status") != ACTIVE:
                raise RunError("run_finished", f"run {run_id!r} is already {before['status']}")
            doc = copy.deepcopy(before)
            for st in doc["steps"]:
                if st["status"] not in STEP_DONE:
                    st["status"] = "cancelled"
            doc["status"] = "cancelled"
            doc["finished_at"] = _iso(now)
            doc["error"] = _error("cancelled", reason or f"cancelled by {identity}")
            _record(doc, now, self._audit.host, "cancelled", None)
            res = tx.update_if(RUNS_COLLECTION, run_id, {"rev": before["rev"]}, _mutable(doc))
            # MemoryStore runs transactions one at a time; MongoDB does not serialise their
            # bodies but aborts the later writer with a write conflict (TransientStoreError)
            # before this point. The guard covers an adapter that does neither.
            if not res.won:  # pragma: no cover
                raise RunError("conflict", f"run {run_id!r} changed concurrently")
            record_completion(tx, before, doc, now)
            self._audit.write(
                tx,
                identity=identity,
                verb="runs.cancel",
                collection=RUNS_COLLECTION,
                target_id=run_id,
                before=before,
                after=res.document,
            )
        return res.document

    def stop_rule_runs(
        self, rule_id: str, identity: str, *, apply: bool = False, reason: str = ""
    ) -> dict[str, Any]:
        """Cancel every active run of a disabled rule (d17); a dry-run only lists them.

        Stopping is offered only once the rule is off: a rule that is still live and enabled
        is refused (``rule_enabled``). Each run goes through :meth:`cancel` (status
        ``cancelled``, audited as ``runs.cancel``), so a stopped run never reaches a later
        step, its action or any failure hand-back. The runs are read from the store, so a
        run another node is executing is cancelled too. A run that finishes in between is
        skipped, so calling this twice is a no-op the second time.
        """
        require_identity(identity)
        rule_doc = self._store.get(RULES_COLLECTION, rule_id)
        if rule_doc is None:
            raise RunError("rule_not_found", f"rule {rule_id!r} does not exist")
        if not rule_doc.get("deleted_at") and rule_doc.get("enabled") is not False:
            raise RunError(
                "rule_enabled",
                f"rule {rule_id!r} is enabled; disable it before stopping its runs",
            )
        listed, total = active_runs(self._store, rule_id, limit=None)
        cancelled: list[str] = []
        if apply:
            why = reason or f"rule disabled: stopped by {identity}"
            for item in listed:
                try:
                    self.cancel(item["id"], identity, why)
                except RunError as exc:
                    if exc.code not in ("run_finished", "run_not_found"):
                        raise
                    continue  # finished (or purged) meanwhile: nothing left to stop
                cancelled.append(item["id"])
        return {
            "rule_id": rule_id,
            "applied": apply,
            "runs": listed[:STOP_RUNS_LIMIT],
            "total": total,
            "cancelled": cancelled,
        }


STOP_RUNS_LIMIT = 50
"""How many active runs a disable response or a stop-runs answer lists (``total`` counts all)."""


def active_runs(
    store: StoragePort, rule_id: str, *, limit: int | None = STOP_RUNS_LIMIT
) -> tuple[list[dict[str, Any]], int]:
    """A rule's non-terminal runs, oldest first, as ``{id, status, started_at}``; and the count.

    The only non-terminal run status is :data:`ACTIVE` (every other one is in
    :data:`RUN_DONE`), so the store is asked for exactly ``{rule_id, status: running}``, an
    equality filter: a long-lived rule's finished runs are never loaded. Reads the store, so
    runs on every node are included.
    """
    docs = store.find(RUNS_COLLECTION, {"rule_id": rule_id, "status": ACTIVE})
    docs.sort(key=lambda d: (d.get("created_at") or "", d["id"]))
    items = [
        {"id": d["id"], "status": d.get("status"), "started_at": d.get("created_at")} for d in docs
    ]
    return (items if limit is None else items[:limit]), len(items)


_MUTABLE = ("status", "rev", "steps", "history", "outputs", "error", "finished_at")


def _mutable(doc: Mapping[str, Any]) -> dict[str, Any]:
    return {k: doc.get(k) for k in _MUTABLE}


def _record(doc: dict, now: datetime, host: str, event: str, key: str | None) -> None:
    doc["rev"] += 1
    doc["history"].append(
        {"rev": doc["rev"], "at": _iso(now), "host": host, "event": event, "step": key}
    )


def _bump(doc: dict) -> None:
    """A write that changes no state worth a history entry (a repeat poll of a queued step):
    ``rev`` still moves, so the compare-and-set holds, but the history does not grow."""
    doc["rev"] += 1


def queue_limit_s(timeout_s: float) -> float:
    """How long a step whose working budget is ``timeout_s`` may wait ``blocked``."""
    return timeout_s * QUEUE_LIMIT_FACTOR


def _blocked_delay(polls: int) -> float:
    """Re-ask delay after the ``polls``-th blocked answer in a row: 5, 10, 20, 40, 60, 60 s."""
    exp = min(max(polls - 1, 0), _BACKOFF_MAX_EXP)
    return min(BLOCKED_RETRY_S * 2**exp, BLOCKED_RETRY_MAX_S)


def _in_queue(st: Mapping[str, Any]) -> bool:
    """Whether ``st`` is in a queue spell: blocked (or between two polls) and not left yet."""
    queue = st.get("queue")
    return bool(queue) and queue.get("left_at") is None


def _new_state(key: str, step_id: str, loop: dict | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "def": step_id,
        "loop": loop,
        "status": "pending",
        "attempt": 0,
        "host": None,
        "inputs": None,
        "outputs": None,
        "error": None,
        "deadline": None,
        "next_attempt_at": None,
        "placement_error": None,
        "resume": False,
        "queue": None,
    }


# --------------------------------------------------------------------------- executor


class Executor:
    """One engine instance on host ``host``: starts runs and advances them by ticks.

    ``ports`` routes work to adapters: a mapping from step kind (``logic``, ``ai``,
    ``code``, ``actor_task``), ``action:<action kind>`` or ``action``, with ``"*"`` as
    the fallback - or a callable taking the :class:`InvocationContext`.
    """

    def __init__(
        self,
        store: StoragePort,
        host: str,
        ports: Ports,
        *,
        audit: AuditLog | None = None,
        clock: Clock | None = None,
        lease: timedelta = DEFAULT_LEASE,
        identity: str | None = None,
        lease_keeper: KeeperFactory = LeaseKeeper,
        holder_offline_after: timedelta = timedelta(seconds=offline_after()),
        head_lookup: HeadLookup | None = None,
    ) -> None:
        """``lease_keeper`` builds the keeper that renews a step's lease while its actor
        is invoked; ``holder_offline_after`` is how stale a holder's heartbeat must be
        before another host may take over its lapsed ``dispatching`` step. ``head_lookup`` reads a
        PR's current head SHA for a wait step's ``head_unchanged`` guard; unset, it goes
        through the ``action:github.pr_head`` port when ``ports`` has one, else the guard
        fails safe."""
        if not isinstance(host, str) or not host:
            raise ValueError("host must be a non-empty string")
        self._store = store
        self.host = host
        self._ports = ports
        self._clock = clock or (lambda: datetime.now(UTC))
        self._audit = audit or AuditLog(host=host, clock=self._clock)
        self.identity = identity or f"engine@{host}"
        self._claims = Claims(
            store, f"{host}/{uuid.uuid4().hex[:8]}", lease=lease, clock=self._clock
        )
        self._lease_keeper = lease_keeper
        self._head_lookup = head_lookup
        self._holder_offline_after = holder_offline_after
        # placement uses the same cadence the takeover threshold was built from
        self._beat_every = holder_offline_after.total_seconds() / MISSED_BEATS_OFFLINE
        ensure_collections(store)

    # ------------------------------------------------------------------ queries

    def run(self, run_id: str) -> Document | None:
        """The run document (always read from the store)."""
        return self._store.get(RUNS_COLLECTION, run_id)

    # ------------------------------------------------------------------ start

    def start_from_store(
        self,
        rule_id: str,
        *,
        trigger: Mapping[str, Any] | None = None,
        upstream: Mapping[str, Mapping[str, Any]] | None = None,
        identity: str | None = None,
        run_id: str | None = None,
        variables: Mapping[str, Any] | None = None,
        concurrency_key: str | None = None,
        fence: Fence | None = None,
    ) -> Document:
        """Start a run of the stored rule ``rule_id`` and its stored workflow, pinning both.

        The rule is validated in stored mode (see :meth:`start`). ``variables`` are the
        shared-variable values its ``{"$var": name}`` inputs map (the snapshot a firing
        intent carries); ``None`` reads the current values from the store.
        """
        # One read-only transaction: the rule, its workflow and the variable values it maps
        # come from the same snapshot.
        with self._store.transaction() as tx:
            rule_doc = tx.get(RULES_COLLECTION, rule_id)
            if rule_doc is None:
                raise RunError("rule_not_found", f"rule {rule_id!r} does not exist")
            if rule_doc.get("deleted_at"):
                raise RunError("not_fireable", f"rule {rule_id!r} is deleted")
            rule = Rule.from_dict(rule_doc, strict=False)
            workflow = None
            if rule.workflow is not None:
                wf_doc = tx.get(WORKFLOWS_COLLECTION, rule.workflow.id)
                if wf_doc is None:
                    raise RunError("workflow_not_found", f"workflow {rule.workflow.id!r} missing")
                if wf_doc.get("deleted_at"):
                    raise RunError("not_fireable", f"workflow {rule.workflow.id!r} is deleted")
                workflow = Workflow.from_dict(wf_doc, strict=False)
            if variables is None:
                variables = variable_values(tx, rule_variable_refs(rule))
        return self.start(
            rule,
            workflow,
            trigger=trigger,
            upstream=upstream,
            identity=identity,
            run_id=run_id,
            concurrency_key=concurrency_key,
            stored=True,
            variables=variables,
            fence=fence,
        )

    def start_workflow(
        self,
        workflow_id: str,
        inputs: Mapping[str, Any] | None = None,
        by: str | None = None,
        *,
        run_id: str | None = None,
    ) -> Document:
        """Run the stored workflow ``workflow_id`` directly, with no rule of its own.

        ``inputs`` are checked against the workflow's typed input ports first: a missing
        required input, a wrongly typed one or one the workflow does not declare is refused
        with ``invalid_inputs`` naming the port (``None`` counts as absent). The run then
        pins a synthetic rule :data:`ADHOC_RULE_PREFIX` ``+ workflow_id`` (manual trigger,
        the inputs as ``{"$literal": v}`` mappings, a ``noop`` action) and starts through
        :meth:`start`, audited as ``runs.start`` under ``by``. Deleted or disabled workflows
        are ``not_fireable``; a paused engine refuses as usual.
        """
        wf_doc = self._store.get(WORKFLOWS_COLLECTION, workflow_id)
        if wf_doc is None:
            raise RunError("workflow_not_found", f"workflow {workflow_id!r} does not exist")
        if wf_doc.get("deleted_at"):
            raise RunError("not_fireable", f"workflow {workflow_id!r} is deleted")
        if wf_doc.get("enabled") is False:
            raise RunError("not_fireable", f"workflow {workflow_id!r} is disabled")
        workflow = Workflow.from_dict(wf_doc, strict=False)
        values = _check_direct_inputs(workflow, inputs)
        synthetic = Rule(
            id=f"{ADHOC_RULE_PREFIX}{workflow.id}",
            name=f"Direct run of {workflow.name or workflow.id}",
            trigger=Trigger(kind="manual"),
            workflow=WorkflowRef(
                id=workflow.id,
                version=workflow.version,
                inputs={k: {LITERAL_KEY: v} for k, v in values.items()},
            ),
            action=Action(kind="noop"),
        )
        return self.start(synthetic, workflow, identity=by, run_id=run_id)

    @mutating_verb("runs.start", "Start a run of a rule, pinning its rule/workflow versions")
    def start(
        self,
        rule: Rule,
        workflow: Workflow | None = None,
        *,
        trigger: Mapping[str, Any] | None = None,
        upstream: Mapping[str, Mapping[str, Any]] | None = None,
        identity: str | None = None,
        run_id: str | None = None,
        stored: bool = False,
        variables: Mapping[str, Any] | None = None,
        concurrency_key: str | None = None,
        fence: Fence | None = None,
    ) -> Document:
        """Validate, pin and persist a new run (audited). Refused while paused.

        ``stored=True`` marks ``rule`` as read back from the store: it is validated in stored
        mode, so a rule saved before the save-time catalog checks still runs. ``fence`` runs
        first inside the transaction that inserts the run, so its reads and writes commit
        atomically with the insert (or it raises, and nothing is written) - a firing intent
        uses it to move itself from ``pending`` to ``started``.
        """
        identity = require_identity(identity or self.identity)
        if is_paused(self._store):
            raise RunError("paused", "the engine is globally paused; nothing fires")
        errors = validate(rule, stored=stored)
        if errors:
            raise RunError("invalid_rule", "rule failed validation", [e.to_dict() for e in errors])
        trigger = dict(trigger or {})
        upstream = {k: dict(v) for k, v in (upstream or {}).items()}
        inputs = self._check_workflow(rule, workflow, trigger, upstream, variables or {})
        now = self._clock()
        run_id = run_id or f"run-{uuid.uuid4().hex}"
        steps = [_new_state(s.id, s.id) for s in (workflow.steps if workflow else ())]
        doc: dict[str, Any] = {
            "id": run_id,
            "kind": "run",
            "status": ACTIVE,
            "rev": 0,
            "history": [],
            "rule_id": rule.id,
            "workflow_id": workflow.id if workflow else None,
            "rule": {"id": rule.id, "digest": _digest(rule), "definition": rule.to_dict()},
            "workflow": (
                {
                    "id": workflow.id,
                    "version": workflow.version,
                    "digest": _digest(workflow),
                    "definition": workflow.to_dict(),
                }
                if workflow
                else None
            ),
            "trigger": trigger,
            "upstream": upstream,
            "inputs": inputs,
            "outputs": None,
            "error": None,
            "started_by": identity,
            "created_at": _iso(now),
            "finished_at": None,
            "steps": steps,
            **({"concurrency_key": concurrency_key} if concurrency_key is not None else {}),
        }
        _record(doc, now, self.host, "started", None)
        with self._store.transaction() as tx:
            if fence is not None:
                fence(tx)
            stored = tx.insert(RUNS_COLLECTION, doc)
            self._audit.write(
                tx,
                identity=identity,
                verb="runs.start",
                collection=RUNS_COLLECTION,
                target_id=run_id,
                before=None,
                after=stored,
            )
        return stored

    def _check_workflow(
        self,
        rule: Rule,
        workflow: Workflow | None,
        trigger: dict[str, Any],
        upstream: dict[str, dict[str, Any]],
        variables: Mapping[str, Any],
    ) -> dict[str, Any]:
        if rule.workflow is None:
            if workflow is not None:
                raise RunError("workflow_mismatch", f"rule {rule.id!r} names no workflow")
            return {}
        if workflow is None:
            raise RunError("workflow_required", f"rule {rule.id!r} needs workflow")
        if workflow.id != rule.workflow.id:
            raise RunError(
                "workflow_mismatch", f"rule wants {rule.workflow.id!r}, got {workflow.id!r}"
            )
        if rule.workflow.version is not None and rule.workflow.version != workflow.version:
            raise RunError(
                "workflow_version_unavailable",
                f"rule pins {workflow.id} v{rule.workflow.version}; v{workflow.version} given",
            )
        errors = validate(workflow)
        if errors:
            raise RunError(
                "invalid_workflow", "workflow failed validation", [e.to_dict() for e in errors]
            )
        _check_supported_steps(workflow)
        missing = sorted(
            {n for ref in rule.workflow.inputs.values() if (n := var_name(ref)) is not None}
            - set(variables)
        )
        if missing:  # fail closed, as matching does: never run without the value
            raise RunError(
                "variable_undefined",
                "workflow input(s) reference undefined shared variable(s): " + ", ".join(missing),
            )
        context = {
            "trigger": trigger,
            "rules": {k: {"outputs": v} for k, v in upstream.items()},
            "variables": dict(variables),
        }
        inputs = {name: resolve_refs(ref, context) for name, ref in rule.workflow.inputs.items()}
        inputs = {k: v for k, v in inputs.items() if v is not None}
        problem = _check_ports(workflow.inputs, inputs, "input")
        if problem:
            raise RunError(problem["code"], problem["message"])
        return inputs

    # ------------------------------------------------------------------ ticking

    def tick(self) -> int:
        """Advance every active run as far as possible; return the transitions made."""
        made = 0
        for doc in self._store.find(RUNS_COLLECTION, {"status": ACTIVE}):
            made += self._advance(doc["id"])
        return made

    def run_until_idle(self, max_ticks: int = 1000) -> int:
        """Tick until a tick makes no transition; return the total made."""
        total = 0
        for _ in range(max_ticks):
            made = self.tick()
            total += made
            if not made:
                break
        return total

    def _advance(self, run_id: str) -> int:
        made = 0
        for _ in range(_MAX_TRANSITIONS_PER_TICK):
            doc = self._store.get(RUNS_COLLECTION, run_id)
            if doc is None or doc.get("status") != ACTIVE:
                break
            moved = self._transition(doc)
            if moved is None:
                break
            if moved:
                made += 1
        return made

    def _transition(self, doc: Document) -> bool | None:
        """Try the next transition of active run ``doc``: housekeeping, else a due wake, else
        (unless paused) one dispatch. None: nothing more to do now; else whether one was
        made (a lost CAS is retried with a fresh read)."""
        now = self._clock()
        plan = _Plan.of(doc)
        new = _housekeep(plan, doc, now, self.host)
        if new is not None:
            return bool(self._cas(doc, new))
        woken = self._wake_waits(plan, doc, now, is_paused(self._store))
        if woken is not None:
            return bool(woken)
        if is_paused(self._store):
            return None
        if self._dispatch_one(plan, doc, now):
            return True
        return None

    # ------------------------------------------------------------------ wait steps

    def _wake_waits(
        self, plan: _Plan, doc: Document, now: datetime, paused: bool = False
    ) -> bool | None:
        """Wake the first due sleeping wait step. None: none due (or none this node may wake
        now); else whether our CAS won (a lost CAS means another engine woke it).

        A guarded wake makes an external call, so it waits while the engine is paused and
        runs only on the node the guard's actor is placed on (and never a drained one); an
        unguarded wake makes no external call and completes even under pause (nothing new
        dispatches). A ``blocked`` lookup re-arms the timer instead of failing."""
        for st in doc["steps"]:
            wake = _due_wake(st, now)
            if wake is None:
                continue
            step = plan.step(st)
            guard = (step.config.get("guard") if step else None) or None
            if guard:
                gate = self._guard_gate(plan, doc, st, step, guard, wake, now, paused)
                if gate is _WAKE_SKIP:
                    continue
                if gate is not _WAKE_GO:
                    return gate
            return self._wake_one(plan, doc, st, step, guard, now)
        return None

    def _guard_gate(
        self,
        plan: _Plan,
        doc: Document,
        st: Mapping,
        step: Step | None,
        guard: Mapping,
        wake: datetime,
        now: datetime,
        paused: bool,
    ) -> Any:
        """Whether a due guarded wake proceeds here (``_WAKE_GO``), is left for now
        (``_WAKE_SKIP``), or was answered by :meth:`_guard_unplaceable` (whether its CAS won)."""
        if paused:
            return _WAKE_SKIP
        where = self._guard_eligible(plan, step, guard, now)
        if isinstance(where, PlacementError):
            answer = self._guard_unplaceable(doc, st, where, wake, now)
            if answer is None:
                return _WAKE_SKIP
            return answer
        if not where:
            return _WAKE_SKIP
        return _WAKE_GO

    def _wake_one(
        self,
        plan: _Plan,
        doc: Document,
        st: Mapping,
        step: Step | None,
        guard: Mapping | None,
        now: datetime,
    ) -> bool:
        """Wake due sleeping ``st`` (its guard, if any, checked now); whether our CAS won."""
        outcome = self._check_guard(plan, doc, step, guard) if guard else None
        new, nst = _copy_with(doc, st["key"])
        retries = int(st.get("lookup_retries") or 0) + 1
        if outcome is not None and outcome["code"] == HEAD_RETRY:
            if retries > HEAD_LOOKUP_RETRIES:
                outcome = _error("head_lookup_failed", outcome["message"])
        # A retry backs off from when the lookup returned: a lookup that used its whole
        # deadline must not leave a due timer behind and re-run within this tick.
        later = self._clock() if guard else now
        code = outcome["code"] if outcome is not None else None
        if code == HEAD_BLOCKED:
            self._wake_blocked(plan, st, new, nst, later)
        elif code == HEAD_RETRY:
            nst["deadline"] = _iso(later + timedelta(seconds=BLOCKED_RETRY_S))
            nst["lookup_retries"] = retries
            # the lookup was admitted: a later refusal starts a new queue spell
            nst.update(lookup_blocked=0, lookup_blocked_since=None)
            _record(new, later, self.host, "wait_retry", st["key"])
        elif outcome is None:
            nst.update(status="succeeded", outputs={}, error=None)
            _record(new, now, self.host, "wait_done", st["key"])
        elif code == SUPERSEDED:
            _supersede_wake(new, nst, outcome, now, self.host, st["key"])
        else:
            nst.update(status="failed", error=outcome)
            _record(new, now, self.host, "failed", st["key"])
        return self._cas(doc, new)

    def _wake_blocked(
        self, plan: _Plan, st: Mapping, new: dict, nst: dict, later: datetime
    ) -> None:
        """A guarded wake whose lookup was refused (``blocked``): fail it ``queue_timeout``
        when the lookup itself ran past the queue bound, else back off like a blocked step
        and record the first refusal only (the counter tracks repeats; history stays
        bounded)."""
        blocked = int(st.get("lookup_blocked") or 0) + 1
        since = _parse(st.get("lookup_blocked_since")) or later
        nst["lookup_blocked_since"] = _iso(since)
        expired = _lookup_queue_expired(plan, nst, later)
        if expired is not None:  # the lookup itself ran past the bound
            nst.update(status="failed", error=expired)
            _record(new, later, self.host, QUEUE_TIMEOUT, st["key"])
            return
        nst["deadline"] = _iso(later + timedelta(seconds=_blocked_delay(blocked)))
        nst["lookup_blocked"] = blocked
        if blocked == 1:
            _record(new, later, self.host, "wait_blocked", st["key"])
        else:
            _bump(new)

    def _guard_eligible(
        self, plan: _Plan, step: Step | None, guard: Mapping, now: datetime
    ) -> bool | PlacementError:
        """Whether this node may perform the guarded lookup: the step's placement, else the
        guard actor's machine (where its App credentials live), resolved as dispatch does.
        A placement that resolves nowhere is answered as its :class:`PlacementError` (to a
        node that is not drained), see :meth:`_guard_unplaceable`."""
        if self._drained():
            return False
        placement = step.placement if step is not None else None
        if placement is None:
            actor_id = guard.get("actor") or _action_actor(plan.rule.action)
            adoc = self._store.get(ACTORS_COLLECTION, actor_id) if actor_id else None
            enabled = adoc and not adoc.get("deleted_at") and adoc.get("enabled") is not False
            if enabled and adoc.get("machine"):  # a disabled actor is left to the router
                placement = Placement(actor=actor_id)
        if placement is None:
            return True
        target = self._target_of(placement, now)
        return target if isinstance(target, PlacementError) else target == self.host

    def _guard_unplaceable(
        self, doc: Document, st: Mapping, error: PlacementError, wake: datetime, now: datetime
    ) -> bool | None:
        """A due guarded wake whose lookup host resolves nowhere (review #17 finding 1).

        Waiting cannot fix a fatal placement: the step fails with its code. A temporarily
        unavailable host (offline, drained) is waited for, its ``placement_error`` recorded
        once, until :data:`PLACEMENT_ABANDON_AFTER` past the wake; then the step fails
        ``placement_unavailable`` - never proceeds as if the head were unchanged, and never
        sleeps forever holding the run's concurrency key. Any node may do it (CAS). None:
        nothing to write now."""
        key = st["key"]
        if error.code in FATAL_PLACEMENT:
            return self._fail_now(doc, key, _error(error.code, error.message), now)
        if now - wake >= PLACEMENT_ABANDON_AFTER:
            message = f"guard lookup host unavailable since the wake: {error.message}"
            return self._fail_now(doc, key, _error(PLACEMENT_UNAVAILABLE, message), now)
        recorded = _error(error.code, error.message)
        if st.get("placement_error") == recorded:
            return None
        new, nst = _copy_with(doc, key)
        nst["placement_error"] = recorded
        _record(new, now, self.host, "placement_waiting", key)
        return self._cas(doc, new)

    def _check_guard(
        self, plan: _Plan, doc: Mapping, step: Step | None, guard: Mapping[str, Any]
    ) -> dict[str, str] | None:
        """None when the PR head is unchanged; else the error (``superseded`` when it moved,
        a failure code when it cannot be told - never None on doubt)."""
        expected = _guard_expected(plan, doc, guard.get("ref"))
        if not isinstance(expected, str) or not expected:
            return _error("guard_unresolved", f"guard ref {guard.get('ref')!r} names no head sha")
        repo, number = _guard_target(doc, guard)
        if not isinstance(repo, str) or number is None:
            return _error("head_lookup_failed", "guard has no repo and PR number to look up")
        actor = guard.get("actor") or _action_actor(plan.rule.action)
        try:
            current = self._lookup_head(doc, step, actor, repo, number)
        except _HeadBlocked as exc:
            return _error(HEAD_BLOCKED, str(exc))
        except _HeadRetry as exc:
            return _error(HEAD_RETRY, f"could not read the PR head in time: {exc}")
        except Exception as exc:  # noqa: BLE001 - any lookup failure is fail-safe
            log.warning("head lookup failed for %s#%s: %s", repo, number, type(exc).__name__)
            return _error("head_lookup_failed", f"could not read the PR head: {exc}")
        if not isinstance(current, str) or not current:
            return _error("head_lookup_failed", "the head lookup returned no sha")
        if current != expected:
            return _error(SUPERSEDED, f"PR head moved from {expected} to {current}")
        return None

    def _lookup_head(
        self, doc: Mapping, step: Step | None, actor: str | None, repo: str, number: int
    ) -> str | None:
        if self._head_lookup is not None:
            return self._head_lookup(actor, repo, number)
        ctx = InvocationContext(
            run_id=doc["id"],
            step_id=step.id if step else "",
            kind="action",
            host=self.host,
            actor=actor,
            config={"kind": "github.pr_head", "params": {"actor": actor} if actor else {}},
        )
        ports = self._ports
        if callable(ports) and not isinstance(ports, Mapping):
            port = ports(ctx)
        else:
            port = ports.get(HEAD_LOOKUP_PORT)
        if port is None:
            raise RuntimeError("no head lookup configured")
        deadline = self._clock() + timedelta(seconds=HEAD_LOOKUP_TIMEOUT_S)
        res = port.invoke(
            {"repo": repo, "number": number},
            idempotency_key(doc["id"], ctx.step_id),
            deadline,
            context=ctx,
        )
        if res.outcome == BLOCKED:
            raise _HeadBlocked(res.error or "blocked")
        if res.outcome == FAILED and res.retryable and res.error in _HEAD_RETRY_CODES:
            raise _HeadRetry(res.error)
        if res.outcome != COMPLETED:
            raise RuntimeError(res.error or res.outcome)
        return res.output.get("head_sha")

    def _cas(self, before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
        """Write ``after`` over ``before`` by compare-and-set on ``rev``. A transition that
        finishes the run also inserts its completion record, in the same transaction
        (:mod:`culture_rules.engine.run_completions`): both commit or neither does."""
        if after.get("status") in RUN_DONE and before.get("status") not in RUN_DONE:
            with self._store.transaction() as tx:
                res = tx.update_if(
                    RUNS_COLLECTION, before["id"], {"rev": before["rev"]}, _mutable(after)
                )
                if res.won:
                    record_completion(tx, before, after, self._clock())
            return res.won
        res = self._store.update_if(
            RUNS_COLLECTION, before["id"], {"rev": before["rev"]}, _mutable(after)
        )
        return res.won

    # ------------------------------------------------------------------ dispatch

    def _drained(self) -> bool:
        return self.host in drained_machines(self._store)

    def _dispatch_one(self, plan: _Plan, doc: Document, now: datetime) -> bool:
        drained = self._drained()
        for st in doc["steps"]:
            if st["status"] == "dispatching":
                if self._dispatch(plan, doc, st, now, resume=True):
                    return True
            elif (
                st["status"] == "pending"
                and not drained
                and _ready(plan, doc, st)
                and _when(plan, doc, st) in (None, True)  # false/invalid: housekeeping's
            ):
                if self._dispatch(plan, doc, st, now, resume=False):
                    return True
        return False

    def _target(self, plan: _Plan, st: Mapping[str, Any], now: datetime) -> Any:
        placement = self._placement(plan, st)
        if placement is None:
            return self.host
        return self._target_of(placement, now)

    def _placement(self, plan: _Plan, st: Mapping[str, Any]) -> Placement | None:
        """Where a step must run: its own placement, else - for the rule action and a
        built-in action step - its actor's machine (see :meth:`_action_placement`). Fresh
        and resumed dispatches both read it, so recovery never leaves that machine."""
        step = plan.step(st)
        placement = step.placement if step is not None else None
        if placement is None and st["key"] in TERMINAL_STEPS:
            placement = self._action_placement(_action_actor(_terminal_action(plan, st)))
        elif placement is None and (spec := _step_action(plan, st)) is not None:
            placement = self._action_placement(_params_actor(spec.get("params")))
        return placement

    def _target_of(self, placement: Placement, now: datetime) -> Any:
        online = online_machines(self._store, now, beat_every=self._beat_every)
        drained = drained_machines(self._store)
        machines = enrolled_machines(self._store)
        states = [MachineState(m.name, m.name in online, m.name in drained) for m in machines]
        actors = [Actor.from_dict(d, strict=False) for d in self._store.find(ACTORS_COLLECTION)]
        resolved = resolve_placement(placement, machines, states, actors)
        return resolved if isinstance(resolved, PlacementError) else resolved.machine

    def _action_placement(self, actor_id: str | None) -> Placement | None:
        """An action through an enabled actor that lives on a machine runs on that machine
        (its app credentials are injected there); an unknown or disabled actor is left to
        the router, which fails the run with ``actor_unavailable``."""
        doc = self._store.get(ACTORS_COLLECTION, actor_id) if actor_id else None
        if not doc or doc.get("deleted_at") or doc.get("enabled") is False:
            return None
        return Placement(actor=actor_id) if doc.get("machine") else None

    def _port(self, ctx: InvocationContext) -> ActorPort | None:
        if callable(self._ports) and not isinstance(self._ports, Mapping):
            return self._ports(ctx)
        ports = self._ports
        if ctx.kind == "action":
            kind = ctx.config.get("kind")
            return ports.get(f"action:{kind}") or ports.get("action") or ports.get("*")
        return ports.get(ctx.kind) or ports.get("*")

    def _fail_now(self, doc: Document, key: str, error: dict, now: datetime) -> bool:
        new = copy.deepcopy(doc)
        st = step_state(new, key)
        st["status"] = "failed"
        st["error"] = error
        _record(new, now, self.host, "failed", key)
        return self._cas(doc, new)

    def _dispatch(
        self, plan: _Plan, doc: Document, st: dict, now: datetime, *, resume: bool
    ) -> bool:
        key = st["key"]
        if not resume and key in TERMINAL_STEPS and _terminal_action(plan, st).only_at_chain_end:
            continuing = self._chain_continues(doc, st, now)
            if continuing:
                return self._skip_terminal(doc, key, continuing, now)
        if resume:
            inputs = self._resume_inputs(plan, st)
        else:
            inputs = self._fresh_inputs(plan, doc, st, now)
        if isinstance(inputs, bool):  # no inputs: this is the dispatch's answer
            return inputs
        attempt = st["attempt"] if (resume or st.get("resume")) else st["attempt"] + 1
        ctx = self._context(plan, doc, st, attempt)
        port = self._port(ctx)
        if port is None:
            return self._fail_now(
                doc, key, _error("no_actor_port", f"no actor port for {ctx.kind!r}"), now
            )
        guard = self._reclaim_guard(st, now)
        if _outcome_unknown(st, resume) and not self._key_safe(plan, st, port):
            return self._refuse_unsafe(doc, key, now, guard)
        claim = self._claims.claim_step(doc["id"], key, may_reclaim=guard)
        if not claim.won:
            return False
        deadline = self._deadline(plan, st, now, resume)
        polling = not resume and _in_queue(st)  # a repeat ask of queued work: no history
        if not self._mark_dispatching(doc, key, attempt, inputs, deadline, now, silent=polling):
            self._claims.release(claim)
            return False
        idem = idempotency_key(doc["id"], key)
        with self._keep_alive(claim, deadline):
            result = _invoke(port, inputs, idem, deadline, ctx)
        self._settle(doc["id"], key, attempt, claim, result, port)
        return True

    def _chain_continues(self, doc: Document, st: Mapping[str, Any], now: datetime) -> list[str]:
        """The live rules that would continue this run's chain (d21): fire on the event the
        run emits once this terminal step is done, on the run's concurrency key
        (:func:`~culture_rules.engine.chain_hold.continuations`)."""
        from culture_rules.engine.chain_hold import continuations  # noqa: PLC0415
        from culture_rules.engine.run_completions import build_run_event  # noqa: PLC0415

        ending = copy.deepcopy(dict(doc))
        if st["key"] == FAILURE_STEP:
            ending.update(status="failed", error=st.get("failure"))
        else:
            ending["status"] = "succeeded"
        ending["finished_at"] = _iso(now)
        envelope = build_run_event(ending)
        if envelope is None:
            return []
        return continuations(self._store, envelope, doc.get("concurrency_key"))

    def _skip_terminal(self, doc: Document, key: str, continuing: list[str], now: datetime) -> bool:
        """Skip an ``only_at_chain_end`` terminal action: the chain goes on (d21)."""
        new = copy.deepcopy(doc)
        nst = step_state(new, key)
        nst.update(status="skipped", outputs={"chain_continues": list(continuing)})
        _record(new, now, self.host, "skipped:chain_continues", key)
        return self._cas(doc, new)

    def _keep_alive(self, claim: ClaimResult, deadline: datetime) -> AbstractContextManager[Any]:
        """The keeper renewing ``claim`` while its actor runs, until ``deadline``."""

        def renew() -> bool:
            if self._clock() >= deadline:
                return False  # past the deadline: let the lease lapse (the step timed out)
            return self._claims.renew(claim).won

        return self._lease_keeper(renew, self._claims.lease.total_seconds() / 3)

    def _reclaim_guard(self, st: Mapping[str, Any], now: datetime) -> ReclaimGuard | None:
        """Who may take over a lapsed claim on ``st``: for a step another host is
        dispatching, only a host that sees the holder offline or the deadline passed."""
        holder = st.get("host")
        if st["status"] != "dispatching" or holder in (None, self.host):
            return None
        deadline = _parse(st.get("deadline"))

        def may_reclaim(_claim: Document) -> bool:
            if deadline is not None and now >= deadline:
                return True
            return not self._machine_online(holder, now)

        return may_reclaim

    def _machine_online(self, machine: str, now: datetime) -> bool:
        """Whether ``machine`` heartbeated within ``holder_offline_after`` of ``now``.

        No heartbeat doc: offline. A doc with an unparseable ``ts``: online (fail safe, no
        takeover), logged."""
        beat = self._store.get(HEARTBEAT_COLLECTION, machine)
        if beat is None:
            return False  # never beat (or doc gone): offline, takeover allowed once lapsed
        try:
            ts = datetime.fromisoformat(str(beat.get("ts")))
        except ValueError:
            # a present but unreadable beat is not proof of death: no takeover
            log.warning("heartbeat for %s has an unparseable ts %r", machine, beat.get("ts"))
            return True
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return now - ts < self._holder_offline_after

    def _deadline(self, plan: _Plan, st: Mapping, now: datetime, resume: bool) -> datetime:
        """A resumed attempt (it may have started) keeps its deadline; any other dispatch -
        a new attempt, or a re-ask of blocked work that never started - gets the step's
        full working budget from now, so time spent queued never eats the work's time."""
        if resume:
            return _parse(st["deadline"]) or now
        return now + timedelta(seconds=self._timeout(plan, st))

    def _resume_inputs(self, plan: _Plan, st: dict) -> dict | bool:
        """The inputs a resumed step re-runs with, or False when it resumes elsewhere."""
        placed = self._placement(plan, st) is not None  # own or actor-derived placement
        if st.get("host") != self.host and (placed or self._drained()):
            return False  # a placed step resumes only on its host
        return st["inputs"] or {}

    def _fresh_inputs(self, plan: _Plan, doc: Document, st: dict, now: datetime) -> dict | bool:
        """The resolved inputs of a step placed here, or the dispatch's answer (a bool)."""
        key = st["key"]
        target = self._target(plan, st, now)
        if isinstance(target, PlacementError):
            return self._placement_failed(doc, st, target, now)
        if target != self.host:
            return False
        resolved = _step_inputs(plan, doc, st)
        if "inputs" not in resolved:
            return self._fail_now(doc, key, resolved, now)
        spec = _step_action(plan, st)
        if spec is not None:  # an action step runs with its params resolved, as the action
            return _action_step_params(spec, resolved["inputs"])
        return resolved["inputs"]

    def _placement_failed(
        self, doc: Document, st: dict, target: PlacementError, now: datetime
    ) -> bool:
        key = st["key"]
        error = _error(target.code, target.message)
        if target.code in FATAL_PLACEMENT:
            return self._fail_now(doc, key, error, now)
        if st.get("placement_error") == error:
            return False
        new = copy.deepcopy(doc)
        step_state(new, key)["placement_error"] = error
        _record(new, now, self.host, "placement_waiting", key)
        return self._cas(doc, new)

    def _refuse_unsafe(
        self, doc: Document, key: str, now: datetime, guard: ReclaimGuard | None
    ) -> bool:
        """Fail a step whose outcome is unknown on a target that cannot deduplicate."""
        claim = self._claims.claim_step(doc["id"], key, may_reclaim=guard)
        if not claim.won:
            return False
        self._claims.release(claim)
        return self._fail_now(
            doc,
            key,
            _error("unsafe_retry", "outcome unknown and the target cannot deduplicate"),
            now,
        )

    def _mark_dispatching(
        self,
        doc: Document,
        key: str,
        attempt: int,
        inputs: dict,
        deadline: datetime,
        now: datetime,
        *,
        silent: bool = False,
    ) -> bool:
        new = copy.deepcopy(doc)
        nst = step_state(new, key)
        nst.update(
            status="dispatching",
            host=self.host,
            attempt=attempt,
            inputs=inputs,
            deadline=_iso(deadline),
            next_attempt_at=None,
            placement_error=None,
            resume=False,
        )
        if silent:
            _bump(new)
        else:
            _record(new, now, self.host, "dispatched", key)
        return self._cas(doc, new)

    def _context(self, plan: _Plan, doc: Document, st: Mapping, attempt: int) -> InvocationContext:
        if st["key"] in TERMINAL_STEPS:
            act = _terminal_action(plan, st)
            config = {"kind": act.kind, "name": act.name, "params": dict(act.params)}
            return InvocationContext(
                doc["id"], st["key"], "action", self.host, attempt, _action_actor(act), config
            )
        spec = _step_action(plan, st)
        if spec is not None:  # routed exactly like the rule action (see the module docstring)
            params = dict(spec.get("params") or {})
            config = {"kind": spec.get("kind"), "name": spec.get("name", ""), "params": params}
            return InvocationContext(
                doc["id"], st["key"], "action", self.host, attempt, _params_actor(params), config
            )
        step = plan.step(st)
        actor = step.placement.actor if step.placement is not None else None
        return InvocationContext(
            doc["id"], st["key"], step.kind, self.host, attempt, actor, dict(step.config)
        )

    def _policy(self, plan: _Plan, st: Mapping) -> tuple[RetryPolicy, float | None, bool]:
        if st["key"] in TERMINAL_STEPS:
            act: Action = _terminal_action(plan, st)
            return _retry_of(act.retry), act.timeout_s, act.idempotent
        step = plan.step(st)
        # only a boolean true counts: a "false" string that slipped past validation fails
        # closed (no blind retry), never truthy
        idempotent = step.config.get("idempotent") is True or spec_idempotent(action_spec(step))
        return _retry_of(step.retry), step.timeout_s, idempotent

    def _timeout(self, plan: _Plan, st: Mapping) -> float:
        return _timeout_of(plan, st)

    def _key_safe(self, plan: _Plan, st: Mapping, port: Any) -> bool:
        return bool(getattr(port, "supports_idempotency_key", True)) or self._policy(plan, st)[2]

    def _settle(
        self,
        run_id: str,
        key: str,
        attempt: int,
        claim: Any,
        result: InvocationResult | None,
        port: Any,
    ) -> None:
        """Record an invocation's outcome and finish/release its claim, atomically."""
        for _ in range(_MAX_CAS_RETRIES):
            try:
                with self._store.transaction() as tx:
                    claims = self._claims.with_ops(tx)
                    doc = tx.get(RUNS_COLLECTION, run_id)
                    st = step_state(doc, key) if doc else None
                    if not _still_dispatching(doc, st, attempt, self.host):
                        claims.release(claim)  # cancelled, delivered or moved on meanwhile
                        return
                    new, nst, now = self._settled(doc, st, key, result, port)
                    res = tx.update_if(RUNS_COLLECTION, run_id, {"rev": doc["rev"]}, _mutable(new))
                    if not res.won:
                        raise _Conflict
                    record_completion(tx, doc, new, now)
                    if nst["status"] in STEP_DONE:
                        claims.complete(claim)
                    else:
                        claims.release(claim)
                return
            except _Conflict:
                continue
        raise RunError("contention", f"could not record {key} of {run_id}")  # pragma: no cover

    def _settled(
        self, doc: Mapping, st: Mapping, key: str, result: InvocationResult | None, port: Any
    ) -> tuple[dict, dict, datetime]:
        """``doc`` with the invocation's outcome applied to step ``key``, that step, and the
        time it was applied at (a run it finishes records its completion at that time)."""
        plan = _Plan.of(doc)
        now = self._clock()
        new = copy.deepcopy(doc)
        nst = step_state(new, key)
        _apply(plan, nst, result, now, self._key_safe(plan, st, port))
        if nst["status"] == "blocked" and _in_queue(st):
            _bump(new)  # still queued: counted on the step, not in history
        else:
            _record(new, now, self.host, nst["status"], key)
        return new, nst, now

    # ------------------------------------------------------------------ events

    def deliver(
        self, idempotency_key: str, result: InvocationResult, *, attempt: int | None = None
    ) -> bool:
        """Record the completion (or failure) of accepted work reported later by an event.

        Returns True iff the result changed the run. Results for finished steps,
        cancelled or finished runs, and unknown keys are ignored (False). With ``attempt``,
        a result for an attempt other than the step's current one is ignored too (checked
        inside the compare-and-set, so a retry dispatched meanwhile is never finished by an
        older attempt's result).
        """
        claim = self._claims.get(idempotency_key)
        if claim is None or claim.get("kind") != "step":
            return False
        run_id, key = claim["run_id"], claim["step_id"]
        if result.outcome in (ACCEPTED, BLOCKED):
            return False
        for _ in range(_MAX_CAS_RETRIES):
            doc = self._store.get(RUNS_COLLECTION, run_id)
            st = step_state(doc, key) if doc else None
            if not _deliverable(doc, st, attempt):
                return False
            plan = _Plan.of(doc)
            now = self._clock()
            new = copy.deepcopy(doc)
            nst = step_state(new, key)
            if nst["attempt"] == 0:
                nst["attempt"] = 1
            _apply(plan, nst, result, now, True)
            _record(new, now, self.host, f"delivered:{nst['status']}", key)
            if self._cas(doc, new):
                return True
        raise RunError("contention", f"could not deliver to {key} of {run_id}")  # pragma: no cover


# --------------------------------------------------------------------------- pure transitions


_WAKE_GO = object()
"""A due guarded wake this node may perform now (:meth:`Executor._guard_gate`)."""
_WAKE_SKIP = object()
"""A due guarded wake left for now: paused, not this node's, or nothing to write."""


def _due_wake(st: Mapping, now: datetime) -> datetime | None:
    """The wake time of a sleeping step that is due at ``now``, else None."""
    wake = _parse(st.get("deadline"))
    if st["status"] != SLEEPING or wake is None or now < wake:
        return None
    return wake


def _supersede_wake(
    new: dict, nst: dict, outcome: Mapping, now: datetime, host: str, key: str
) -> None:
    """End the run ``superseded``: the guarded wake's PR head moved during the wait."""
    nst.update(status="cancelled", error=outcome)
    for s in new["steps"]:
        if s["status"] not in STEP_DONE:
            s["status"] = "cancelled"
    new.update(status=SUPERSEDED, finished_at=_iso(now), error=None)
    _record(new, now, host, SUPERSEDED, key)


def _check_supported_steps(workflow: Workflow) -> None:
    """Refuse a step with a reserved id, a nested loop, or a wait inside a loop body."""
    for s in workflow.steps:
        if s.id in TERMINAL_STEPS or any(b.kind in LOOP_KINDS for b in s.body):
            raise RunError("unsupported_workflow", f"step {s.id!r}: reserved id or nested loop")
        if any(b.kind == "wait" for b in s.body):
            raise RunError("unsupported_workflow", f"step {s.id!r}: wait inside a loop body")


def _still_dispatching(doc: Mapping | None, st: Mapping | None, attempt: int, host: str) -> bool:
    """Whether ``st`` of active ``doc`` is still the ``attempt`` this ``host`` dispatched."""
    return not (
        doc is None
        or doc["status"] != ACTIVE
        or st is None
        or st["status"] != "dispatching"
        or st["attempt"] != attempt
        or st["host"] != host
    )


def _deliverable(doc: Mapping | None, st: Mapping | None, attempt: int | None) -> bool:
    """Whether a later-reported result may finish ``st``: the run is active, the step is not
    done, and (with ``attempt``) it is the step's current attempt - a newer attempt runs (or
    none ran yet) otherwise, so the result is not this one's."""
    if doc is None or doc["status"] != ACTIVE or st is None or st["status"] in STEP_DONE:
        return False
    return not (attempt is not None and st["attempt"] != attempt)


def _outcome_unknown(st: Mapping, resume: bool) -> bool:
    """Whether the step's last attempt may have run: a resume, or a timed-out attempt."""
    return resume or (st["attempt"] > 0 and (st.get("error") or {}).get("code") == "timeout")


def _invoke(
    port: ActorPort,
    inputs: dict,
    idem: str,
    deadline: datetime,
    ctx: InvocationContext,
) -> InvocationResult | None:
    """Invoke ``port``; None when it raised (no acknowledgement: the outcome is unknown)."""
    try:
        return port.invoke(inputs, idem, deadline, context=ctx)
    except Exception:  # no acknowledgement: the outcome is unknown
        return None


def _apply(
    plan: _Plan, st: dict, result: InvocationResult | None, now: datetime, key_safe: bool
) -> None:
    """Apply one invocation outcome to step state ``st`` (in place)."""
    if result is not None and result.outcome == BLOCKED:
        _queue_poll(plan, st, result, now)
        return
    if _in_queue(st):  # any other answer ends the queue spell (the queue record stays)
        st["queue"] = dict(st["queue"], left_at=_iso(now))
    if result is None:
        _attempt_failed(
            plan,
            st,
            _error("no_ack", "no acknowledgement from the actor"),
            now,
            retryable=True,
            unknown=True,
            key_safe=key_safe,
        )
        return
    if result.outcome == COMPLETED:
        outputs = dict(result.output)
        step = plan.step(st)
        problem = _check_ports(step.outputs, outputs, "output") if step is not None else None
        if problem:
            problem["code"] = "output_type_mismatch"
            st.update(status="failed", error=problem, outputs=outputs)
            return
        st.update(status="succeeded", outputs=outputs, error=None)
    elif result.outcome == ACCEPTED:
        st["status"] = "waiting"
    elif result.error == ACTOR_UNAVAILABLE:
        message = f"actor {_actor_of(plan, st)!r} is unknown or disabled"
        st.update(status="failed", error=_error(ACTOR_UNAVAILABLE, message))
    else:
        _attempt_failed(
            plan,
            st,
            _error("actor_failed", result.error or "failed"),
            now,
            retryable=result.retryable,
            unknown=False,
            key_safe=key_safe,
        )


def _queue_poll(plan: _Plan, st: dict, result: InvocationResult, now: datetime) -> None:
    """A ``blocked`` answer: the work never started and waits in the actor's queue.

    The first one opens a queue spell (``queue.since``, and ``queue.deadline`` =
    :func:`queue_limit_s` of the step's budget later); each one counts a poll and schedules
    the next ask with :func:`_blocked_delay`. The working ``deadline`` is cleared: the
    budget starts again in full at the dispatch the actor accepts."""
    queue = st["queue"] if _in_queue(st) else None
    if queue is None:
        limit = queue_limit_s(_timeout_of(plan, st))
        queue = {"since": _iso(now), "deadline": _iso(now + timedelta(seconds=limit)), "polls": 0}
    polls = int(queue.get("polls") or 0) + 1
    st.update(
        status="blocked",
        deadline=None,
        next_attempt_at=_iso(now + timedelta(seconds=_blocked_delay(polls))),
        error=_error("blocked", result.error or "actor is blocked"),
        queue=dict(queue, polls=polls, last_at=_iso(now), left_at=None),
    )


def _lookup_queue_expired(plan: _Plan, st: Mapping, now: datetime) -> dict | None:
    """The ``queue_timeout`` error of a guarded wait whose head lookup has been refused
    (``blocked``) since ``lookup_blocked_since`` for longer than the queue bound, else None.
    Checked in housekeeping (any node, drained or not, before any lookup) and after a
    refused lookup."""
    since = _parse(st.get("lookup_blocked_since"))
    if since is None:
        return None
    limit = queue_limit_s(_timeout_of(plan, st))
    if now < since + timedelta(seconds=limit):
        return None
    return _error(
        QUEUE_TIMEOUT,
        f"the head lookup's actor refused (blocked) from {_iso(since)} past the queue bound "
        f"of {limit:g} s ({st.get('lookup_blocked')} refusals)",
    )


def _timeout_of(plan: _Plan, st: Mapping) -> float:
    """The step's (or the terminal action's) working budget in seconds."""
    if st["key"] in TERMINAL_STEPS:
        timeout = _terminal_action(plan, st).timeout_s
    else:
        step = plan.step(st)
        timeout = step.timeout_s if step is not None else None
    return timeout or DEFAULT_TIMEOUT_S


def _actor_of(plan: _Plan, st: Mapping) -> str | None:
    """The actor a step (or the rule action) names, for messages."""
    if st["key"] in TERMINAL_STEPS:
        return _action_actor(_terminal_action(plan, st))
    spec = _step_action(plan, st)
    if spec is not None:
        return _params_actor(spec.get("params"))
    step = plan.step(st)
    return step.placement.actor if step is not None and step.placement is not None else None


def _terminal_action(plan: _Plan, st: Mapping) -> Action:
    """The rule's action behind a terminal step: ``action``, or ``on_failure`` (d16)."""
    if st["key"] == FAILURE_STEP and plan.rule.on_failure is not None:
        return plan.rule.on_failure
    return plan.rule.action


def _action_actor(action: Action) -> str | None:
    """The actor id a rule action names in ``params.actor`` (a literal, never resolved)."""
    return _params_actor(action.params)


def _params_actor(params: Any) -> str | None:
    """The literal actor id in an action's ``params.actor`` (never resolved), else None."""
    actor = params.get("actor") if isinstance(params, Mapping) else None
    return actor if isinstance(actor, str) and actor else None


def _step_action(plan: _Plan, st: Mapping) -> Mapping[str, Any] | None:
    """The ``config.action`` of a built-in action step state, else None (d12)."""
    if st["key"] in TERMINAL_STEPS:
        return None
    step = plan.step(st)
    return action_spec(step) if step is not None else None


def _action_step_params(spec: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    """An action step's params resolved as the rule action's are (see :func:`_finish`),
    against the step's input ports (the ``inputs`` namespace) only."""
    return resolve_refs(dict(spec.get("params") or {}), {"inputs": dict(inputs)})


def _attempt_failed(
    plan: _Plan,
    st: dict,
    error: dict,
    now: datetime,
    *,
    retryable: bool,
    unknown: bool,
    key_safe: bool,
) -> None:
    policy = _policy_of(plan, st)
    if retryable and st["attempt"] < policy.max_attempts:
        if unknown and not key_safe:
            st.update(
                status="failed",
                error=_error(
                    "unsafe_retry", f"{error['message']}; the target cannot deduplicate a retry"
                ),
            )
            return
        delay = policy.backoff_s * policy.backoff_multiplier ** (st["attempt"] - 1)
        st.update(
            status="retry_wait", error=error, next_attempt_at=_iso(now + timedelta(seconds=delay))
        )
        return
    st.update(status="failed", error=error)


def _policy_of(plan: _Plan, st: Mapping) -> RetryPolicy:
    if st["key"] in TERMINAL_STEPS:
        return _retry_of(_terminal_action(plan, st).retry)
    step = plan.step(st)
    return _retry_of(step.retry if step is not None else None)


def _ready(plan: _Plan, doc: Mapping, st: Mapping) -> bool:
    """Whether a pending, dispatchable step's predecessors are all done."""
    if st["key"] in TERMINAL_STEPS:
        return True
    step = plan.step(st)
    if step is None or step.kind in LOOP_KINDS or step.kind == "wait" or not step.enabled:
        return False
    loop = st.get("loop")
    if loop:
        return _earlier_iteration_steps_ok(doc, st, loop)
    return _deps_done(plan, doc, step.id)


def _earlier_iteration_steps_ok(doc: Mapping, st: Mapping, loop: Mapping) -> bool:
    """Whether every body step before ``st`` in its loop iteration is done."""
    for other in doc["steps"]:
        if other is st or other["key"] == st["key"]:
            break
        same = other.get("loop") or {}
        if same.get("parent") == loop["parent"] and same.get("iteration") == loop["iteration"]:
            if other["status"] not in STEP_OK:
                return False
    return True


def _deps_done(plan: _Plan, doc: Mapping, step_id: str) -> bool:
    for e in plan.edges_into(step_id):
        source = plan.body_parent.get(e.source, e.source)
        if source == "inputs":
            continue
        dep = step_state(doc, source)
        if dep is None or dep["status"] not in STEP_OK:
            return False
    return True


#: History events that never make a pending step due (it was already pending and waiting).
_NOT_READINESS = frozenset({"placement_waiting"})


def _dependency_keys(plan: _Plan, doc: Mapping, st: Mapping) -> set[str]:
    """Keys whose last change can be what made ``st`` ready (see :func:`_ready`)."""
    loop = st.get("loop")
    if loop:
        keys = {loop["parent"]}
        for other in doc["steps"]:
            if other["key"] == st["key"]:
                break
            same = other.get("loop") or {}
            if same.get("parent") == loop["parent"] and same.get("iteration") == loop["iteration"]:
                keys.add(other["key"])
        return keys
    step = plan.step(st)
    if step is None:
        return set()
    return {
        plan.body_parent.get(e.source, e.source)
        for e in plan.edges_into(step.id)
        if e.source != "inputs"
    }


def _ready_since(plan: _Plan, doc: Mapping, st: Mapping) -> datetime | None:
    """When pending ``st`` became ready: the latest change of the step itself (it became
    pending) or of one of its dependencies (the last one to finish), else the run's start."""
    deps = _dependency_keys(plan, doc, st)
    history = doc.get("history") or []
    since = _parse(doc.get("created_at")) or (_parse(history[0].get("at")) if history else None)
    for h in history:
        key = h.get("step")
        if key == st["key"] and h.get("event") in _NOT_READINESS:
            continue
        if key == st["key"] or key in deps:
            since = _later_of(since, _parse(h.get("at")))
    if _in_queue(st):  # a queued step's repeat polls are dated on the step, not in history
        since = _later_of(since, _parse(st["queue"].get("last_at")))
    return since


def _later_of(since: datetime | None, at: datetime | None) -> datetime | None:
    """``at`` when it is later than ``since`` (or ``since`` is unknown), else ``since``."""
    if at is not None and (since is None or at > since):
        return at
    return since


def due_steps(doc: Mapping[str, Any], now: datetime) -> list[tuple[str, datetime | None]]:
    """Work of a live run the executor would act on now, each with when it became due.

    A ``pending`` step counts only when it is ready to dispatch (:func:`_ready`, the same
    predicate dispatch uses) and is due since it became ready; a ``retry_wait`` step counts
    once ``next_attempt_at`` has passed. Finished runs have no due work.
    """
    if doc.get("status") != ACTIVE:
        return []
    found: list[tuple[str, datetime | None]] = []
    plan: _Plan | None = None
    for st in doc.get("steps") or ():
        status = st.get("status")
        if status == "pending":
            plan = plan or _Plan.of(doc)
            if _ready(plan, doc, st):
                found.append((st["key"], _ready_since(plan, doc, st)))
            continue
        timed = _due_timer(st, status, now)
        if timed is not None:
            found.append(timed)
    return found


def _due_timer(st: Mapping, status: Any, now: datetime) -> tuple[str, datetime | None] | None:
    """A ``retry_wait`` step past ``next_attempt_at`` (or with none), or a ``sleeping`` one
    past its ``deadline``, with when it became due; else None."""
    if status == "retry_wait":
        due = _parse(st.get("next_attempt_at"))
        if due is None or due <= now:
            return (st["key"], due)
    elif status == SLEEPING:
        due = _parse(st.get("deadline"))
        if due is not None and due <= now:
            return (st["key"], due)
    return None


def _latest_output(doc: Mapping, plan: _Plan, step_id: str) -> Mapping[str, Any]:
    if step_id in plan.body_parent:  # a body step read from outside: its latest iteration
        for st in reversed(doc["steps"]):
            if st.get("def") == step_id and st.get("loop") and st["status"] == "succeeded":
                return st.get("outputs") or {}
        return {}
    dep = step_state(doc, step_id)
    return (dep or {}).get("outputs") or {}


def _edge_source(
    plan: _Plan, doc: Mapping, source: str, loop: Mapping | None, loop_state: Mapping | None
) -> Mapping[str, Any]:
    """The values an edge from ``source`` reads (inside ``loop``'s iteration, when given)."""
    if source == "inputs":
        return doc.get("inputs") or {}
    if loop and source == loop["parent"]:
        return (loop_state or {}).get("inputs") or {}
    if loop and plan.body_parent.get(source) == loop["parent"]:
        return (step_state(doc, step_key(loop["parent"], loop["iteration"], source)) or {}).get(
            "outputs"
        ) or {}
    return _latest_output(doc, plan, source)


def _implicit_loop_inputs(
    loop: Mapping, loop_state: Mapping, loop_step: Step | None = None
) -> dict[str, Any]:
    """The loop's own inputs plus ``iteration``/``index`` (and ``item`` for for_each).

    A ``retry_until`` loop's ``config["carry"]`` (``{input name: result field}``) feeds the
    previous iteration's result (the final body outputs ``until`` read) into the next one:
    from iteration 1 on, each named input takes that field, overriding a loop input of the
    same name; iteration 0 sees only the loop's inputs."""
    implicit = dict(loop_state.get("inputs") or {})
    i = loop["iteration"]
    implicit.update(iteration=i, index=i)
    items = loop_state.get("items")
    if isinstance(items, list) and i < len(items):
        implicit["item"] = items[i]
    carry = loop_step.config.get("carry") if loop_step is not None else None
    results = loop_state.get("results") or []
    if loop_step is not None and loop_step.kind == "retry_until" and isinstance(carry, dict):
        previous = results[i - 1] if 0 < i <= len(results) else None
        if isinstance(previous, Mapping):
            implicit.update(_carried(carry, previous))
    return implicit


def _carried(carry: Mapping, previous: Mapping) -> dict[str, Any]:
    """The inputs a ``retry_until`` loop's ``carry`` (``{input name: result field}``) takes
    from the previous iteration's result: string pairs whose field the result has."""
    return {
        name: previous[field_name]
        for name, field_name in carry.items()
        if isinstance(name, str) and isinstance(field_name, str) and field_name in previous
    }


def _step_inputs(plan: _Plan, doc: Mapping, st: Mapping) -> dict[str, Any]:
    """``{"inputs": {...}}`` for a step about to run, or an error dict."""
    if st["key"] in TERMINAL_STEPS:
        return {"inputs": dict(st.get("inputs") or {})}
    step = plan.step(st)
    values = _gathered_inputs(plan, doc, st)
    problem = _check_ports(step.inputs, values, "input")
    return problem or {"inputs": values}


def _gathered_inputs(plan: _Plan, doc: Mapping, st: Mapping) -> dict[str, Any]:
    """The values a body or top-level step's edges (and a loop's implicit inputs) give it,
    before any port check."""
    step = plan.step(st)
    values: dict[str, Any] = {}
    loop = st.get("loop")
    loop_state = step_state(doc, loop["parent"]) if loop else None
    for e in plan.edges_into(step.id):
        src = _edge_source(plan, doc, e.source, loop, loop_state)
        if e.source_port in src:
            values[e.target_port] = src[e.source_port]
    if loop and loop_state is not None:
        implicit = _implicit_loop_inputs(loop, loop_state, plan.top.get(loop["parent"]))
        values.update(
            (p.name, implicit[p.name])
            for p in step.inputs
            if p.name not in values and p.name in implicit
        )
    return values


WHEN_INVALID = "when_invalid"
"""Failure code of a step whose ``config.when`` cannot be evaluated."""
EXPLAIN_MAX_CHARS = 2000
"""How much of a ``retry_until`` loop's ``explain`` field its failure message keeps."""


def _when(plan: _Plan, doc: Mapping, st: Mapping) -> bool | dict | None:
    """A step's ``config.when`` over its gathered inputs: ``True``/``False``, an error dict
    when it cannot be evaluated, or ``None`` when the step has no ``when``."""
    step = plan.step(st)
    config = step.config if step is not None and isinstance(step.config, dict) else {}
    if "when" not in config:
        return None
    values = _gathered_inputs(plan, doc, st)
    try:
        return bool(cond.evaluate(config["when"], {"trigger": values, "variables": values}))
    except (cond.ConditionError, TypeError, ValueError, AttributeError, KeyError) as exc:
        return _error(WHEN_INVALID, f"config.when cannot be evaluated: {exc}")


def _skip_when(plan: _Plan, doc: Mapping, now: datetime) -> Found:
    """Skip a ready step whose ``when`` is false (any node may: no placement is needed),
    or fail it ``when_invalid``; a true ``when`` leaves it to be dispatched."""
    for st in doc["steps"]:
        if st["status"] != "pending" or st["key"] in TERMINAL_STEPS:
            continue
        if not _ready(plan, doc, st):
            continue
        verdict = _when(plan, doc, st)
        if verdict is None or verdict is True:
            continue
        new, nst = _copy_with(doc, st["key"])
        if verdict is False:
            nst["status"] = "skipped"
            return new, "skipped", st["key"]
        nst.update(status="failed", error=verdict)
        return new, "failed", st["key"]
    return None


def _explained(step: Step, result: Mapping[str, Any], message: str) -> str:
    """``message`` plus the last result's ``config.explain`` field, capped, when it has text."""
    name = step.config.get("explain") if isinstance(step.config, dict) else None
    text = result.get(name) if isinstance(name, str) else None
    if not isinstance(text, str) or not text.strip():
        return message
    text = text.strip()
    if len(text) > EXPLAIN_MAX_CHARS:
        text = text[:EXPLAIN_MAX_CHARS] + "…"
    return f"{message}; last: {text}"


def _housekeep(plan: _Plan, doc: Mapping, now: datetime, host: str) -> dict | None:
    """Return ``doc`` with the first due bookkeeping transition applied, or None."""
    for fn in (
        _due_timers,
        _loop_progress,
        _run_failure,
        _skip_disabled,
        _skip_when,
        _wait_start,
        _loop_start,
        _finish,
    ):
        found = fn(plan, doc, now)
        if found is not None:
            new, event, key = found
            if event is None:
                _bump(new)
            else:
                _record(new, now, host, event, key)
            return new
    return None


Found = tuple[dict, str | None, str | None] | None


def _copy_with(doc: Mapping, key: str) -> tuple[dict, dict]:
    new = copy.deepcopy(dict(doc))
    return new, step_state(new, key)


def _due_timers(plan: _Plan, doc: Mapping, now: datetime) -> Found:
    """The first due timer: a working deadline, a queue bound, or a retry / re-ask time.

    The event ``None`` marks a write without a history entry (a queued step's repeat poll,
    see :func:`_housekeep`)."""
    adopted = _adopt_legacy_queue(plan, doc, now)
    if adopted is not None:
        return adopted
    for st in doc["steps"]:
        # parsed before any timer, as it always was: a malformed deadline raises, unwritten
        _parse(st.get("deadline"))
        for timer in (_lookup_queue_timer, _working_deadline, _queue_bound, _retry_or_reask):
            found = timer(plan, doc, st, now)
            if found is not None:
                return found
    return None


def _lookup_queue_timer(plan: _Plan, doc: Mapping, st: Mapping, now: datetime) -> Found:
    """A guarded wake queued on its lookup's actor: bounded whatever its node does."""
    if st["status"] == SLEEPING and (expired := _lookup_queue_expired(plan, st, now)):
        new, nst = _copy_with(doc, st["key"])
        nst.update(status="failed", error=expired)
        return new, QUEUE_TIMEOUT, st["key"]
    return None


def _working_deadline(plan: _Plan, doc: Mapping, st: Mapping, now: datetime) -> Found:
    """A ``waiting`` step past its working deadline times out (a retryable attempt)."""
    deadline = _parse(st.get("deadline"))
    if st["status"] == "waiting" and deadline is not None and now >= deadline:
        new, nst = _copy_with(doc, st["key"])
        # Re-invoking timed-out work reuses its key; dispatch refuses (unsafe_retry)
        # when the target cannot deduplicate, since housekeeping does not know the port.
        error = _error("timeout", "the step's deadline passed")
        _attempt_failed(plan, nst, error, now, retryable=True, unknown=True, key_safe=True)
        nst["resume"] = False
        return new, "timeout", st["key"]
    return None


def _queue_bound(plan: _Plan, doc: Mapping, st: Mapping, now: datetime) -> Found:
    """Queued work past its queue bound fails ``queue_timeout``.

    The bound holds while the step is blocked and while a re-poll left it pending (a drained
    or unavailable node may hold it there), never once it is dispatching: that work may have
    started, and its resumed dispatch settles it."""
    status = st["status"]
    queue = st.get("queue") or {}
    queued = status == "blocked" or (status == "pending" and _in_queue(st))
    bound = _parse(queue.get("deadline")) if queued else None
    if bound is None or now < bound:
        return None
    # Queued work never started (the outcome is known): fail it outright - a retry
    # would only queue again, and the queue bound is the explicit end of waiting.
    new, nst = _copy_with(doc, st["key"])
    last = (st.get("error") or {}).get("message")
    message = (
        f"waited in the actor's queue from {queue.get('since')} past its bound "
        f"({queue.get('polls')} asks; last answer: {last})"
    )
    nst.update(
        status="failed",
        error=_error(QUEUE_TIMEOUT, message),
        next_attempt_at=None,
        resume=False,
        queue=dict(queue, left_at=_iso(now)),
    )
    return new, QUEUE_TIMEOUT, st["key"]


def _retry_or_reask(plan: _Plan, doc: Mapping, st: Mapping, now: datetime) -> Found:
    """A ``retry_wait`` or ``blocked`` step past ``next_attempt_at`` returns to pending.

    The event ``None`` marks a queued step's repeat poll: dated on the step, not in
    history."""
    status = st["status"]
    due = _parse(st.get("next_attempt_at"))
    if status not in ("retry_wait", "blocked") or due is None or now < due:
        return None
    new, nst = _copy_with(doc, st["key"])
    nst.update(status="pending", next_attempt_at=None, resume=status == "blocked")
    if status == "retry_wait":
        return new, "retry_due", st["key"]
    if _in_queue(st):  # a repeat poll: dated on the step, not in history
        nst["queue"] = dict(st.get("queue") or {}, last_at=_iso(now))
        return new, None, st["key"]
    return new, "unblocked", st["key"]


QUEUE_ADOPTED = "queue_adopted"
"""History event of a queued step or guarded wake an older engine left without a queue
record, given one on upgrade (:func:`_adopt_legacy_queue`)."""


def _adopt_legacy_queue(plan: _Plan, doc: Mapping, now: datetime) -> Found:
    """Give a queued step the pre-queue-record engine persisted a bounded ``queue`` record.

    That engine left a ``blocked`` step (or one its ``unblocked`` made ``pending`` with
    ``resume``) with the working ``deadline`` of its first, blocked dispatch and no
    ``queue``; a guarded wake whose lookup was refused stayed ``sleeping`` with one
    ``wait_blocked`` entry per refusal and no ``lookup_blocked_since``. Unadopted, neither
    is bounded. The spell is dated from the earliest trustworthy time: the old deadline
    minus ``timeout_s`` (the first blocked dispatch), else the step's first ``blocked``
    entry of its trailing spell, else now; the bound is then the normal one, so a step
    already past it fails ``queue_timeout`` before any dispatch or lookup."""
    for st in doc["steps"]:
        status = st["status"]
        if st.get("queue") is None and (
            status == "blocked" or (status == "pending" and st.get("resume"))
        ):
            since = _legacy_queue_since(plan, doc, st, now)
            limit = queue_limit_s(_timeout_of(plan, st))
            new, nst = _copy_with(doc, st["key"])
            nst["deadline"] = None
            nst["queue"] = {
                "since": _iso(since),
                "deadline": _iso(since + timedelta(seconds=limit)),
                "polls": max(1, len(_trailing_events(doc, st["key"], ("blocked",)))),
                "last_at": _iso(now),
                "left_at": None,
            }
            return new, QUEUE_ADOPTED, st["key"]
        if status == SLEEPING and st.get("lookup_blocked_since") is None:
            refusals = _trailing_events(doc, st["key"], ("wait_blocked",), strict=True)
            if refusals:
                new, nst = _copy_with(doc, st["key"])
                nst["lookup_blocked_since"] = refusals[0].get("at") or _iso(now)
                nst["lookup_blocked"] = len(refusals)
                return new, QUEUE_ADOPTED, st["key"]
    return None


def _trailing_events(
    doc: Mapping, key: str, events: tuple[str, ...], *, strict: bool = False
) -> list[Mapping]:
    """The step's history entries of ``events`` in its latest spell, oldest first: those
    after its last entry that is not one of ``events`` (the old queue cycle's
    ``unblocked`` / ``dispatched`` entries are skipped unless ``strict``)."""
    cycle = set(events) if strict else {*events, "unblocked", "dispatched"}
    found: list[Mapping] = []
    for h in reversed(doc.get("history") or []):
        if h.get("step") != key:
            continue
        if h.get("event") not in cycle:
            break
        if h.get("event") in events:
            found.append(h)
    return found[::-1]


def _legacy_queue_since(plan: _Plan, doc: Mapping, st: Mapping, now: datetime) -> datetime:
    deadline = _parse(st.get("deadline"))
    if deadline is not None:
        return min(deadline - timedelta(seconds=_timeout_of(plan, st)), now)
    blocked = _trailing_events(doc, st["key"], ("blocked",))
    return (_parse(blocked[0].get("at")) if blocked else None) or now


def _loop_states(doc: Mapping, parent: str, iteration: int) -> list[dict]:
    return [
        s
        for s in doc["steps"]
        if (s.get("loop") or {}).get("parent") == parent
        and (s.get("loop") or {}).get("iteration") == iteration
    ]


def _spawn(new: dict, loop_step: Step, iteration: int) -> None:
    for b in loop_step.body:
        state = _new_state(
            step_key(loop_step.id, iteration, b.id),
            b.id,
            {"parent": loop_step.id, "iteration": iteration},
        )
        if not b.enabled:
            state["status"] = "skipped"
        new["steps"].append(state)


def _loop_outputs(step: Step, results: list[dict]) -> dict[str, Any]:
    names = [p.name for p in step.outputs]
    if step.kind == "for_each":
        if not names:
            return {"results": results}
        return {n: (results if n == "results" else [r.get(n) for r in results]) for n in names}
    last = results[-1] if results else {}
    out = {n: last.get(n) for n in names if n in last} if names else dict(last)
    if "iterations" in names:
        out["iterations"] = len(results)
    return out


def _until(step: Step, result: Mapping[str, Any], iteration: int) -> bool:
    tree = step.config.get("until")
    if not isinstance(tree, dict):
        return True
    ctx = {"trigger": dict(result), "variables": {**result, "iteration": iteration}}
    try:
        return cond.evaluate(tree, ctx)
    except (cond.ConditionError, TypeError, ValueError):
        return False


def _loop_progress(plan: _Plan, doc: Mapping, now: datetime) -> Found:
    for st in doc["steps"]:
        if st["status"] != "running" or st.get("loop"):
            continue
        found = _progress_loop(plan, doc, st)
        if found is not None:
            return found
    return None


def _progress_loop(plan: _Plan, doc: Mapping, st: Mapping) -> Found:
    """The next transition of running loop ``st``, or None while its iteration runs."""
    step = plan.top[st["def"]]
    i = st["iteration"]
    body = _loop_states(doc, st["key"], i)
    failed = next((b for b in body if b["status"] == "failed"), None)
    new, nst = _copy_with(doc, st["key"])
    if failed is not None:
        nst.update(
            status="failed",
            error=_error(
                "loop_body_failed",
                f"{failed['key']}: {(failed.get('error') or {}).get('message')}",
            ),
        )
        return new, "failed", st["key"]
    if not body or any(b["status"] not in STEP_OK for b in body):
        return None
    done = [b for b in body if b["status"] == "succeeded"]
    result = dict(done[-1].get("outputs") or {}) if done else {}
    results = list(nst.get("results") or []) + [result]
    nst["results"] = results
    if step.kind == "for_each":
        if i + 1 < len(nst.get("items") or []):
            return _next_iteration(new, nst, step, i)
        return _loop_done(new, nst, step, results)
    if _until(step, result, i):
        return _loop_done(new, nst, step, results)
    if i + 1 >= (step.max_iterations or 1):
        message = f"until not met after {step.max_iterations} iterations"
        nst.update(
            status="failed",
            error=_error("loop_max_exceeded", _explained(step, result, message)),
        )
        return new, "failed", st["key"]
    return _next_iteration(new, nst, step, i)


def _next_iteration(new: dict, nst: dict, step: Step, i: int) -> Found:
    nst["iteration"] = i + 1
    _spawn(new, step, i + 1)
    return new, "iteration", nst["key"]


def _loop_done(new: dict, nst: dict, step: Step, results: list[dict]) -> Found:
    outputs = _loop_outputs(step, results)
    problem = _check_ports(step.outputs, outputs, "output")
    if problem:
        problem["code"] = "output_type_mismatch"
        nst.update(status="failed", error=problem, outputs=outputs)
        return new, "failed", nst["key"]
    nst.update(status="succeeded", outputs=outputs)
    return new, "succeeded", nst["key"]


def _run_failure(plan: _Plan, doc: Mapping, now: datetime) -> Found:
    hand_back = step_state(doc, FAILURE_STEP)
    if hand_back is not None:  # on_failure already started: end the run once it is done
        if hand_back["status"] not in STEP_DONE:
            return None
        new = copy.deepcopy(dict(doc))
        new.update(status="failed", finished_at=_iso(now), error=hand_back.get("failure"))
        return new, "run_failed", FAILURE_STEP
    failed = next(
        (
            s
            for s in doc["steps"]
            if not s.get("loop") and s["status"] == "failed" and s["key"] != FAILURE_STEP
        ),
        None,
    )
    if failed is None:
        return None
    failure = {"step": failed["key"], **(failed.get("error") or {})}
    return _fail_run(plan, copy.deepcopy(dict(doc)), failure, now)


def _fail_run(plan: _Plan, new: dict, failure: dict, now: datetime) -> Found:
    """End ``new`` failed with ``failure`` - or, when the rule has an ``on_failure`` action,
    first cancel the unfinished steps and add its step (once; the run ends when it is done,
    whatever its outcome, with ``failure`` as its error). ``new`` is a copy to mutate."""
    if step_state(new, FAILURE_STEP) is not None:  # never a second handler (defensive)
        return None
    for s in new["steps"]:
        if s["status"] not in STEP_DONE:
            s["status"] = "cancelled"
    on_failure = plan.rule.on_failure
    if on_failure is None:
        new.update(status="failed", finished_at=_iso(now), error=failure)
        return new, "run_failed", failure.get("step")
    context = {
        "workflow": {"outputs": _workflow_outputs(plan, new)},
        "trigger": new.get("trigger") or {},
        "rules": {k: {"outputs": v} for k, v in (new.get("upstream") or {}).items()},
        "run": {
            "id": new["id"],
            "error": {
                "step": failure.get("step"),
                "code": failure.get("code"),
                "message": failure.get("message"),
            },
        },
    }
    state = _new_state(FAILURE_STEP, FAILURE_STEP)
    state["inputs"] = resolve_refs(dict(on_failure.params), context)
    state["failure"] = failure  # the run's error once the hand-back is done
    new["steps"].append(state)
    return new, "on_failure_ready", FAILURE_STEP


def _skip_disabled(plan: _Plan, doc: Mapping, now: datetime) -> Found:
    for st in doc["steps"]:
        if st["status"] != "pending" or st.get("loop") or st["key"] in TERMINAL_STEPS:
            continue
        step = plan.top.get(st["def"])
        if step is not None and not step.enabled and _deps_done(plan, doc, step.id):
            new, nst = _copy_with(doc, st["key"])
            nst["status"] = "skipped"
            return new, "skipped", st["key"]
    return None


def _wait_start(plan: _Plan, doc: Mapping, now: datetime) -> Found:
    """Park a ready top-level wait step: ``sleeping`` until ``now + seconds`` (persisted)."""
    for st in doc["steps"]:
        if st["status"] != "pending" or st.get("loop"):
            continue
        step = plan.top.get(st["def"])
        if step is None or step.kind != "wait" or not step.enabled:
            continue
        if not _deps_done(plan, doc, step.id):
            continue
        new, nst = _copy_with(doc, st["key"])
        seconds = float(step.config["seconds"])
        nst.update(status=SLEEPING, attempt=1, deadline=_iso(now + timedelta(seconds=seconds)))
        return new, "wait_started", st["key"]
    return None


def _guard_expected(plan: _Plan, doc: Mapping, ref: Any) -> Any:
    """The SHA a guard ref names: ``inputs.<name>`` or ``vars.<name>`` (its default)."""
    kind, _, name = ref.partition(".") if isinstance(ref, str) else ("", "", "")
    if kind == "inputs":
        return (doc.get("inputs") or {}).get(name)
    if kind == "vars" and plan.workflow:
        return next((v.default for v in plan.workflow.variables if v.name == name), None)
    return None


def _guard_target(doc: Mapping, guard: Mapping[str, Any]) -> tuple[Any, int | None]:
    """The PR a guard watches: the guard's own ``repo``/``number``, else the run's inputs,
    else its trigger payload."""
    trigger = doc.get("trigger") or {}
    data = trigger.get("data") if isinstance(trigger.get("data"), Mapping) else {}
    sources = (
        guard,
        doc.get("inputs") or {},
        {"repo": data.get("repository"), "number": data.get("number")},  # normalized event
        trigger,
    )
    repo = next((s["repo"] for s in sources if s.get("repo")), None)
    raw = next((s["number"] for s in sources if s.get("number") is not None), None)
    try:
        number = int(raw) if raw is not None and not isinstance(raw, bool) else None
    except (TypeError, ValueError):
        number = None
    return repo, number


def _loop_start(plan: _Plan, doc: Mapping, now: datetime) -> Found:
    for st in doc["steps"]:
        if st["status"] != "pending" or st.get("loop"):
            continue
        step = plan.top.get(st["def"])
        if step is None or step.kind not in LOOP_KINDS or not step.enabled:
            continue
        if not _deps_done(plan, doc, step.id):
            continue
        return _start_loop(plan, doc, st, step)
    return None


def _start_loop(plan: _Plan, doc: Mapping, st: Mapping, step: Step) -> Found:
    new, nst = _copy_with(doc, st["key"])
    resolved = _step_inputs(plan, doc, st)
    if "inputs" not in resolved:
        nst.update(status="failed", error=resolved)
        return new, "failed", st["key"]
    inputs = resolved["inputs"]
    nst.update(status="running", inputs=inputs, iteration=0, results=[], attempt=1)
    if step.kind == "for_each":
        found = _start_for_each(new, nst, step, inputs)
        if found is not None:
            return found
    _spawn(new, step, 0)
    return new, "loop_started", st["key"]


def _start_for_each(new: dict, nst: dict, step: Step, inputs: Mapping[str, Any]) -> Found:
    """Bind a for_each loop's items; a finished transition, or None to spawn iteration 0."""
    items = inputs.get(step.config.get("items", "items"))
    if not isinstance(items, list):
        nst.update(status="failed", error=_error("loop_items_invalid", "items not a list"))
        return new, "failed", nst["key"]
    if len(items) > (step.max_iterations or 0):
        nst.update(
            status="failed",
            error=_error(
                "loop_max_exceeded",
                f"{len(items)} items exceed max_iterations={step.max_iterations}",
            ),
        )
        return new, "failed", nst["key"]
    nst["items"] = items
    if not items:
        return _loop_done(new, nst, step, [])
    return None


def _workflow_outputs(plan: _Plan, doc: Mapping) -> dict[str, Any]:
    wf = plan.workflow
    if wf is None:
        return {}
    variables = {v.name: v.default for v in wf.variables}
    out: dict[str, Any] = {}
    for o in wf.outputs:
        parts = (o.source or "").split(".")
        value = None
        if len(parts) == 2 and parts[0] == "inputs":
            value = (doc.get("inputs") or {}).get(parts[1])
        elif len(parts) == 2 and parts[0] == "vars":
            value = variables.get(parts[1])
        elif len(parts) == 4 and parts[0] == "steps":
            value = _latest_output(doc, plan, parts[1]).get(parts[3])
        out[o.name] = value
    return out


def _finish(plan: _Plan, doc: Mapping, now: datetime) -> Found:
    if step_state(doc, FAILURE_STEP) is not None:  # the run is failing: _run_failure ends it
        return None
    top = [s for s in doc["steps"] if not s.get("loop") and s["key"] not in TERMINAL_STEPS]
    if any(s["status"] not in STEP_OK for s in top):
        return None
    action = step_state(doc, ACTION_STEP)
    new = copy.deepcopy(dict(doc))
    if action is None:
        outputs = _workflow_outputs(plan, doc)
        wf = plan.workflow
        for o in wf.outputs if wf else ():
            value = outputs.get(o.name)
            if value is not None and not type_ok(o.type, value):
                failure = _error("output_type_mismatch", f"workflow output {o.name!r}")
                return _fail_run(plan, new, {"step": None, **failure}, now)
        context = {
            "workflow": {"outputs": outputs},
            "trigger": doc.get("trigger") or {},
            "rules": {k: {"outputs": v} for k, v in (doc.get("upstream") or {}).items()},
            "run": {"id": doc["id"]},
        }
        state = _new_state(ACTION_STEP, ACTION_STEP)
        state["inputs"] = resolve_refs(dict(plan.rule.action.params), context)
        new["outputs"] = outputs
        new["steps"].append(state)
        return new, "action_ready", ACTION_STEP
    if action["status"] in STEP_OK:  # skipped: only_at_chain_end and the chain goes on
        new.update(status="succeeded", finished_at=_iso(now))
        return new, "run_succeeded", None
    return None
