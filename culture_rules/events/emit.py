"""Emitted envelopes: correlationId / causationId / runId lineage.

Every envelope culture-rules emits is an events-cli wire-form envelope (camelCase
keys, ``evt_`` ULID id, RFC 3339 ``time``, ``schemaVersion`` "1") with its
lineage stamped:

- ``causationId`` - the id of the event that directly caused this one;
- ``correlationId`` - inherited from the cause (or the cause's own id when the
  cause has none), so a whole chain shares one correlation; a root event with
  no cause correlates to itself;
- ``runId`` - the run this event belongs to: an explicit ``run_id`` wins,
  otherwise it is inherited from the cause;
- ``hops`` - how many derivations separate this event from an external one: the
  cause's hops plus one (an explicit ``hops`` wins). A root event (no cause) carries
  none, which reads as 0 (:func:`event_hops`). The engine refuses to fire a rule on an
  event past :data:`MAX_EVENT_HOPS` (deviation d21: an event chain - a run's finish
  firing a rule whose run's finish fires another - always terminates).

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
MAX_EVENT_HOPS = 8
"""The deepest derived event a rule may fire on: an event whose :func:`event_hops` exceeds
it is refused (the skip ``hop_limit``, recorded on the rule's history, never fired). Eight
derivations allow a long chain of rules on ``rules.run.*`` events while bounding a loop
(two rules firing on each other's runs) to a few runs."""
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


INTERNAL_SOURCE_PREFIX = "culture-rules://"
"""Sources the engine writes straight into the store (run events, checks settle). The bus
may not carry them (:func:`reserved_reason`)."""
ENGINE_APP_SOURCE_PREFIX = "app://culture-rules/"
"""The source of what a node publishes on the bus (``app://culture-rules/<host>``,
:func:`engine_app_source`): it comes back through ingest, so it is not reserved."""
ENGINE_SOURCE_PREFIXES = (INTERNAL_SOURCE_PREFIX, ENGINE_APP_SOURCE_PREFIX)
"""Every source the engine itself produces. The engine always stamps ``hops`` on what it
derives, so a derived event from one of them without ``hops`` is malformed
(:func:`event_hops` fails closed)."""


def engine_app_source(host: str) -> str:
    """The source a node's emitter publishes under (one contract for producer and check)."""
    return f"{ENGINE_APP_SOURCE_PREFIX}{host}"


def is_engine_source(source: Any) -> bool:
    """Whether ``source`` is one the engine produces (:data:`ENGINE_SOURCE_PREFIXES`)."""
    return isinstance(source, str) and source.startswith(ENGINE_SOURCE_PREFIXES)


RUN_EVENT_ID_PREFIX = "runevt_"
RUN_EVENT_TYPE_PREFIX = "rules.run."


def is_stored_document(doc: Mapping[str, Any]) -> bool:
    """Whether ``doc`` is a stored ``events`` document (the storage boundary), not a wire
    envelope: it holds an ``envelope`` mapping whose id is its own id, and no wire ``type``.
    A wire envelope that merely carries an ``envelope`` field is *not* one."""
    inner = doc.get("envelope")
    return (
        isinstance(inner, Mapping)
        and "type" not in doc
        and isinstance(doc.get("id"), str)
        and doc.get("id") == inner.get("id")
    )


def wire_envelope(doc: Mapping[str, Any]) -> Mapping[str, Any]:
    """The wire envelope of a stored ``events`` document, or ``doc`` itself when it is a
    wire envelope (the only place a stored document is unwrapped)."""
    return doc["envelope"] if is_stored_document(doc) else doc


def event_hops(envelope: Mapping[str, Any]) -> int | None:
    """The hop count of the **wire** ``envelope`` (never unwrapped), or ``None`` - the
    caller then fails closed and treats it as past :data:`MAX_EVENT_HOPS` - when:

    * it is malformed (a string, a bool, a negative number);
    * the envelope carries an ``envelope`` field (ambiguous with a stored document);
    * it is absent on a derived event (a ``causationId``) from an engine source
      (:data:`ENGINE_SOURCE_PREFIXES`): the engine always stamps the hops it derives.

    Absent on a root event, or on an event from an outside source (whose producers do not
    count hops), it is 0."""
    if "envelope" in envelope:
        return None
    if "hops" not in envelope:
        derived = bool(envelope.get("causationId"))
        return None if derived and is_engine_source(envelope.get("source")) else 0
    hops = envelope["hops"]
    if isinstance(hops, bool) or not isinstance(hops, int) or hops < 0:
        return None
    return hops


def reserved_reason(envelope: Mapping[str, Any]) -> str | None:
    """Why ``envelope`` may not enter the store from the bus or a webhook, or ``None``.

    The run-event namespace (ids ``runevt_*``, types ``rules.run.*``) and the internal
    sources are written only by the engine itself (deviation d21): a copy from outside could
    otherwise squat a run's event id. An envelope carrying an ``envelope`` field is refused
    as ambiguous with a stored document."""
    if "envelope" in envelope:
        return "an envelope field makes it ambiguous with a stored event document"
    eid, kind, source = envelope.get("id"), envelope.get("type"), envelope.get("source")
    if isinstance(eid, str) and eid.startswith(RUN_EVENT_ID_PREFIX):
        return f"id prefix {RUN_EVENT_ID_PREFIX} is reserved for the engine's run events"
    if isinstance(kind, str) and kind.startswith(RUN_EVENT_TYPE_PREFIX):
        return f"type {RUN_EVENT_TYPE_PREFIX}* is reserved for the engine's run events"
    if isinstance(source, str) and source.startswith(INTERNAL_SOURCE_PREFIX):
        return f"source {INTERNAL_SOURCE_PREFIX}* is reserved for the engine"
    return None


def derive_envelope(
    cause: Mapping[str, Any] | None,
    *,
    type: str,
    source: str,
    data: Mapping[str, Any] | None = None,
    run_id: str | None = None,
    id: str | None = None,
    time: str | None = None,
    hops: int | None = None,
) -> dict[str, Any]:
    """Build a new wire-form envelope caused by ``cause`` (None for a root event).

    ``hops`` defaults to the cause's hops plus one (none on a root event); a cause whose
    hop count is malformed gives :data:`MAX_EVENT_HOPS` plus one, so whatever it causes is
    refused too (fail closed)."""
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
        inherited_run, hops = _inherit(env, cause, hops)
    if hops is not None:
        if isinstance(hops, bool) or not isinstance(hops, int) or hops < 0:
            raise ValueError("hops must be a non-negative int")
        env["hops"] = hops
    run = run_id or inherited_run
    if run:
        env["runId"] = run
    env["data"] = copy.deepcopy(dict(data or {}))
    return env


def _inherit(
    env: dict[str, Any], cause: Mapping[str, Any], hops: int | None
) -> tuple[Any, int | None]:
    """Stamp ``env``'s lineage from ``cause`` (correlation and causation ids); answer the
    cause's run id and the hop count (``hops``, else the cause's plus one, or past
    :data:`MAX_EVENT_HOPS` when the cause's is malformed)."""
    wire = wire_envelope(cause)
    cause_id = wire.get("id")
    if not isinstance(cause_id, str) or not cause_id:
        raise ValueError("the causing event has no id")
    env["correlationId"] = wire.get("correlationId") or cause_id
    env["causationId"] = cause_id
    if hops is None:
        cause_hops = event_hops(wire)
        hops = MAX_EVENT_HOPS + 1 if cause_hops is None else cause_hops + 1
    return wire.get("runId"), hops


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
