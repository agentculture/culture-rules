"""Run completions: the immutable record of how a run finished, and the event it emits (d21).

When a run reaches a terminal status, the engine writes one **completion record** into
:data:`RUN_COMPLETIONS` in the *same* store transaction as the terminal transition (the
compare-and-set on the run's ``rev``): either both commit or neither does. The record holds
the complete ``rules.run.*`` wire envelope the run emits, built from the run document as it is
in that transition - its pinned rule and workflow (id and version), status, error, only the
explicitly exported outputs, the trigger's subject fields and the lineage (causation,
correlation, hops). Nothing else writes the collection, and the envelope in a record is never
rewritten: a later edit of the run document (a stale or wrong writer) changes neither what is
emitted nor what a rule on the event is verified against. The only fields that change after
the insert are the delivery state, ``emitted`` (``False`` -> ``True``, by compare-and-set) and
``event_id`` (the id the event was stored under), written by the outbox
(:mod:`culture_rules.node.run_events`).

Record: ``id`` (= the run id, one record per run), ``run_id``, ``rule_id``, ``status``,
``envelope`` (the wire envelope, see :func:`build_run_event`), ``emitted``, ``event_id``,
``recorded_at``.

Restore: completions are backed up with run history, the events themselves are not. A
restore re-opens the recently emitted completions whose event is missing
(:func:`reopen_undelivered`) and the outbox delivers them again under the same id; the
deterministic run ids keep downstream work to exactly once relative to the backup.

Upgrade cut-off: only terminal transitions written by this code produce a record, so runs that
finished under an older engine emit nothing - and none is lost in between, since emission is
driven by un-emitted records, not by change-feed history. Standard-library only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from culture_rules.engine.matching import exported_outputs
from culture_rules.events.emit import (
    INTERNAL_SOURCE_PREFIX,
    MAX_EVENT_HOPS,
    RUN_EVENT_ID_PREFIX,
    RUN_EVENT_TYPE_PREFIX,
    derive_envelope,
    event_hops,
)
from culture_rules.model.rule import Rule
from culture_rules.model.workflow import Workflow
from culture_rules.store.port import EVENTS_COLLECTION, StoreOps
from culture_rules.store.versioning import utc_timestamp

__all__ = [
    "REOPEN_WINDOW",
    "RUN_COMPLETIONS",
    "RUN_EVENT_PREFIX",
    "RUN_EVENT_SOURCE",
    "RUN_EVENT_TYPES",
    "SUBJECT_FIELDS",
    "build_run_event",
    "record_completion",
    "reopen_undelivered",
    "run_event_id",
]

RUN_COMPLETIONS = "run_completions"
"""One immutable completion record per finished run (the run-event outbox)."""
RUN_EVENT_PREFIX = RUN_EVENT_TYPE_PREFIX
RUN_EVENT_TYPES: dict[str, str] = {
    "succeeded": "rules.run.succeeded",
    "failed": "rules.run.failed",
    "cancelled": "rules.run.cancelled",
    "superseded": "rules.run.superseded",
}
"""Terminal run status -> the event type its run emits (one type per status)."""
RUN_EVENT_SOURCE = f"{INTERNAL_SOURCE_PREFIX}runs"
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


def run_event_id(run_id: str) -> str:
    """The id of the event run ``run_id`` emits when it finishes (the same on every node)."""
    return RUN_EVENT_ID_PREFIX + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]


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

    A pure function of the run document; the engine calls it once, in the terminal
    transition, and stores the result (:func:`record_completion`)."""
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
        time=run.get("finished_at") or run.get("created_at") or None,
        hops=hops,
    )


def record_completion(tx: StoreOps, before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    """In the terminal transition's transaction ``tx``: when ``after`` finishes a run that
    ``before`` had not finished, insert its completion record (read first: a duplicate key
    aborts a MongoDB transaction; the first record wins). Answer whether one was inserted."""
    if after.get("status") not in RUN_EVENT_TYPES or before.get("status") in RUN_EVENT_TYPES:
        return False
    envelope = build_run_event(after)
    if envelope is None or tx.get(RUN_COMPLETIONS, after["id"]) is not None:
        return False
    tx.insert(
        RUN_COMPLETIONS,
        {
            "id": after["id"],
            "run_id": after["id"],
            "rule_id": after.get("rule_id"),
            "status": after["status"],
            "envelope": envelope,
            "emitted": False,
            "event_id": None,
            "blocked": False,
            "recorded_at": utc_timestamp(None),
        },
    )
    return True


REOPEN_WINDOW = timedelta(hours=24)
"""How far before the restored point an emitted completion is re-opened by a restore:
the daily snapshot interval. Older ones were consumed long before the backup."""


def reopen_undelivered(store: Any, *, restored_to: datetime | None = None) -> int:
    """After a restore: re-open every completion emitted within :data:`REOPEN_WINDOW` before
    ``restored_to`` (or with no ``emitted_at``) whose event is not in the ``events``
    collection, which is not backed up. The outbox then delivers it again under the **same**
    ``event_id``, and the trigger consumers evaluate it with the restored rules:

    * a downstream rule that had fired already has its run in the restored ``runs`` (the run
      id is derived from rule and event id), so starting it again is a duplicate key and no
      second run starts;
    * one whose firing had not reached a run yet (or had not been evaluated) runs now.

    Downstream work stays exactly once relative to the backup. Answer how many."""
    cutoff = None if restored_to is None else utc_timestamp(restored_to - REOPEN_WINDOW)
    reopened = 0
    for record in store.find(RUN_COMPLETIONS, {"emitted": True}):
        event_id = record.get("event_id")
        if not event_id or store.get(EVENTS_COLLECTION, event_id) is not None:
            continue
        emitted_at = record.get("emitted_at")
        if cutoff is not None and emitted_at and emitted_at < cutoff:
            continue
        moved = store.update_if(
            RUN_COMPLETIONS, record["id"], {"emitted": True}, {"emitted": False, "blocked": False}
        )
        reopened += 1 if moved.won else 0
    return reopened
