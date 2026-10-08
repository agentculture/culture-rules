"""Node health status as a pure function (no web imports).

``health_status(store, now, host)`` reports: whether the store is reachable and its
schema version; this host's heartbeat age and online state; and executor lag (how long
due work in live runs - pending steps that are ready to dispatch, aged from when they
became ready, and retries past ``next_attempt_at`` - has been waiting; see
:func:`culture_rules.engine.runs.due_steps`). ``hooks``
counts webhook deliveries per surface, actor and outcome and ``gateways`` reports each Discord
gateway's lease holder, connected flag and last event time. ``chain_needs_review`` counts the
chain continuations a node could not recover (d21,
:data:`~culture_rules.node.chain.CHAIN_NEEDS_REVIEW`). The overall ``status`` is ``ok``,
``degraded`` (stale/missing heartbeat or lag above :data:`LAG_DEGRADED_S`) or ``down`` (store
unreachable). It never raises. Standard-library only; the HTTP route lives in the server package.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from culture_rules.apps.discord_gateway import GATEWAY_STATE_COLLECTION
from culture_rules.engine.named_lease import LEASES_COLLECTION
from culture_rules.engine.runs import RUNS_COLLECTION, due_steps
from culture_rules.events.hook_sink import HOOK_STATS_COLLECTION
from culture_rules.machines.heartbeat import (
    HEARTBEAT_COLLECTION,
    HEARTBEAT_INTERVAL_S,
    offline_after,
)
from culture_rules.node.chain import CHAIN_NEEDS_REVIEW

__all__ = ["LAG_DEGRADED_S", "gateways", "health_status", "hook_counts"]

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


def hook_counts(store: Any) -> dict[str, dict[str, dict[str, int]]]:
    """``{surface: {actor id: {outcome: count}}}`` from ``hook_stats`` (counts only)."""
    out: dict[str, dict[str, dict[str, int]]] = {}
    for doc in store.find(HOOK_STATS_COLLECTION):
        surface = str(doc.get("surface") or "unknown")
        actor = str(doc.get("actor") or "unknown")
        out.setdefault(surface, {}).setdefault(actor, {})[str(doc.get("outcome"))] = int(
            doc.get("count") or 0
        )
    return out


def gateways(store: Any, now: datetime) -> list[dict[str, Any]]:
    """Discord gateway state per actor: lease holder (None once the lease lapsed), whether
    its listener is connected and when it last saw a message."""
    prefix = "discord-gateway:"
    states = {str(d.get("id")): d for d in store.find(GATEWAY_STATE_COLLECTION)}
    out = []
    for lease in sorted(store.find(LEASES_COLLECTION), key=lambda d: str(d.get("id"))):
        lease_id = str(lease.get("id"))
        if not lease_id.startswith(prefix):
            continue
        actor = lease_id[len(prefix) :]
        expires = _parse(lease.get("expires_at"))
        live = lease.get("holder") if expires is not None and expires > now else None
        state = states.get(actor) or {}
        out.append(
            {
                "actor": actor,
                "holder": live,
                "connected": bool(live) and bool(state.get("connected")),
                "last_event_at": state.get("last_event_at"),
                "lease_expires_at": lease.get("expires_at"),
            }
        )
    return out


def health_status(
    store: Any, now: datetime, host: str, *, beat_every: float = HEARTBEAT_INTERVAL_S
) -> dict[str, Any]:
    """Health document for the node ``host``; JSON-serialisable.

    ``beat_every`` is the cluster's heartbeat cadence (offline after 3 missed beats)."""
    out: dict[str, Any] = {"host": host, "at": now.astimezone(UTC).isoformat()}
    try:
        beat = store.get(HEARTBEAT_COLLECTION, host)
        executor = _executor(store, now)
        hooks = hook_counts(store)
        gateway_list = gateways(store, now)
        stuck = len(store.find(CHAIN_NEEDS_REVIEW))
        version = getattr(store, "node_schema_version", None)
        out["store"] = {
            "reachable": True,
            "schema_version": None if version is None else str(version),
        }
    except Exception as exc:  # noqa: BLE001 - health must report and never raise
        out["store"] = {"reachable": False, "schema_version": None, "error": str(exc)}
        out["heartbeat"] = {"age_s": None, "online": False}
        out["executor"] = {"lag_s": None, "due_steps": None}
        out["hooks"] = {}
        out["gateways"] = []
        out["chain_needs_review"] = None
        out["status"] = "down"
        return out
    ts = _parse((beat or {}).get("ts"))
    age = None if ts is None else round((now - ts).total_seconds(), 3)
    out["heartbeat"] = {"age_s": age, "online": age is not None and age < offline_after(beat_every)}
    out["executor"] = executor
    out["hooks"] = hooks
    out["gateways"] = gateway_list
    out["chain_needs_review"] = stuck
    degraded = not out["heartbeat"]["online"] or executor["lag_s"] > LAG_DEGRADED_S
    out["status"] = "degraded" if degraded else "ok"
    return out
