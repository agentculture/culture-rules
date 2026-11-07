"""The Discord extension's health section: ``gateways`` in the node's health document.

Registered with :meth:`~culture_rules.extensions.Extension.health_section`; ``ops.health``
puts it after ``hooks`` and reports ``[]`` when the store is unreachable. Standard library
only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from culture_rules.engine.named_lease import LEASES_COLLECTION
from culture_rules.extensions.discord.gateway import GATEWAY_STATE_COLLECTION, LEASE_PREFIX

__all__ = ["gateways"]


def _parse(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def gateways(store: Any, now: datetime) -> list[dict[str, Any]]:
    """Discord gateway state per actor: lease holder (None once the lease lapsed), whether
    its listener is connected and when it last saw a message."""
    states = {str(d.get("id")): d for d in store.find(GATEWAY_STATE_COLLECTION)}
    out = []
    for lease in sorted(store.find(LEASES_COLLECTION), key=lambda d: str(d.get("id"))):
        lease_id = str(lease.get("id"))
        if not lease_id.startswith(LEASE_PREFIX):
            continue
        actor = lease_id[len(LEASE_PREFIX) :]
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
