"""Server-sent events driven by the store change feeds (standard-library only).

There is no in-process fan-out: each open stream owns a private cursor per collection (its
own local variables) and polls ``store.changes``. Any API instance on the same store
therefore streams every committed write, whichever instance made it. The SSE ``id`` of an
event is the JSON map of all current cursors, so a reconnecting client's ``Last-Event-ID``
resumes every collection exactly where it stopped.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

from culture_rules.store.port import Change, StoragePort

__all__ = ["STREAMABLE", "parse_cursors", "stream_changes"]

STREAMABLE: tuple[str, ...] = (
    "rules",
    "workflows",
    "actors",
    "machines",
    "runs",
    "controls",
    "audit",
    "asks",
    "heartbeats",
    "rule_decisions",
)
POLL_INTERVAL_S = 0.2
KEEPALIVE_S = 15.0


def parse_cursors(raw: str | None) -> dict[str, str]:
    """Parse an ``after`` / ``Last-Event-ID`` value: a JSON object collection -> token."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"cursor is not JSON: {exc.msg}") from exc
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError("cursor must be a JSON object of collection -> token strings")
    return data


def _drain(store: StoragePort, collection: str, token: str) -> list[Change]:
    return list(store.changes(collection, token, timeout=0.0))


def _frame(event: str, data: Any, event_id: str | None = None) -> str:
    lines = [f"event: {event}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append("data: " + json.dumps(data, sort_keys=True, separators=(",", ":"), default=str))
    return "\n".join(lines) + "\n\n"


async def stream_changes(
    store: StoragePort,
    collections: Sequence[str],
    after: dict[str, str] | None = None,
    *,
    max_events: int | None = None,
    max_seconds: float = 300.0,
    poll_interval: float = POLL_INTERVAL_S,
) -> AsyncIterator[str]:
    """Yield SSE frames for changes after ``after`` (default: from now), until a limit."""
    cursors = {
        c: (after or {}).get(c) or await asyncio.to_thread(store.head, c) for c in collections
    }
    yield "retry: 2000\n\n"
    started = last_sent = time.monotonic()
    sent = 0
    while time.monotonic() - started < max_seconds:
        batches = await asyncio.gather(
            *(asyncio.to_thread(_drain, store, c, cursors[c]) for c in collections)
        )
        found = False
        for collection, changes in zip(collections, batches, strict=True):
            for change in changes:
                found = True
                cursors[collection] = change.token
                payload = {
                    "collection": change.collection,
                    "op": change.op,
                    "id": change.id,
                    "document": change.document,
                }
                yield _frame("change", payload, json.dumps(cursors, sort_keys=True))
                last_sent = time.monotonic()
                sent += 1
                if max_events is not None and sent >= max_events:
                    return
        if not found:
            if time.monotonic() - last_sent >= KEEPALIVE_S:
                yield ": keepalive\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(poll_interval)
