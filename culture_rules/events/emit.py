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
QUEUE_EVENT_TYPE_PREFIX = "rules.queue."
QUEUE_EVENT_ID_PREFIX = "queue_"
QUEUE_SOURCE = f"{INTERNAL_SOURCE_PREFIX}queue"
"""The fixer queue's events (#35, d29: ``rules.queue.dispatch``, :mod:`culture_rules.node.
actions.queue`): written only by the ``queue.progress`` built-in, straight into the store;
their types and ids are reserved at external ingest like the run events'."""
CHECKS_SETTLED_TYPE = "github.pr.checks_settled"
CHECKS_LATE_TYPE = "github.pr.checks_failed_late"
PR_CONFLICTING_TYPE = "github.pr.conflicting"
SETTLE_TYPES = frozenset((CHECKS_SETTLED_TYPE, CHECKS_LATE_TYPE, PR_CONFLICTING_TYPE))
SETTLE_TYPE_NAMES = (CHECKS_SETTLED_TYPE, CHECKS_LATE_TYPE, PR_CONFLICTING_TYPE)
"""The checks settler's event types (:mod:`culture_rules.node.checks_settle`) and the
conflict watch's (d31, :mod:`culture_rules.node.conflict_watch`): written only by the
engine, from its internal sources; reserved at external ingest (d25)."""
SETTLED_ID_PREFIX = "settled_"
LATE_ID_PREFIX = "late_"
CONFLICT_ID_PREFIX = "conflict_"
SETTLE_ID_PREFIXES = (SETTLED_ID_PREFIX, LATE_ID_PREFIX, CONFLICT_ID_PREFIX)
TRIGGER_EVENT_KINDS = frozenset(("schedule", "probe"))
"""Kinds and types of the events the scheduler and the probe stage write straight into the
store (:mod:`culture_rules.node.schedule`, :mod:`culture_rules.node.probe_trigger`)."""
TRIGGER_EVENT_KIND_NAMES = ("schedule", "probe")
TRIGGER_EVENT_SOURCES = ("culture-rules/schedule", "culture-rules/probe")
TRIGGER_ID_PREFIXES = ("schedule/", "probe/")
"""Their sources and deterministic id prefixes; all reserved at external ingest."""
"""The settler's deterministic event id prefixes; reserved so no copy can squat them (d25)."""


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

    The run-event namespace (ids ``runevt_*``, types ``rules.run.*``), the fixer queue's
    (ids ``queue_*``, types ``rules.queue.*``, #35), the checks settler's
    namespace and the conflict watch's (types :data:`SETTLE_TYPES`, ids ``settled_*`` /
    ``late_*`` / ``conflict_*``, d25, d31), the
    schedule and probe namespace (kind or type ``schedule`` / ``probe``, sources
    :data:`TRIGGER_EVENT_SOURCES`, ids ``schedule/*`` / ``probe/*``) and the internal
    sources are written only by the engine itself (deviation d21): a copy from outside
    could otherwise fire a rule or squat a deterministic event id. An envelope
    carrying an ``envelope`` field is refused as ambiguous with a stored document, and one
    in which a field the reservation reads (:data:`CHECKED_FIELDS`) is present - even as
    ``null`` - but not a non-empty string as malformed; an absent field keeps today's
    handling. Every field is validated first, in one place, so no later membership test
    ever sees a non-string; an envelope with any text a store cannot hold (not UTF-8
    encodable: a lone surrogate, in any field or in ``data``) is refused too. Never
    raises."""
    if "envelope" in envelope:
        return "an envelope field makes it ambiguous with a stored event document"
    shape = envelope_shape_problem(envelope)
    if shape is not None:
        return shape
    malformed = _malformed_field(envelope)
    if malformed is not None:
        return f"{malformed} must be a non-empty string"  # never raise on it, never store it
    eid, kind, source = envelope.get("id"), envelope.get("type"), envelope.get("source")
    if isinstance(eid, str) and eid.startswith(RUN_EVENT_ID_PREFIX):
        return f"id prefix {RUN_EVENT_ID_PREFIX} is reserved for the engine's run events"
    if isinstance(kind, str) and kind.startswith(RUN_EVENT_TYPE_PREFIX):
        return f"type {RUN_EVENT_TYPE_PREFIX}* is reserved for the engine's run events"
    if isinstance(kind, str) and kind.startswith(QUEUE_EVENT_TYPE_PREFIX):
        return f"type {QUEUE_EVENT_TYPE_PREFIX}* is reserved for the engine's queue"
    if isinstance(eid, str) and eid.startswith(QUEUE_EVENT_ID_PREFIX):
        return f"id prefix {QUEUE_EVENT_ID_PREFIX} is reserved for the engine's queue"
    if isinstance(source, str) and source.startswith(INTERNAL_SOURCE_PREFIX):
        return f"source {INTERNAL_SOURCE_PREFIX}* is reserved for the engine"
    return _settle_reserved(eid, kind) or _trigger_reserved(envelope)


CHECKED_FIELDS = ("id", "type", "kind", "source")
"""The envelope fields :func:`reserved_reason` reads; each must be a non-empty string when
present."""


MAX_ENVELOPE_DEPTH = 32
"""The deepest nesting of objects and arrays an envelope may have (the envelope itself is
level 1); deeper is refused before anything walks it recursively."""
_INT64 = (-(2**63), 2**63 - 1)


def _text_problem(text: str, what: str) -> str | None:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return f"{what} is not valid UTF-8 (a lone surrogate) and cannot be stored"
    return None


def _key_problem(key: Any) -> str | None:
    """Why a document key cannot be stored: not text, a NUL (BSON keys are C strings), a
    leading ``$`` (an operator name), or not UTF-8."""
    if not isinstance(key, str):
        return "an object key is not a string"
    if "\x00" in key:
        return "an object key contains NUL"
    if key.startswith("$"):
        return "an object key starts with $"
    return _text_problem(key, "an object key")


def _scalar_problem(value: Any) -> str | None:
    if isinstance(value, str):
        return _text_problem(value, "a string")
    if isinstance(value, int) and not isinstance(value, bool):
        if not _INT64[0] <= value <= _INT64[1]:
            return "an integer is outside signed 64-bit"
    return None


def envelope_shape_problem(envelope: Any) -> str | None:
    """Why ``envelope`` (any JSON-shaped value) cannot be stored as is, or ``None``: nested
    deeper than :data:`MAX_ENVELOPE_DEPTH`, a key :func:`_key_problem` refuses, text that is
    not UTF-8, an int outside signed 64-bit. Iterative - never recursion - so any depth is
    safe to check; NaN and infinities are storable (BSON doubles)."""
    stack: list[tuple[Any, int]] = [(envelope, 1)]
    while stack:
        value, depth = stack.pop()
        if isinstance(value, Mapping | list | tuple) and depth > MAX_ENVELOPE_DEPTH:
            return f"nested deeper than {MAX_ENVELOPE_DEPTH} levels"
        problem, children = _children(value, depth + 1)
        if problem is not None:
            return problem
        stack.extend(children)
    return None


def _children(value: Any, depth: int) -> tuple[str | None, list[tuple[Any, int]]]:
    """``(problem, children at depth)`` of one value: a mapping's keys are checked, a
    scalar is checked, a container's items are its children."""
    if isinstance(value, Mapping):
        for key in value:
            problem = _key_problem(key)
            if problem is not None:
                return problem, []
        return None, [(item, depth) for item in value.values()]
    if isinstance(value, list | tuple):
        return None, [(item, depth) for item in value]
    return _scalar_problem(value), []


def _malformed_field(envelope: Mapping[str, Any]) -> str | None:
    """The first of :data:`CHECKED_FIELDS` present but not a non-empty string, else None."""
    for field in CHECKED_FIELDS:
        if field in envelope and not (isinstance(envelope[field], str) and envelope[field]):
            return field
    return None


def _settle_reserved(eid: Any, kind: Any) -> str | None:
    """Why an id or type in the checks settler's namespace is refused (d25), or ``None``."""
    if kind in SETTLE_TYPE_NAMES:
        return f"type {kind} is reserved for the engine's checks settle"
    if isinstance(eid, str) and eid.startswith(SETTLE_ID_PREFIXES):
        return "id prefixes settled_, late_ and conflict_ are reserved for the engine"
    return None


def _trigger_reserved(envelope: Mapping[str, Any]) -> str | None:
    """Why a schedule or probe event (kind, type, source or id) is refused, or ``None``:
    only the engine writes them, so a copy could fire such a rule or squat a slot's id."""
    kinds = (envelope.get("kind"), envelope.get("type"))  # strings or absent (validated)
    if any(k in TRIGGER_EVENT_KIND_NAMES for k in kinds):
        return "kinds and types schedule and probe are reserved for the engine"
    source, eid = envelope.get("source"), envelope.get("id")
    if isinstance(source, str) and any(
        source == s or source.startswith(s + "/") for s in TRIGGER_EVENT_SOURCES
    ):
        return "sources culture-rules/schedule and culture-rules/probe are reserved"
    if isinstance(eid, str) and eid.startswith(TRIGGER_ID_PREFIXES):
        return "id prefixes schedule/ and probe/ are reserved for the engine"
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
