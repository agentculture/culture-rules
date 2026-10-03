"""events-cli adapters: the durable-subscription :class:`EventSource` and the :class:`EventSink`.

events-cli (PyPI ``events-cli``, import ``events_cli``) owns the MQTT broker,
durable subscriptions and their history store; culture-rules only uses its
public API. It is an optional dependency - install ``culture-rules[events]`` -
and is imported lazily, so importing this module never imports events_cli or
paho-mqtt.

Source semantics
----------------
One durable subscription per host (:func:`subscription_name`). The cursor is
the events-cli history sequence for that subscription. ``drain(after)`` first
replays what events-cli already persisted past ``after`` (an event drained and
acknowledged before culture-rules stored it is recovered from there, not lost),
and only when history is caught up drains the broker session, which persists
each event before acknowledging it. Either way the batch is bounded by ``max``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any

from culture_rules.events.source import EventFabricError, SourceBatch

EXTRA_HINT = "install the optional extra: pip install 'culture-rules[events]'"
SUBSCRIPTION_PREFIX = "culture-rules-"
DEFAULT_PATTERN = "#"
DEFAULT_PUBLISH_WAIT = 5.0


def load_events_cli() -> Any:
    """Return the events-cli API surface the adapters use, importing it lazily."""
    try:
        from events_cli.core.envelope import Envelope
        from events_cli.core.topics import type_to_topic
        from events_cli.history import open_store
        from events_cli.subs import add_subscription, drain_subscription, get_subscription
    except ImportError as exc:
        raise EventFabricError(f"events-cli is not available ({exc}); {EXTRA_HINT}") from None
    return SimpleNamespace(
        Envelope=Envelope,
        type_to_topic=type_to_topic,
        open_store=open_store,
        add_subscription=add_subscription,
        drain_subscription=drain_subscription,
        get_subscription=get_subscription,
    )


def subscription_name(host: str) -> str:
    """The durable events-cli subscription name for ``host``."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be a non-empty string")
    slug = re.sub(r"[^a-z0-9-]+", "-", host.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"host {host!r} has no usable characters")
    return SUBSCRIPTION_PREFIX + slug


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
        pattern: str = DEFAULT_PATTERN,
        api: Any = None,
        history: Any = None,
        **drain_options: Any,
    ) -> None:
        self.name = name
        self.pattern = pattern
        self._api = api if api is not None else load_events_cli()
        self._history = history if history is not None else self._api.open_store()
        self._drain_options = drain_options  # address=, registry=, client_factory=, ...

    @classmethod
    def for_host(cls, host: str, **kwargs: Any) -> EventsCliSource:
        return cls(subscription_name(host), **kwargs)

    def ensure(self) -> None:
        """Register the durable subscription with events-cli if it is not registered yet."""
        registry = self._drain_options.get("registry")
        if self._api.get_subscription(self.name, registry=registry) is None:
            options = {k: v for k, v in self._drain_options.items() if k != "store"}
            self._api.add_subscription(self.name, self.pattern, **options)

    def drain(self, after: str | None, *, max: int, timeout: float) -> SourceBatch:
        since = _parse_cursor(after)
        page = self._history.read(self.name, since=since, max=max)
        if page.records:
            records, cursor, has_more = page.records, page.cursor, page.has_more
        else:
            result = self._api.drain_subscription(
                self.name,
                since=since,
                max=max,
                timeout=timeout,
                store=self._history,
                **self._drain_options,
            )
            records, cursor, has_more = result.records, result.cursor, result.has_more
        envelopes = tuple(record.envelope.to_dict() for record in records)
        out = after if cursor == since else str(cursor)
        return SourceBatch(envelopes=envelopes, cursor=out, has_more=bool(has_more))


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
        typed = self._envelope_cls.from_dict(dict(envelope))  # validates at the boundary
        result = self._client.publish_event(
            typed, self._topic_for(envelope["type"]), qos=1, wait=self._wait
        )
        if not getattr(result, "ok", False):
            reason = getattr(result, "reason", None) or "not confirmed"
            raise EventFabricError(f"publishing {envelope.get('id')!r} failed: {reason}")
