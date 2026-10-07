"""Run-lifecycle events: one ``rules.run.*`` event per finished run (deviation d21).

Rule relationships (must/may run after) chain rules that fire on the *same* event. d21 adds
the other composition: a rule that fires when another rule's run *finishes*. Every run that
reaches a terminal status emits exactly one event into the ``events`` collection, where the
trigger consumers evaluate it like any other event, so an ordinary ``event`` trigger with
``params.type`` set to one of these types (plus a condition over ``trigger.data.*``) fires on
it.

Types
=====
One type per terminal status, never a shared type with a status field:

* ``rules.run.succeeded`` - the run (its workflow and its action) succeeded;
* ``rules.run.failed`` - it failed (after its ``on_failure`` action, if any);
* ``rules.run.cancelled`` - an operator cancelled it (or stopped a disabled rule's runs);
* ``rules.run.superseded`` - its ``head_unchanged`` wait guard saw the PR head move.

Separate types keep supersession and cancellation out of downstream work by construction: a
rule on ``rules.run.failed`` (a hand-back, a re-fix) never sees a superseded or cancelled run,
and there is no condition an author could forget. They are still emitted, so the
exactly-one-event-per-finished-run invariant holds and the history shows them.

Data
====
``data`` holds, verbatim:

* ``run_id``, ``rule_id``, ``workflow_id``, ``workflow_version`` (``None`` without a
  workflow), ``status`` and ``concurrency_key`` (the resolved key, ``None`` when unkeyed);
* ``outputs`` - only the outputs the run's pinned workflow explicitly exports
  (:func:`~culture_rules.engine.matching.exported_outputs`; ``{}`` when it did not produce
  them, e.g. a failed run);
* ``error_code`` / ``error_message`` - the run's error (``None`` on success);
* ``trigger_event_id`` / ``trigger_type`` - the event the run fired on (``None`` for a run
  started by hand);
* the subject fields of the trigger's ``data`` that are present and scalar,
  :data:`SUBJECT_FIELDS` (``repository``, ``number``, ``head_sha`` ...), at the top level,
  so a downstream rule resolves the same concurrency-key template
  (``pr-fixer:{trigger.data.repository}#{trigger.data.number}``) and they carry on down a
  chain. Nothing else of the trigger is copied (free text such as a comment body stays out).

Lineage (:func:`~culture_rules.events.emit.derive_envelope`): ``causationId`` is the run's
trigger event, ``correlationId`` is inherited from it, ``runId`` is the finished run and
``hops`` is the trigger's hops plus one (a run started by hand has hops 1). The id is
:func:`run_event_id` (a digest of the run id) and ``time`` is the run's ``finished_at``, so
the event is a deterministic function of the finished run document.

Exactly once
============
Emission is a change-feed consumer on ``runs`` (:class:`~culture_rules.node.chain.FeedConsumer`,
one shared consumer :data:`RUN_EVENTS_CONSUMER` polled by every node): for each run whose
post-image is terminal it commits, in **one store transaction**, the consumer's marker for the
run, the event insert and the advanced resume token. So:

* a node that dies before the commit leaves nothing; the token stays before that change and the
  next poll - on any node, or this one restarted - emits it (no loss);
* a node that committed recorded both the event and the token; a stale node replaying an old
  token finds the marker and skips (no duplicate);
* two nodes racing on the same run write the same marker; one transaction wins, the other
  conflicts and, retried, skips;
* independently of the marker, the event id is deterministic and the insert is preceded by a
  read in the same transaction, so even a lost marker can never store a second event.

The consumer's first poll pins the feed's head (as every consumer does): runs that finished
before a node first ran this version emit nothing, so an upgrade never replays history.
During a global pause emission is deferred (the token stays;
:class:`~culture_rules.node.firing.Deferred`) and resumes in order once the pause lifts, like a
chain re-evaluation: the run was accepted before the pause.

Verification
============
Events reach the ``events`` collection from the bus too, so anyone who can publish there
could publish a ``rules.run.succeeded`` claiming any outputs. Before a rule fires on a
``rules.run.*`` event the node rebuilds the event from the run it names
(:func:`verify_run_event`) and refuses every rule on any difference - an unknown or
unfinished run, another id, type, time, lineage, hop count or byte of ``data`` - with the
final skip ``run_event_unverified``. A faithful copy is indistinguishable from the real event,
and both carry the same id, so at most one is stored and evaluated.
Standard-library only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from culture_rules.engine.matching import exported_outputs
from culture_rules.engine.runs import RUN_DONE, RUNS_COLLECTION, SUPERSEDED
from culture_rules.events.emit import MAX_EVENT_HOPS, derive_envelope, event_hops
from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
from culture_rules.model.rule import Rule
from culture_rules.model.workflow import Workflow
from culture_rules.store.port import StoreOps

__all__ = [
    "RUN_EVENTS_CONSUMER",
    "RUN_EVENTS_HOST",
    "RUN_EVENT_PREFIX",
    "RUN_EVENT_SOURCE",
    "RUN_EVENT_TYPES",
    "SUBJECT_FIELDS",
    "build_run_event",
    "emit_run_event",
    "is_run_event",
    "run_event_id",
    "verify_run_event",
]

RUN_EVENT_PREFIX = "rules.run."
RUN_EVENT_TYPES: dict[str, str] = {
    "succeeded": "rules.run.succeeded",
    "failed": "rules.run.failed",
    "cancelled": "rules.run.cancelled",
    SUPERSEDED: "rules.run.superseded",
}
"""Terminal run status -> the event type its run emits (one type per status)."""
RUN_EVENT_SOURCE = "culture-rules://runs"
RUN_EVENTS_HOST = "run-events"
"""The ``host`` recorded on the stored event (it is not ingested from a host's subscription)."""
RUN_EVENTS_CONSUMER = "run-events"
"""The one change-feed consumer every node shares to emit run events."""
SUBJECT_FIELDS = (
    "repository",
    "number",
    "head_sha",
    "head_branch",
    "base_sha",
    "base_branch",
    "base_repo",
    "head_repo",
    "pr_author",
    "draft",
)
"""Trigger ``data`` fields copied to the top level of a run event when present and scalar:
what a downstream rule needs to correlate (its concurrency key, its PR)."""

assert set(RUN_EVENT_TYPES) == set(RUN_DONE), "every terminal run status emits one event type"


def run_event_id(run_id: str) -> str:
    """The id of the event run ``run_id`` emits when it finishes (the same on every node)."""
    return "runevt_" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]


def is_run_event(envelope: Mapping[str, Any]) -> bool:
    """Whether ``envelope`` claims to be a run-lifecycle event (by its type)."""
    kind = envelope.get("type")
    return isinstance(kind, str) and kind.startswith(RUN_EVENT_PREFIX)


def _exported(run: Mapping[str, Any]) -> dict[str, Any]:
    """The run's outputs its pinned workflow explicitly exports (nothing when unreadable)."""
    outputs = run.get("outputs")
    pinned_rule = (run.get("rule") or {}).get("definition")
    pinned_wf = (run.get("workflow") or {}).get("definition")
    if not isinstance(outputs, Mapping) or not isinstance(pinned_rule, Mapping):
        return {}
    if not isinstance(pinned_wf, Mapping):
        return {}
    try:
        rule = Rule.from_dict(dict(pinned_rule), strict=False)
        wf = Workflow.from_dict(dict(pinned_wf), strict=False)
    except ValueError:
        return {}  # fail closed: export nothing rather than everything
    names = exported_outputs(rule, {wf.id: wf})
    return {k: outputs[k] for k in sorted(outputs) if k in names}


def _subject(trigger: Mapping[str, Any]) -> dict[str, Any]:
    data = trigger.get("data")
    if not isinstance(data, Mapping):
        return {}
    return {
        k: data[k]
        for k in SUBJECT_FIELDS
        if k in data and (data[k] is None or isinstance(data[k], str | int | float | bool))
    }


def build_run_event(run: Mapping[str, Any]) -> dict[str, Any] | None:
    """The wire envelope finished run ``run`` emits, or ``None`` while it is not finished.

    A pure function of the run document (module doc, "Data")."""
    status = run.get("status")
    if status not in RUN_EVENT_TYPES or not isinstance(run.get("id"), str):
        return None
    trigger = run.get("trigger") if isinstance(run.get("trigger"), Mapping) else {}
    cause = trigger if isinstance(trigger.get("id"), str) and trigger.get("id") else None
    if cause is None:
        hops = 1
    else:
        trigger_hops = event_hops(cause)
        hops = MAX_EVENT_HOPS + 1 if trigger_hops is None else trigger_hops + 1
    workflow = run.get("workflow") if isinstance(run.get("workflow"), Mapping) else {}
    error = run.get("error") if isinstance(run.get("error"), Mapping) else {}
    data: dict[str, Any] = {
        **_subject(trigger),
        "run_id": run["id"],
        "rule_id": run.get("rule_id"),
        "workflow_id": run.get("workflow_id"),
        "workflow_version": workflow.get("version"),
        "status": status,
        "concurrency_key": run.get("concurrency_key"),
        "outputs": _exported(run),
        "error_code": error.get("code"),
        "error_message": error.get("message"),
        "trigger_event_id": trigger.get("id") if cause is not None else None,
        "trigger_type": trigger.get("type") if cause is not None else None,
    }
    return derive_envelope(
        cause,
        type=RUN_EVENT_TYPES[status],
        source=RUN_EVENT_SOURCE,
        data=data,
        run_id=run["id"],
        id=run_event_id(run["id"]),
        time=run.get("finished_at") or run.get("created_at") or "",
        hops=hops,
    )


def emit_run_event(tx: StoreOps, run: Mapping[str, Any]) -> bool:
    """Store the event of finished run ``run`` through ``tx`` unless it is already stored;
    answer whether it was inserted. Read-before-insert: on MongoDB a duplicate key inside a
    transaction aborts it, so the existing event is looked up first."""
    current = tx.get(RUNS_COLLECTION, run["id"]) or run
    envelope = build_run_event(current)
    if envelope is None:
        return False
    if tx.get(EVENTS_COLLECTION, envelope["id"]) is not None:
        return False
    tx.insert(EVENTS_COLLECTION, event_document(envelope, host=RUN_EVENTS_HOST))
    return True


_COMPARED = (
    "id",
    "type",
    "source",
    "time",
    "schemaVersion",
    "correlationId",
    "causationId",
    "runId",
    "hops",
    "data",
)


def verify_run_event(tx: StoreOps, envelope: Mapping[str, Any]) -> str | None:
    """``None`` when ``envelope`` is exactly the event its run emits; otherwise why not (the
    ``run_event_unverified`` detail). Reads the run through ``tx``."""
    data = envelope.get("data")
    run_id = data.get("run_id") if isinstance(data, Mapping) else None
    if not isinstance(run_id, str) or not run_id:
        return "names no run"
    run = tx.get(RUNS_COLLECTION, run_id)
    if run is None:
        return f"run {run_id} does not exist"
    expected = build_run_event(run)
    if expected is None:
        return f"run {run_id} has not finished"
    for key in _COMPARED:
        if envelope.get(key) != expected.get(key):
            return f"{key} differs from what run {run_id} emitted"
    return None
