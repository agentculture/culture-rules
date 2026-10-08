"""events-cli adapters: the durable-subscription :class:`EventSource` and the :class:`EventSink`.

events-cli (PyPI ``events-cli``, import ``events_cli``) owns the MQTT broker,
durable subscriptions and their history store; culture-rules only uses its
public API. It is an optional dependency - install ``culture-rules[events]`` -
and is imported lazily, so importing this module never imports events_cli or
paho-mqtt.

Source semantics
----------------
A host drains several durable subscriptions, one per pattern depth
(:func:`open_host_source`, :class:`EventsCliFanIn`); its cursor is a JSON object of
each subscription's events-cli history sequence, and a cursor of any other shape (a
legacy single-subscription one) starts fresh with one warning. Per subscription
(:class:`EventsCliSource`), ``drain(after)`` first
replays what events-cli already persisted past ``after`` (an event drained and
acknowledged before culture-rules stored it is recovered from there, not lost),
and only when history is caught up drains the broker session, which persists
each event before acknowledging it. Either way the batch is bounded by ``max``.

Subscribing to every event type
-------------------------------
events-cli has no catch-all pattern. A pattern is a dotted event type in which
``*`` stands for exactly one segment (``task.*`` compiles to ``events/task/+``),
and the raw MQTT filter characters ``#``, ``+`` and ``/`` are rejected with a
``SubscriptionValidationError``, so ``#`` can never be used. A subscription
holds one pattern. The node therefore registers one durable subscription per
type depth - ``*``, ``*.*``, ``*.*.*``, ... up to :data:`DEFAULT_DEPTH`
segments - and :class:`EventsCliFanIn` drains them as one :class:`EventSource`
with a composite cursor. Event types deeper than :data:`DEFAULT_DEPTH` segments
are not ingested.

Every events-cli error (``EventsError``: validation, broker, registry, drain)
is re-raised as :class:`EventFabricError`, which the node treats as "no event
source": it logs a warning and keeps running without ingest.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from culture_rules.events.emit import is_engine_source
from culture_rules.events.source import EventFabricError, SourceBatch

_log = logging.getLogger(__name__)
DEPTH_ENV = "CULTURE_RULES_EVENTS_DEPTH"
QUIET_LOGGERS = ("events_cli", "paho")

EXTRA_HINT = "install the optional extra: pip install 'culture-rules[events]'"
SUBSCRIPTION_PREFIX = "culture-rules-"
DEFAULT_DEPTH = 4
"""Deepest event type (in dotted segments) the default host subscriptions cover."""


def depth_patterns(depth: int = DEFAULT_DEPTH) -> tuple[str, ...]:
    """``("*", "*.*", ...)``: one events-cli pattern per type depth, 1..``depth``."""
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 1:
        raise ValueError("depth must be a positive int")
    return tuple(".".join("*" * n) for n in range(1, depth + 1))


def configured_depth() -> int:
    """Subscription depth from ``CULTURE_RULES_EVENTS_DEPTH``; invalid values use the default."""
    raw = os.environ.get(DEPTH_ENV)
    if raw is None:
        return DEFAULT_DEPTH
    try:
        depth = int(raw)
    except ValueError:
        depth = 0
    if depth < 1:
        _log.warning("%s=%r is not a positive integer; using %d", DEPTH_ENV, raw, DEFAULT_DEPTH)
        return DEFAULT_DEPTH
    return depth


DEFAULT_PATTERNS = depth_patterns()
"""The patterns a host subscribes to: together they match every type up to DEFAULT_DEPTH."""
DEFAULT_PUBLISH_WAIT = 5.0
MIN_DRAIN_TIMEOUT = 0.2
"""events-cli rejects a drain timeout <= 0; a caller's "do not wait" becomes this bound."""


def load_events_cli() -> Any:
    """Return the events-cli API surface the adapters use, importing it lazily."""
    try:
        from events_cli.core.envelope import Envelope
        from events_cli.core.errors import EventsError
        from events_cli.core.topics import type_to_topic
        from events_cli.history import open_store
        from events_cli.subs import add_subscription, drain_subscription, get_subscription
    except ImportError as exc:
        raise EventFabricError(f"events-cli is not available ({exc}); {EXTRA_HINT}") from None
    return SimpleNamespace(
        Envelope=Envelope,
        EventsError=EventsError,
        type_to_topic=type_to_topic,
        open_store=open_store,
        add_subscription=add_subscription,
        drain_subscription=drain_subscription,
        get_subscription=get_subscription,
    )


def open_client(**kwargs: Any) -> Any:
    """An events-cli ``EventClient`` for the configured broker (``EVENTS_BROKER_*``).

    The client connects in the background and never raises at runtime; construction raises
    :class:`EventFabricError` when events-cli (or its MQTT dependency) is not installed.
    """
    try:
        from events_cli import EventClient

        return EventClient(**kwargs)
    except ImportError as exc:
        raise EventFabricError(f"events-cli is not available ({exc}); {EXTRA_HINT}") from None


def subscription_name(host: str) -> str:
    """The durable events-cli subscription name for ``host``."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be a non-empty string")
    slug = re.sub(r"[^a-z0-9-]+", "-", host.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"host {host!r} has no usable characters")
    return SUBSCRIPTION_PREFIX + slug


@contextmanager
def translated(api: Any, label: str) -> Iterator[None]:
    """Re-raise events-cli's own errors (``api.EventsError``) as :class:`EventFabricError`."""
    events_error = getattr(api, "EventsError", None)
    if not isinstance(events_error, type) or not issubclass(events_error, Exception):
        events_error = ()  # an API surface without one (a test fake): nothing to translate
    try:
        yield
    except events_error as exc:
        hint = getattr(exc, "remediation", "")
        detail = f"{exc} ({hint})" if hint else str(exc)
        raise EventFabricError(f"{label}: {detail}") from exc


def _parse_cursor(after: str | None) -> int:
    if after is None:
        return 0
    try:
        value = int(after)
    except (TypeError, ValueError):
        raise EventFabricError(f"not an events-cli cursor: {after!r}") from None
    if value < 0:
        raise EventFabricError(f"not an events-cli cursor: {after!r}")
    return value


class EventsCliSource:
    """A host's durable events-cli subscription as an :class:`EventSource`."""

    def __init__(
        self,
        name: str,
        *,
        pattern: str,
        api: Any = None,
        history: Any = None,
        **drain_options: Any,
    ) -> None:
        self.name = name
        self.pattern = pattern
        self._api = api if api is not None else load_events_cli()
        if history is None:
            with translated(self._api, "events-cli history store"):
                history = self._api.open_store()
        self._history = history
        self._drain_options = drain_options  # address=, registry=, client_factory=, ...

    @classmethod
    def for_host(cls, host: str, *, pattern: str, **kwargs: Any) -> EventsCliSource:
        return cls(subscription_name(host), pattern=pattern, **kwargs)

    def ensure(self) -> None:
        """Register the durable subscription with events-cli if it is not registered yet."""
        registry = self._drain_options.get("registry")
        with translated(self._api, f"subscription {self.name!r}"):
            if self._api.get_subscription(self.name, registry=registry) is None:
                options = {k: v for k, v in self._drain_options.items() if k != "store"}
                self._api.add_subscription(self.name, self.pattern, **options)

    def drain(self, after: str | None, *, max: int, timeout: float) -> SourceBatch:
        with translated(self._api, f"subscription {self.name!r}"):
            return self._drain(after, max=max, timeout=timeout)

    def _drain(self, after: str | None, *, max: int, timeout: float) -> SourceBatch:
        since = _parse_cursor(after)
        page = self._history.read(self.name, since=since, max=max)
        if page.records:
            records, cursor, has_more = page.records, page.cursor, page.has_more
        else:
            # the history store is always the source's own; a caller's store= is dropped, as
            # in ensure(), instead of colliding with it
            options = {k: v for k, v in self._drain_options.items() if k != "store"}
            result = self._api.drain_subscription(
                self.name,
                since=since,
                max=max,
                timeout=timeout if timeout > MIN_DRAIN_TIMEOUT else MIN_DRAIN_TIMEOUT,
                store=self._history,
                **options,
            )
            records, cursor, has_more = result.records, result.cursor, result.has_more
        envelopes = tuple(from_bus(record.envelope.to_dict()) for record in records)
        out = after if cursor == since else str(cursor)
        return SourceBatch(envelopes=envelopes, cursor=out, has_more=bool(has_more))


def _parse_fan_in_cursor(after: str | None, names: Sequence[str]) -> dict[str, str]:
    return _read_fan_in_cursor(after, names)[0]


def _read_fan_in_cursor(after: str | None, names: Sequence[str]) -> tuple[dict[str, str], bool]:
    """``(cursors, valid)``: the per-subscription cursors in ``after``, and whether ``after``
    was a fan-in cursor at all (``None`` counts as valid: nothing to reset)."""
    if after is None:
        return {}, True
    try:
        value = json.loads(after)
    except (TypeError, ValueError):
        value = None
    if (
        not isinstance(value, dict)
        or not set(value) <= set(names)
        or not all(isinstance(v, str) for v in value.values())
    ):
        # e.g. a legacy single-subscription cursor ("12"): start fresh rather than wedge ingest
        _log.warning("ignoring a cursor that is not a fan-in cursor (%r); starting fresh", after)
        return {}, False
    return dict(value), True


class EventsCliFanIn:
    """Several durable subscriptions drained as one :class:`EventSource`.

    The cursor is a JSON object mapping each subscription name to its own events-cli
    cursor; a cursor of any other shape (a legacy single-subscription one) starts fresh
    with a warning. A batch never holds more than ``max`` envelopes; the subscription
    drained first rotates from call to call so a busy one cannot starve the others, and
    only the first one drained in a call waits up to ``timeout`` (the others drain for the
    shortest time events-cli allows, :data:`MIN_DRAIN_TIMEOUT`). When ``timeout`` is itself
    at most :data:`MIN_DRAIN_TIMEOUT`, every source is clamped to that bound.
    """

    def __init__(self, name: str, sources: Sequence[EventsCliSource]) -> None:
        if not sources:
            raise ValueError("a fan-in needs at least one source")
        self.name = name
        self.sources = tuple(sources)
        self._next = 0

    def ensure(self) -> None:
        """Register every subscription that is not registered yet."""
        for source in self.sources:
            source.ensure()

    def drain(self, after: str | None, *, max: int, timeout: float) -> SourceBatch:
        cursors, valid = _read_fan_in_cursor(after, [s.name for s in self.sources])
        start = dict(cursors)
        count = len(self.sources)
        order = [self.sources[(self._next + i) % count] for i in range(count)]
        self._next = (self._next + 1) % count
        envelopes: list[Mapping[str, Any]] = []
        has_more = False
        wait = timeout
        for source in order:
            remaining = max - len(envelopes)
            if remaining <= 0:
                has_more = True
                break
            batch = source.drain(cursors.get(source.name), max=remaining, timeout=wait)
            wait = 0.0
            envelopes.extend(batch.envelopes[:remaining])
            if batch.cursor is not None:
                cursors[source.name] = batch.cursor
            has_more = has_more or batch.has_more
        if valid and cursors == start:
            out = after  # nothing moved
        else:  # moved, or a reset cursor: persist the fresh state so it warns only once
            out = json.dumps(cursors, sort_keys=True, separators=(",", ":"))
        return SourceBatch(envelopes=tuple(envelopes), cursor=out, has_more=has_more)


def open_host_source(
    host: str, *, patterns: Sequence[str] | None = None, **kwargs: Any
) -> EventsCliFanIn:
    """This host's event source: one durable subscription per pattern, drained together.

    The subscription for ``patterns[i]`` is named ``culture-rules-<host>-d<i+1>``; the
    fan-in itself (and so the ingest cursor slot) is ``culture-rules-<host>``. Without
    ``patterns``, the depth comes from ``CULTURE_RULES_EVENTS_DEPTH`` (default 4).
    """
    for logger_name in QUIET_LOGGERS:  # the per-cycle connect/disconnect INFO lines
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    if patterns is None:
        patterns = depth_patterns(configured_depth())
    name = subscription_name(host)
    api = kwargs.pop("api", None)
    if api is None:
        api = load_events_cli()
    history = kwargs.pop("history", None)
    if history is None:
        with translated(api, "events-cli history store"):
            history = api.open_store()
    sources = [
        EventsCliSource(f"{name}-d{i}", pattern=pattern, api=api, history=history, **kwargs)
        for i, pattern in enumerate(patterns, start=1)
    ]
    return EventsCliFanIn(name, sources)


BUS_HOPS_KEY = "_culture_rules_hops"
"""Where an envelope's ``hops`` rides on the bus: events-cli's envelope has no ``hops``
field and refuses unknown ones, so :func:`to_bus` moves it into ``data`` under this key and
:func:`from_bus` moves it back (d21)."""


def to_bus(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """``envelope`` as events-cli accepts it: a ``hops`` field moves into ``data``."""
    env = dict(envelope)
    if "hops" not in env:
        return env
    env["data"] = {**dict(env.get("data") or {}), BUS_HOPS_KEY: env.pop("hops")}
    return env


def from_bus(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """A drained ``envelope`` with its ``hops`` back as a field - only on an engine event
    (:func:`~culture_rules.events.emit.is_engine_source`; no outside producer counts hops).
    The value moves as is, so a malformed one still fails closed in ``event_hops``."""
    env = dict(envelope)
    data = env.get("data")
    if not is_engine_source(env.get("source")) or not isinstance(data, Mapping):
        return env
    if BUS_HOPS_KEY not in data:
        return env
    rest = dict(data)
    env["hops"] = rest.pop(BUS_HOPS_KEY)
    env["data"] = rest
    return env


class EventsCliSink:
    """Publishes wire-form envelopes through an events-cli ``EventClient`` at QoS 1."""

    def __init__(
        self,
        client: Any,
        *,
        envelope_cls: Any = None,
        topic_for: Callable[[str], str] | None = None,
        wait: float = DEFAULT_PUBLISH_WAIT,
    ) -> None:
        if envelope_cls is None or topic_for is None:
            api = load_events_cli()
            envelope_cls = envelope_cls or api.Envelope
            topic_for = topic_for or api.type_to_topic
        self._client = client
        self._envelope_cls = envelope_cls
        self._topic_for = topic_for
        self._wait = wait

    def publish(self, envelope: Mapping[str, Any]) -> None:
        typed = self._envelope_cls.from_dict(to_bus(envelope))  # validates at the boundary
        result = self._client.publish_event(
            typed, self._topic_for(envelope["type"]), qos=1, wait=self._wait
        )
        if not getattr(result, "ok", False):
            reason = getattr(result, "reason", None) or "not confirmed"
            raise EventFabricError(f"publishing {envelope.get('id')!r} failed: {reason}")
