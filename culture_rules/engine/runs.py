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

``id``, ``status`` (``running`` -> ``succeeded`` | ``failed`` | ``cancelled``), ``rev``
(incremented by every transition), ``history`` (one entry per transition: ``rev``,
``at``, ``host``, ``event``, ``step``), ``rule`` / ``workflow`` (the **pinned**
definitions: ``id``, ``digest``, ``version`` and the full ``definition`` the run started
with - editing a workflow later never changes an in-flight run), ``trigger``,
``upstream``, ``inputs`` (workflow inputs, type-checked at start), ``outputs`` (the
workflow's explicitly exported outputs), ``error``, ``started_by``, ``created_at``,
``finished_at`` and ``steps``: a list of step states.

Step state: ``key`` (the step id; ``<loop>[<i>]/<body id>`` for a loop body step in
iteration ``i``; :data:`ACTION_STEP` for the rule's terminal action), ``def`` (step id in
the pinned workflow), ``loop`` (``{"parent", "iteration"}`` for body steps), ``status``,
``attempt``, ``host``, ``inputs``, ``outputs``, ``error`` (``{"code", "message"}``),
``deadline``, ``next_attempt_at``, ``placement_error`` and, for loops, ``iteration`` and
``results``.

Step statuses: ``pending`` -> ``dispatching`` (claimed, the actor is being invoked) ->
``waiting`` (actor accepted; completes later via :meth:`Executor.deliver`) | ``blocked``
(actor busy; asked again after :data:`BLOCKED_RETRY_S` without using an attempt) |
``retry_wait`` (attempt failed or timed out; re-dispatched after the backoff) |
``succeeded`` | ``failed`` | ``skipped`` (disabled step) | ``cancelled``. Loop steps use
``running`` while their iterations execute.

Semantics
=========

* **Timeouts and retries** - each attempt gets ``deadline = now + timeout_s`` (default
  :data:`DEFAULT_TIMEOUT_S`). Accepted work whose deadline passes, an exception from
  ``invoke`` (no acknowledgement) and a retryable ``failed`` outcome all consume an
  attempt; the step is retried after ``backoff_s * backoff_multiplier ** (attempt - 1)``
  until ``retry.max_attempts`` is used up. Every attempt uses the same idempotency key, so
  a target that already did the work (ack lost) deduplicates instead of repeating it. If
  the target cannot deduplicate (``supports_idempotency_key = False``) and the work is not
  declared idempotent, an attempt with an unknown outcome is never retried: the step fails
  with ``unsafe_retry``. A deadline that passes while the step is ``blocked`` is *not* an
  unknown outcome (blocked work never started): it consumes an attempt with error code
  ``blocked_timeout`` and is retried on any target, failing only once attempts run out.
  Wrongly typed outputs fail the step at once
  (``output_type_mismatch``; retrying cannot fix a deterministic mismatch).
* **Exactly-once dispatch** - a step is invoked only under its claim
  (:mod:`culture_rules.engine.claims`). While ``invoke`` blocks, a
  :class:`~culture_rules.engine.leasekeeper.LeaseKeeper` renews the claim's lease every
  third of the lease (until the step's deadline), so a slow actor never looks abandoned.
  Another host takes over a ``dispatching`` step only when its lease lapsed *and* the
  holder's machine is offline (no heartbeat for ``holder_offline_after``) or the step's
  deadline has passed; the holder's own host may always reclaim it (a restarted node).
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
  fails with ``loop_max_exceeded`` after ``max_iterations`` iterations. Body steps run in
  declared order; a body step's input port named ``item``/``index`` (for_each) or
  ``iteration`` (retry_until), or named like one of the loop's inputs, is filled
  implicitly when no edge feeds it. A ``for_each`` loop's outputs are, per declared port
  ``p``, the list of each iteration's final body output ``p`` (``results`` = the whole
  output objects); a ``retry_until`` loop's outputs are its last iteration's. Loops do not
  nest.
* **Rule action** - after the workflow succeeds, the rule's action runs as the terminal
  step :data:`ACTION_STEP` (kind ``"action"``), its params resolved against
  ``workflow.outputs.*``, ``trigger.*`` and ``rules.<id>.outputs.*`` (whole-string
  references or ``{{ ref }}`` templates). A rule without a workflow runs only its action.
  A whole string is a reference only when its path fits a namespace's shape; any other
  string (``rules.yaml``, ``workflow.md``, ``trigger.sh``) is a literal.
  ``{"$ref": path}`` always references and ``{"$literal": value}`` never does (see
  :mod:`culture_rules.engine.refs`). Workflow-input mappings resolve the same way.
* **Containment** (:class:`Containment`, every verb audited) - a global pause stops new
  runs and all new dispatch (accepted work may still complete); draining a machine stops
  new placements on it while its running steps finish; cancelling a run cancels every
  unfinished step and ignores late results.

Collections written in transactions are listed in :data:`RUN_COLLECTIONS`; on MongoDB they
are created up front (``ensure_collections``).
"""

from __future__ import annotations

import copy
import hashlib
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
    ActorPort,
    InvocationContext,
    InvocationResult,
)
from culture_rules.engine.audit import AUDIT_COLLECTION, AuditLog, mutating_verb, require_identity
from culture_rules.engine.claims import (
    CLAIMS_COLLECTION,
    DEFAULT_LEASE,
    ClaimResult,
    Claims,
    ReclaimGuard,
    idempotency_key,
)
from culture_rules.engine.leasekeeper import KeeperFactory, LeaseKeeper
from culture_rules.engine.placement import MachineState, PlacementError, resolve_placement
from culture_rules.engine.refs import resolve_refs
from culture_rules.machines.enrol import enrolled_machines
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION, OFFLINE_AFTER_S, online_machines
from culture_rules.model import condition as cond
from culture_rules.model.action import Action
from culture_rules.model.actor import Actor
from culture_rules.model.common import RetryPolicy
from culture_rules.model.rule import Rule
from culture_rules.model.validate import validate
from culture_rules.model.workflow import LOOP_KINDS, Port, Step, Workflow
from culture_rules.store.port import Document, StoragePort, StoreOps

__all__ = [
    "ACTION_STEP",
    "BLOCKED_RETRY_S",
    "BLOCKED_TIMEOUT",
    "CONTROLS_COLLECTION",
    "DEFAULT_TIMEOUT_S",
    "RUNS_COLLECTION",
    "RUN_COLLECTIONS",
    "Containment",
    "Executor",
    "RunError",
    "drained_machines",
    "due_steps",
    "ensure_collections",
    "is_paused",
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
)
RULES_COLLECTION = "rules"
WORKFLOWS_COLLECTION = "workflows"
ACTORS_COLLECTION = "actors"

ACTION_STEP = "@action"
"""Step key of a rule's terminal action."""
DEFAULT_TIMEOUT_S = 3600.0
BLOCKED_RETRY_S = 5.0
BLOCKED_TIMEOUT = "blocked_timeout"
"""Error code of a deadline that passed while the step was blocked (never started)."""

ACTIVE = "running"
RUN_DONE = ("succeeded", "failed", "cancelled")
STEP_DONE = ("succeeded", "failed", "skipped", "cancelled")
STEP_OK = ("succeeded", "skipped")

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

_MAX_TRANSITIONS_PER_TICK = 10_000
_MAX_CAS_RETRIES = 50
Clock = Callable[[], datetime]
Ports = Mapping[str, ActorPort] | Callable[[InvocationContext], ActorPort | None]


class RunError(ValueError):
    """A run could not be started or a containment verb was refused."""

    def __init__(self, code: str, message: str, details: Iterable[Any] = ()) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = list(details)


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
            if not res.won:  # pragma: no cover - transactions serialise this on every adapter
                raise RunError("conflict", f"run {run_id!r} changed concurrently")
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


_MUTABLE = ("status", "rev", "steps", "history", "outputs", "error", "finished_at")


def _mutable(doc: Mapping[str, Any]) -> dict[str, Any]:
    return {k: doc.get(k) for k in _MUTABLE}


def _record(doc: dict, now: datetime, host: str, event: str, key: str | None) -> None:
    doc["rev"] += 1
    doc["history"].append(
        {"rev": doc["rev"], "at": _iso(now), "host": host, "event": event, "step": key}
    )


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
        holder_offline_after: timedelta = timedelta(seconds=OFFLINE_AFTER_S),
    ) -> None:
        """``lease_keeper`` builds the keeper that renews a step's lease while its actor
        is invoked; ``holder_offline_after`` is how stale a holder's heartbeat must be
        before another host may take over its lapsed ``dispatching`` step."""
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
        self._holder_offline_after = holder_offline_after
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
    ) -> Document:
        """Start a run of the stored rule ``rule_id`` and its stored workflow, pinning both."""
        rule_doc = self._store.get(RULES_COLLECTION, rule_id)
        if rule_doc is None:
            raise RunError("rule_not_found", f"rule {rule_id!r} does not exist")
        if rule_doc.get("deleted_at"):
            raise RunError("not_fireable", f"rule {rule_id!r} is deleted")
        rule = Rule.from_dict(rule_doc, strict=False)
        workflow = None
        if rule.workflow is not None:
            wf_doc = self._store.get(WORKFLOWS_COLLECTION, rule.workflow.id)
            if wf_doc is None:
                raise RunError("workflow_not_found", f"workflow {rule.workflow.id!r} missing")
            if wf_doc.get("deleted_at"):
                raise RunError("not_fireable", f"workflow {rule.workflow.id!r} is deleted")
            workflow = Workflow.from_dict(wf_doc, strict=False)
        return self.start(
            rule, workflow, trigger=trigger, upstream=upstream, identity=identity, run_id=run_id
        )

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
    ) -> Document:
        """Validate, pin and persist a new run (audited). Refused while paused."""
        identity = require_identity(identity or self.identity)
        if is_paused(self._store):
            raise RunError("paused", "the engine is globally paused; nothing fires")
        errors = validate(rule)
        if errors:
            raise RunError("invalid_rule", "rule failed validation", [e.to_dict() for e in errors])
        trigger = dict(trigger or {})
        upstream = {k: dict(v) for k, v in (upstream or {}).items()}
        inputs = self._check_workflow(rule, workflow, trigger, upstream)
        now = self._clock()
        run_id = run_id or f"run-{uuid.uuid4().hex}"
        steps = [_new_state(s.id, s.id) for s in (workflow.steps if workflow else ())]
        doc: dict[str, Any] = {
            "id": run_id,
            "kind": "run",
            "status": ACTIVE,
            "rev": 0,
            "history": [],
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
        }
        _record(doc, now, self.host, "started", None)
        with self._store.transaction() as tx:
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
        for s in workflow.steps:
            if s.id == ACTION_STEP or any(b.kind in LOOP_KINDS for b in s.body):
                raise RunError("unsupported_workflow", f"step {s.id!r}: reserved id or nested loop")
        context = {"trigger": trigger, "rules": {k: {"outputs": v} for k, v in upstream.items()}}
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
            now = self._clock()
            plan = _Plan.of(doc)
            new = _housekeep(plan, doc, now, self.host)
            if new is not None:
                if self._cas(doc, new):
                    made += 1
                continue
            if is_paused(self._store):
                break
            if self._dispatch_one(plan, doc, now):
                made += 1
                continue
            break
        return made

    def _cas(self, before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
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
            elif st["status"] == "pending" and not drained and _ready(plan, doc, st):
                if self._dispatch(plan, doc, st, now, resume=False):
                    return True
        return False

    def _target(self, plan: _Plan, st: Mapping[str, Any], now: datetime) -> Any:
        step = plan.step(st)
        placement = step.placement if step is not None else None
        if placement is None:
            return self.host
        online = online_machines(self._store, now)
        drained = drained_machines(self._store)
        machines = enrolled_machines(self._store)
        states = [MachineState(m.name, m.name in online, m.name in drained) for m in machines]
        actors = [Actor.from_dict(d, strict=False) for d in self._store.find(ACTORS_COLLECTION)]
        resolved = resolve_placement(placement, machines, states, actors)
        return resolved if isinstance(resolved, PlacementError) else resolved.machine

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
        if not self._mark_dispatching(doc, key, attempt, inputs, deadline, now):
            self._claims.release(claim)
            return False
        idem = idempotency_key(doc["id"], key)
        with self._keep_alive(claim, deadline):
            result = _invoke(port, inputs, idem, deadline, ctx)
        self._settle(doc["id"], key, attempt, claim, result, port)
        return True

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
        """Whether ``machine`` heartbeated within ``holder_offline_after`` of ``now``."""
        beat = self._store.get(HEARTBEAT_COLLECTION, machine) or {}
        try:
            ts = datetime.fromisoformat(str(beat.get("ts")))
        except ValueError:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return now - ts < self._holder_offline_after

    def _deadline(self, plan: _Plan, st: Mapping, now: datetime, resume: bool) -> datetime:
        """A resumed attempt keeps its deadline; a new one gets the step's timeout."""
        if resume or st.get("resume"):
            return _parse(st["deadline"]) or now
        return now + timedelta(seconds=self._timeout(plan, st))

    def _resume_inputs(self, plan: _Plan, st: dict) -> dict | bool:
        """The inputs a resumed step re-runs with, or False when it resumes elsewhere."""
        step = plan.step(st)
        placed = step is not None and step.placement is not None
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
        _record(new, now, self.host, "dispatched", key)
        return self._cas(doc, new)

    def _context(self, plan: _Plan, doc: Document, st: Mapping, attempt: int) -> InvocationContext:
        if st["key"] == ACTION_STEP:
            act = plan.rule.action
            config = {"kind": act.kind, "name": act.name, "params": dict(act.params)}
            return InvocationContext(
                doc["id"], ACTION_STEP, "action", self.host, attempt, None, config
            )
        step = plan.step(st)
        actor = step.placement.actor if step.placement is not None else None
        return InvocationContext(
            doc["id"], st["key"], step.kind, self.host, attempt, actor, dict(step.config)
        )

    def _policy(self, plan: _Plan, st: Mapping) -> tuple[RetryPolicy, float | None, bool]:
        if st["key"] == ACTION_STEP:
            act: Action = plan.rule.action
            return _retry_of(act.retry), act.timeout_s, act.idempotent
        step = plan.step(st)
        return _retry_of(step.retry), step.timeout_s, bool(step.config.get("idempotent", False))

    def _timeout(self, plan: _Plan, st: Mapping) -> float:
        return self._policy(plan, st)[1] or DEFAULT_TIMEOUT_S

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
                    if (
                        doc is None
                        or doc["status"] != ACTIVE
                        or st is None
                        or st["status"] != "dispatching"
                        or st["attempt"] != attempt
                        or st["host"] != self.host
                    ):
                        claims.release(claim)  # cancelled, delivered or moved on meanwhile
                        return
                    plan = _Plan.of(doc)
                    now = self._clock()
                    new = copy.deepcopy(doc)
                    nst = step_state(new, key)
                    _apply(plan, nst, result, now, self._key_safe(plan, st, port))
                    _record(new, now, self.host, nst["status"], key)
                    res = tx.update_if(RUNS_COLLECTION, run_id, {"rev": doc["rev"]}, _mutable(new))
                    if not res.won:
                        raise _Conflict
                    if nst["status"] in STEP_DONE:
                        claims.complete(claim)
                    else:
                        claims.release(claim)
                return
            except _Conflict:
                continue
        raise RunError("contention", f"could not record {key} of {run_id}")  # pragma: no cover

    # ------------------------------------------------------------------ events

    def deliver(self, idempotency_key: str, result: InvocationResult) -> bool:
        """Record the completion (or failure) of accepted work reported later by an event.

        Returns True iff the result changed the run. Results for finished steps,
        cancelled or finished runs, and unknown keys are ignored (False).
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
            if doc is None or doc["status"] != ACTIVE or st is None or st["status"] in STEP_DONE:
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
    elif result.outcome == BLOCKED:
        st.update(
            status="blocked",
            next_attempt_at=_iso(now + timedelta(seconds=BLOCKED_RETRY_S)),
            error=_error("blocked", result.error or "actor is blocked"),
        )
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
    if st["key"] == ACTION_STEP:
        return _retry_of(plan.rule.action.retry)
    step = plan.step(st)
    return _retry_of(step.retry if step is not None else None)


def _ready(plan: _Plan, doc: Mapping, st: Mapping) -> bool:
    """Whether a pending, dispatchable step's predecessors are all done."""
    if st["key"] == ACTION_STEP:
        return True
    step = plan.step(st)
    if step is None or step.kind in LOOP_KINDS or not step.enabled:
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
            at = _parse(h.get("at"))
            if at is not None and (since is None or at > since):
                since = at
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
        if status == "retry_wait":
            due = _parse(st.get("next_attempt_at"))
            if due is None or due <= now:
                found.append((st["key"], due))
        elif status == "pending":
            plan = plan or _Plan.of(doc)
            if _ready(plan, doc, st):
                found.append((st["key"], _ready_since(plan, doc, st)))
    return found


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


def _implicit_loop_inputs(loop: Mapping, loop_state: Mapping) -> dict[str, Any]:
    """The loop's own inputs plus ``iteration``/``index`` (and ``item`` for for_each)."""
    implicit = dict(loop_state.get("inputs") or {})
    i = loop["iteration"]
    implicit.update(iteration=i, index=i)
    items = loop_state.get("items")
    if isinstance(items, list) and i < len(items):
        implicit["item"] = items[i]
    return implicit


def _step_inputs(plan: _Plan, doc: Mapping, st: Mapping) -> dict[str, Any]:
    """``{"inputs": {...}}`` for a step about to run, or an error dict."""
    if st["key"] == ACTION_STEP:
        return {"inputs": dict(st.get("inputs") or {})}
    step = plan.step(st)
    values: dict[str, Any] = {}
    loop = st.get("loop")
    loop_state = step_state(doc, loop["parent"]) if loop else None
    for e in plan.edges_into(step.id):
        src = _edge_source(plan, doc, e.source, loop, loop_state)
        if e.source_port in src:
            values[e.target_port] = src[e.source_port]
    if loop and loop_state is not None:
        implicit = _implicit_loop_inputs(loop, loop_state)
        values.update(
            (p.name, implicit[p.name])
            for p in step.inputs
            if p.name not in values and p.name in implicit
        )
    problem = _check_ports(step.inputs, values, "input")
    return problem or {"inputs": values}


def _housekeep(plan: _Plan, doc: Mapping, now: datetime, host: str) -> dict | None:
    """Return ``doc`` with the first due bookkeeping transition applied, or None."""
    for fn in (_due_timers, _loop_progress, _run_failure, _skip_disabled, _loop_start, _finish):
        found = fn(plan, doc, now)
        if found is not None:
            new, event, key = found
            _record(new, now, host, event, key)
            return new
    return None


Found = tuple[dict, str, str | None] | None


def _copy_with(doc: Mapping, key: str) -> tuple[dict, dict]:
    new = copy.deepcopy(dict(doc))
    return new, step_state(new, key)


def _due_timers(plan: _Plan, doc: Mapping, now: datetime) -> Found:
    for st in doc["steps"]:
        status = st["status"]
        deadline = _parse(st.get("deadline"))
        if status in ("waiting", "blocked") and deadline is not None and now >= deadline:
            new, nst = _copy_with(doc, st["key"])
            if status == "blocked":
                # Blocked work was refused before it started: the outcome is known (nothing
                # happened), so the retry is safe on any target (BLOCKED_TIMEOUT).
                error = _error(BLOCKED_TIMEOUT, "the step's deadline passed while blocked")
            else:
                # Re-invoking timed-out work reuses its key; dispatch refuses (unsafe_retry)
                # when the target cannot deduplicate, since housekeeping does not know the port.
                error = _error("timeout", "the step's deadline passed")
            _attempt_failed(
                plan,
                nst,
                error,
                now,
                retryable=True,
                unknown=status != "blocked",
                key_safe=True,
            )
            nst["resume"] = False
            return new, "timeout", st["key"]
        due = _parse(st.get("next_attempt_at"))
        if status in ("retry_wait", "blocked") and due is not None and now >= due:
            new, nst = _copy_with(doc, st["key"])
            nst.update(status="pending", next_attempt_at=None, resume=status == "blocked")
            return new, "retry_due" if status == "retry_wait" else "unblocked", st["key"]
    return None


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
        nst.update(
            status="failed",
            error=_error(
                "loop_max_exceeded", f"until not met after {step.max_iterations} iterations"
            ),
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
    failed = next((s for s in doc["steps"] if not s.get("loop") and s["status"] == "failed"), None)
    if failed is None:
        return None
    new = copy.deepcopy(dict(doc))
    for s in new["steps"]:
        if s["status"] not in STEP_DONE:
            s["status"] = "cancelled"
    new.update(
        status="failed",
        finished_at=_iso(now),
        error={"step": failed["key"], **(failed.get("error") or {})},
    )
    return new, "run_failed", failed["key"]


def _skip_disabled(plan: _Plan, doc: Mapping, now: datetime) -> Found:
    for st in doc["steps"]:
        if st["status"] != "pending" or st.get("loop") or st["key"] == ACTION_STEP:
            continue
        step = plan.top.get(st["def"])
        if step is not None and not step.enabled and _deps_done(plan, doc, step.id):
            new, nst = _copy_with(doc, st["key"])
            nst["status"] = "skipped"
            return new, "skipped", st["key"]
    return None


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
    top = [s for s in doc["steps"] if not s.get("loop") and s["key"] != ACTION_STEP]
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
                new.update(
                    status="failed",
                    finished_at=_iso(now),
                    error=_error("output_type_mismatch", f"workflow output {o.name!r}"),
                )
                return new, "run_failed", None
        context = {
            "workflow": {"outputs": outputs},
            "trigger": doc.get("trigger") or {},
            "rules": {k: {"outputs": v} for k, v in (doc.get("upstream") or {}).items()},
        }
        state = _new_state(ACTION_STEP, ACTION_STEP)
        state["inputs"] = resolve_refs(dict(plan.rule.action.params), context)
        new["outputs"] = outputs
        new["steps"].append(state)
        return new, "action_ready", ACTION_STEP
    if action["status"] == "succeeded":
        new.update(status="succeeded", finished_at=_iso(now))
        return new, "run_succeeded", None
    return None
