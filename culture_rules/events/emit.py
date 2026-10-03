"""Emitted envelopes: correlationId / causationId / runId lineage.

Every envelope culture-rules emits is an events-cli wire-form envelope (camelCase
keys, ``evt_`` ULID id, RFC 3339 ``time``, ``schemaVersion`` "1") with its
lineage stamped:

- ``causationId`` - the id of the event that directly caused this one;
- ``correlationId`` - inherited from the cause (or the cause's own id when the
  cause has none), so a whole chain shares one correlation; a root event with
  no cause correlates to itself;
- ``runId`` - the run this event belongs to: an explicit ``run_id`` wins,
  otherwise it is inherited from the cause.

Publishing goes through an :class:`EventSink`; the events-cli sink lives in
:mod:`culture_rules.events.events_cli_adapter`. Standard-library only.
"""

from __future__ import annotations

import copy
import secrets
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

SCHEMA_VERSION = "1"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_event_id() -> str:
    """``evt_`` plus a 26-character Crockford ULID (48-bit ms time, 80 random bits)."""
    value = ((time.time_ns() // 1_000_000) & ((1 << 48) - 1)) << 80 | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "evt_" + "".join(reversed(chars))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _wire(cause: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept a wire envelope or a stored ``events`` document (``{"envelope": ...}``)."""
    inner = cause.get("envelope")
    return inner if isinstance(inner, Mapping) else cause


def derive_envelope(
    cause: Mapping[str, Any] | None,
    *,
    type: str,
    source: str,
    data: Mapping[str, Any] | None = None,
    run_id: str | None = None,
    id: str | None = None,
    time: str | None = None,
) -> dict[str, Any]:
    """Build a new wire-form envelope caused by ``cause`` (None for a root event)."""
    env: dict[str, Any] = {
        "id": id or new_event_id(),
        "type": type,
        "source": source,
        "time": time or _now(),
        "schemaVersion": SCHEMA_VERSION,
    }
    inherited_run = None
    if cause is None:
        env["correlationId"] = env["id"]
    else:
        wire = _wire(cause)
        cause_id = wire.get("id")
        if not isinstance(cause_id, str) or not cause_id:
            raise ValueError("the causing event has no id")
        env["correlationId"] = wire.get("correlationId") or cause_id
        env["causationId"] = cause_id
        inherited_run = wire.get("runId")
    run = run_id or inherited_run
    if run:
        env["runId"] = run
    env["data"] = copy.deepcopy(dict(data or {}))
    return env


@runtime_checkable
class EventSink(Protocol):
    """Where emitted envelopes go (events-cli in production)."""

    def publish(self, envelope: Mapping[str, Any]) -> None:
        """Publish one wire-form envelope; raise on failure."""


class Emitter:
    """Builds lineage-stamped envelopes from one ``source`` and publishes them."""

    def __init__(self, sink: EventSink, *, source: str) -> None:
        self.sink = sink
        self.source = source

    def emit(
        self,
        type: str,
        data: Mapping[str, Any] | None = None,
        *,
        cause: Mapping[str, Any] | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        env = derive_envelope(cause, type=type, source=self.source, data=data, run_id=run_id)
        self.sink.publish(env)
        return env
