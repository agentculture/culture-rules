"""A fake :class:`~culture_rules.events.source.EventSource` for tests.

Behaves like an events-cli durable subscription: at-least-once (it can hand the
same envelope over more than once), bounded batches (``max``), and an opaque
monotonic cursor the caller persists and passes back as ``after``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from culture_rules.events.source import SourceBatch


def envelope(n: int | str, **extra: Any) -> dict[str, Any]:
    """A wire-form events-cli envelope with id ``evt_<n>``."""
    env = {
        "id": f"evt_{n}",
        "type": "task.requested",
        "source": "agent://tester",
        "time": "2026-10-03T12:00:00.000000Z",
        "schemaVersion": "1",
        "data": {"n": str(n), "nested": {"k": [1, 2]}},
    }
    env.update(extra)
    return env


class FakeEventSource:
    """A queue of envelopes per position; ``drain(after)`` returns those past ``after``."""

    def __init__(self, envelopes: list[Mapping[str, Any]] | None = None, *, name: str = "fake"):
        self.name = name
        self.log: list[Mapping[str, Any]] = list(envelopes or [])
        self.calls: list[dict[str, Any]] = []
        self.overdeliver = False  # misbehave: return more than ``max``

    def publish(self, *envs: Mapping[str, Any]) -> None:
        self.log.extend(envs)

    def drain(self, after: str | None, *, max: int, timeout: float) -> SourceBatch:
        self.calls.append({"after": after, "max": max, "timeout": timeout})
        start = 0 if after is None else int(after)
        limit = len(self.log) if self.overdeliver else start + max
        batch = self.log[start:limit]
        cursor = str(start + len(batch)) if batch else after
        return SourceBatch(
            envelopes=tuple(batch), cursor=cursor, has_more=start + len(batch) < len(self.log)
        )
