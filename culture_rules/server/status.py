"""Read models behind ``GET /machines/status``, ``GET /asks`` and the ``/runs`` summaries.

Pure reads over the store (standard-library only), recomputed on every request:

- a machine's liveness and load come from its heartbeat (``machines/heartbeat.py``): online
  while the latest beat is younger than ``offline_after(beat_every)``; load is reported in percent
  0-100 (the heartbeat stores fractions; CPU load-per-core above 1 is clamped to 100) and is
  ``null`` while the machine is offline or never beat;
- *running* are the steps of active runs dispatched to that host and not yet finished
  (``dispatching``, ``running``, ``waiting``);
- *queue depth* counts the pending steps of active runs bound to that host: placed on it by
  name (``placement.machine``), or pending a retry after running there.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors.human import ASKS_COLLECTION
from culture_rules.engine.runs import ACTIVE, RUNS_COLLECTION
from culture_rules.machines.heartbeat import (
    HEARTBEAT_COLLECTION,
    HEARTBEAT_INTERVAL_S,
    online_machines,
)
from culture_rules.store.port import StoreOps

__all__ = [
    "ASK_STATUSES",
    "IN_FLIGHT",
    "list_asks",
    "machine_statuses",
    "run_hosts",
]

ASK_STATUSES = ("open", "answered", "expired")
IN_FLIGHT = ("dispatching", "running", "waiting")
_ASK_INTERNAL = ("idempotency_key", "requested_emitted", "delivered")


def list_asks(
    store: StoreOps, *, run_id: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    """Asks (oldest first), engine-internal bookkeeping fields removed."""
    where: dict[str, Any] = {}
    if run_id:
        where["run_id"] = run_id
    if status:
        where["status"] = status
    docs = store.find(ASKS_COLLECTION, where or None)
    docs = sorted(docs, key=lambda d: (str(d.get("asked_at") or ""), str(d.get("id"))))
    return [{k: v for k, v in d.items() if k not in _ASK_INTERNAL} for d in docs]


def run_hosts(doc: Mapping[str, Any]) -> list[str]:
    """The distinct hosts any step of a run was dispatched to, sorted."""
    hosts = {st.get("host") for st in doc.get("steps") or () if isinstance(st, Mapping)}
    return sorted(h for h in hosts if isinstance(h, str) and h)


def _percent(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(min(max(float(value) * 100.0, 0.0), 100.0), 1)


def _load(beat: Mapping[str, Any] | None) -> dict[str, float | None] | None:
    if beat is None:
        return None
    raw = beat.get("load") if isinstance(beat.get("load"), Mapping) else {}
    return {key: _percent(raw.get(key)) for key in ("cpu", "gpu", "mem")}


def _step_defs(steps: Any) -> Iterable[Mapping[str, Any]]:
    for step in steps if isinstance(steps, list | tuple) else ():
        if isinstance(step, Mapping):
            yield step
            yield from _step_defs(step.get("body"))


def _work(store: StoreOps) -> tuple[dict[str, list[dict[str, str]]], dict[str, int]]:
    """(host -> in-flight steps, host -> queued step count) over the active runs."""
    running: dict[str, list[dict[str, str]]] = {}
    queued: dict[str, int] = {}
    runs = sorted(
        store.find(RUNS_COLLECTION, {"status": ACTIVE}),
        key=lambda d: (str(d.get("created_at") or ""), str(d.get("id"))),
    )
    for run in runs:
        _tally_run(run, running, queued)
    return running, queued


def _tally_run(
    run: Mapping[str, Any], running: dict[str, list[dict[str, str]]], queued: dict[str, int]
) -> None:
    """Add ``run``'s in-flight steps to ``running`` and its pending ones to ``queued``."""
    workflow = run.get("workflow") or {}
    definition = workflow.get("definition") or {}
    label = str(definition.get("name") or workflow.get("id") or "workflow")
    defs = {str(s.get("id")): s for s in _step_defs(definition.get("steps"))}
    for st in run.get("steps") or ():
        sdef = defs.get(str(st.get("def")), {})
        host = st.get("host")
        if st.get("status") in IN_FLIGHT and isinstance(host, str):
            running.setdefault(host, []).append(
                {
                    "step": str(sdef.get("name") or st.get("def") or st.get("key")),
                    "workflow": label,
                    "run_id": str(run.get("id")),
                }
            )
        elif st.get("status") == "pending":
            target = _queue_target(sdef, host)
            if isinstance(target, str) and target:
                queued[target] = queued.get(target, 0) + 1


def _queue_target(sdef: Mapping[str, Any], host: Any) -> Any:
    """Where a pending step waits: its placed machine, else the host it last ran on."""
    placed = (sdef.get("placement") or {}).get("machine")
    return placed if isinstance(placed, str) and placed else host


def machine_statuses(
    store: StoreOps, now: datetime, *, beat_every: float = HEARTBEAT_INTERVAL_S
) -> list[dict[str, Any]]:
    """One status per enrolled (not soft-deleted) machine, ordered by name."""
    names = sorted(
        {
            str(d.get("name") or d.get("id"))
            for d in store.find("machines")
            if not d.get("deleted_at")
        }
    )
    beats = {
        d.get("machine"): d for d in store.find(HEARTBEAT_COLLECTION) if isinstance(d, Mapping)
    }
    online = online_machines(store, now, beat_every=beat_every)
    running, queued = _work(store)
    out = []
    for name in names:
        beat = beats.get(name)
        is_online = name in online
        out.append(
            {
                "name": name,
                "online": is_online,
                "last_seen": (beat or {}).get("ts"),
                "load": _load(beat) if is_online else None,
                "running": running.get(name, []),
                "queue_depth": queued.get(name, 0),
            }
        )
    return out
