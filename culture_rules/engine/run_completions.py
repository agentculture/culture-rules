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
restore re-opens every emitted completion whose event is missing (:func:`reopen_undelivered`)
and the outbox delivers it again under its assigned id. The consumption marks
(:data:`RUN_EVENT_CONSUMPTION`) and the intents, reservations and runs backed up with them
keep downstream work exactly once relative to the backup.

Upgrade cut-off: only terminal transitions written by this code produce a record, so runs that
finished under an older engine emit nothing - and none is lost in between, since emission is
driven by un-emitted records, not by change-feed history. Standard-library only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
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
    "REOPEN_BATCH",
    "RUN_COMPLETIONS",
    "RUN_EVENT_CONSUMPTION",
    "RUN_EVENT_PREFIX",
    "RUN_EVENT_SOURCE",
    "RUN_EVENT_TYPES",
    "SUBJECT_FIELDS",
    "build_run_event",
    "consumption_id",
    "record_completion",
    "reopen_undelivered",
    "run_event_id",
]

RUN_COMPLETIONS = "run_completions"
"""One immutable completion record per finished run (the run-event outbox)."""
RUN_EVENT_CONSUMPTION = "run_event_consumption"
"""One mark per (trigger consumer, run event) it evaluated, written in the evaluating
transaction together with the firing intents and key reservations it decided. Backed up with
them (consumption marks, then intents, then reservations, then runs:
:mod:`culture_rules.ops.backup`), it is the consumer progress a restore needs: a re-delivered
run event is not re-decided by a consumer that had already decided it."""
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


REOPEN_BATCH = 500
"""How many completion records one :func:`reopen_undelivered` page looks at."""


def consumption_id(consumer: str, event_id: str) -> str:
    """The id of the mark that trigger consumer ``consumer`` evaluated run event ``event_id``."""
    return f"{consumer}/{event_id}"


def reopen_undelivered(store: Any, *, limit: int | None = None) -> int:
    """After a restore: re-open every completion marked emitted whose event is not in the
    ``events`` collection (it is not backed up) - whatever its age: age is no proof that its
    event was consumed. The outbox then delivers it again under its **assigned** ``event_id``
    (immutable once assigned, :func:`~culture_rules.node.run_events.deliver`), and:

    * a trigger consumer that had evaluated it before the backup finds its consumption mark
      (:data:`RUN_EVENT_CONSUMPTION`, backed up) and skips it, so no rule re-decides it
      against today's rules - and the intents it committed then are restored with it;
    * a consumer that had not evaluated it evaluates it now, with the rules of now, as it
      would any pending event.

    Keyset-paged over completion ids (:data:`REOPEN_BATCH` per query, each page after the
    last id inspected, so records whose event is present never hide later ones); ``limit``
    caps the records *re-opened*, not those scanned (``None``: all). Answer how many."""
    reopened, last = 0, None
    while limit is None or reopened < limit:
        page = _page(store, last)
        if not page:
            break
        for record in page:
            last = record["id"]
            event_id = record.get("event_id")
            if not event_id or store.get(EVENTS_COLLECTION, event_id) is not None:
                continue
            moved = store.update_if(
                RUN_COMPLETIONS,
                record["id"],
                {"emitted": True},
                {"emitted": False, "blocked": False},
            )
            reopened += 1 if moved.won else 0
            if limit is not None and reopened >= limit:
                break
    return reopened


def _page(store: Any, after: str | None) -> list[Mapping[str, Any]]:
    """The next :data:`REOPEN_BATCH` emitted completions after id ``after``, in id order."""
    ranged = getattr(store, "find_range", None)
    if callable(ranged):
        return ranged(
            RUN_COMPLETIONS, {"emitted": True}, field="id", after=after, limit=REOPEN_BATCH
        )
    records = [
        r
        for r in store.find(RUN_COMPLETIONS, {"emitted": True})
        if after is None or r["id"] > after
    ]
    return records[:REOPEN_BATCH]
