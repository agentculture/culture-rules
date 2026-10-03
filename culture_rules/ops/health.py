"""Node health status as a pure function (no web imports).

``health_status(store, now, host)`` reports: whether the store is reachable and its
schema version; this host's heartbeat age and online state; and executor lag (how long
due work in live runs - pending steps that are ready to dispatch, aged from when they
became ready, and retries past ``next_attempt_at`` - has been waiting; see
:func:`culture_rules.engine.runs.due_steps`). The
overall ``status`` is ``ok``, ``degraded`` (stale/missing heartbeat or lag above
:data:`LAG_DEGRADED_S`) or ``down`` (store unreachable). It never raises. Standard-library
only; the HTTP route lives in the server package.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from culture_rules.engine.runs import RUNS_COLLECTION, due_steps
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION, OFFLINE_AFTER_S

__all__ = ["LAG_DEGRADED_S", "health_status"]

LAG_DEGRADED_S = 30.0


def _parse(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _executor(store: Any, now: datetime) -> dict[str, Any]:
    lag, due = 0.0, 0
    for run in store.find(RUNS_COLLECTION, {"status": "running"}):
        try:
            found = due_steps(run, now)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue  # an unparseable run is not executor lag; the executor skips it too
        for _key, since in found:
            due += 1
            if since is not None:
                lag = max(lag, (now - since).total_seconds())
    return {"lag_s": round(lag, 3), "due_steps": due}


def health_status(store: Any, now: datetime, host: str) -> dict[str, Any]:
    """Health document for the node ``host``; JSON-serialisable."""
    out: dict[str, Any] = {"host": host, "at": now.astimezone(UTC).isoformat()}
    try:
        beat = store.get(HEARTBEAT_COLLECTION, host)
        executor = _executor(store, now)
        version = getattr(store, "node_schema_version", None)
        out["store"] = {
            "reachable": True,
            "schema_version": None if version is None else str(version),
        }
    except Exception as exc:  # noqa: BLE001 - health must report and never raise
        out["store"] = {"reachable": False, "schema_version": None, "error": str(exc)}
        out["heartbeat"] = {"age_s": None, "online": False}
        out["executor"] = {"lag_s": None, "due_steps": None}
        out["status"] = "down"
        return out
    ts = _parse((beat or {}).get("ts"))
    age = None if ts is None else round((now - ts).total_seconds(), 3)
    out["heartbeat"] = {"age_s": age, "online": age is not None and age < OFFLINE_AFTER_S}
    out["executor"] = executor
    degraded = not out["heartbeat"]["online"] or executor["lag_s"] > LAG_DEGRADED_S
    out["status"] = "degraded" if degraded else "ok"
    return out
