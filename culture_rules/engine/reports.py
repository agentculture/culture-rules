"""Mesh run reports: post a one-line summary of each finished run to a channel.

Optional and best-effort. A :class:`RunReporter` is handed terminal run documents (by the
executor's caller via :meth:`RunReporter.report`, or by reading the ``runs`` change feed
via :meth:`RunReporter.observe`) and posts through a tiny :class:`ChannelPoster` seam; the
real mesh client is wired elsewhere. A poster that raises is logged and swallowed - a
report can never fail, block or alter a run. Each run is reported at most once per
reporter, and a failed post is retried the next time the run is seen. Standard-library
only.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.store.port import StoragePort

log = logging.getLogger(__name__)

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


@runtime_checkable
class ChannelPoster(Protocol):
    """Posts text to a mesh channel."""

    def post(self, channel: str, text: str) -> None:
        """Post ``text`` to ``channel``; may raise."""


def summarize_run(doc: Mapping[str, Any]) -> str:
    """One-line summary of a run document."""
    steps = [s for s in doc.get("steps") or () if not s.get("loop")]
    ok = sum(1 for s in steps if s.get("status") == "succeeded")
    text = f"run {doc['id']} of rule {doc['rule']['id']} {doc['status']} ({ok}/{len(steps)} steps)"
    error = doc.get("error")
    if error:
        text += f": {error.get('code', 'error')}: {error.get('message', '')}".rstrip(": ")
    return text


class RunReporter:
    """Posts finished-run summaries; ``channels`` (per rule id) override ``channel``."""

    def __init__(
        self,
        poster: ChannelPoster,
        *,
        channel: str | None = None,
        channels: Mapping[str, str] | None = None,
    ) -> None:
        self._poster = poster
        self._channel = channel
        self._channels = dict(channels or {})
        self._reported: set[str] = set()

    def channel_for(self, rule_id: str) -> str | None:
        return self._channels.get(rule_id, self._channel)

    def report(self, doc: Mapping[str, Any]) -> bool:
        """Post the summary if the run is finished and a channel is configured."""
        try:
            if doc.get("status") not in TERMINAL or doc["id"] in self._reported:
                return False
            channel = self.channel_for(doc["rule"]["id"])
            if not channel:
                return False
            self._poster.post(channel, summarize_run(doc))
        except Exception as exc:  # noqa: BLE001 - a report must never fail a run
            log.warning("run report for %s not posted: %s", doc.get("id"), exc)
            return False
        self._reported.add(doc["id"])
        return True

    def observe(self, store: StoragePort, after: str) -> str:
        """Report every run finished after ``after``; return the token to resume from."""
        token = after
        for change in store.changes(RUNS_COLLECTION, after):
            token = change.token
            if change.document is not None:
                self.report(change.document)
        return token
