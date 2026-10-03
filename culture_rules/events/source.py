"""The :class:`EventSource` seam between the ingest loop and a durable subscription.

The ingest loop never talks to a transport. It asks a source for the next
bounded batch after an opaque cursor it persisted itself, stores the batch and
only then persists the new cursor. The production source is the events-cli
adapter (:mod:`culture_rules.events.events_cli_adapter`); tests use a fake.
Standard-library only.

Contract a source must honour
-----------------------------
- ``drain(after, max=, timeout=)`` returns at most ``max`` envelopes recorded
  after cursor ``after`` (``None`` = the subscription's start), waiting up to
  ``timeout`` seconds when nothing is queued. It never blocks unbounded.
- Delivery may be **at-least-once**: an envelope can be returned again (in a
  later batch, or by another host's source). The ingest dedupes on the
  envelope ``id``.
- ``SourceBatch.cursor`` is what to pass back as ``after`` next time. On an
  empty batch it is ``after`` unchanged, so a caller never rewinds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class EventFabricError(Exception):
    """An event source, sink or the ingest loop broke its contract or is unavailable."""


@dataclass(frozen=True)
class SourceBatch:
    """One bounded batch: wire-form envelopes, the resume cursor and whether more is queued."""

    envelopes: tuple[Mapping[str, Any], ...]
    cursor: str | None
    has_more: bool


@runtime_checkable
class EventSource(Protocol):
    """A durable subscription the ingest loop drains in bounded batches."""

    name: str
    """Stable name of the subscription; keys the persisted cursor."""

    def drain(self, after: str | None, *, max: int, timeout: float) -> SourceBatch:
        """Return up to ``max`` envelopes recorded after cursor ``after``."""
