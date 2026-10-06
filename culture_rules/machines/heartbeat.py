"""Engine-node heartbeats and offline detection.

A heartbeat document (collection ``heartbeats``, id = machine name) carries
``machine``, ``ts`` (ISO UTC), ``load`` (``cpu``, ``mem``, optional ``gpu``),
``tools`` (probed tools -> bool), ``capabilities`` (what this engine node supports, e.g.
``variables``: it resolves shared variables) and ``engine_version``. A heartbeat without
``capabilities`` comes from a node that predates them. Beats are written every
10 s; absence for 30 s (3 missed beats) means offline, and placement skips it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from importlib import metadata
from typing import Any

from culture_rules.machines.enrol import enrolled_machines
from culture_rules.machines.probe import read_load
from culture_rules.model.machine import Machine
from culture_rules.store.port import StoreError, StoreOps

__all__ = [
    "HEARTBEAT_COLLECTION",
    "HEARTBEAT_INTERVAL_S",
    "OFFLINE_AFTER_S",
    "MISSED_BEATS_OFFLINE",
    "HeartbeatPublisher",
    "offline_after",
    "online_machines",
    "placeable_machines",
]

HEARTBEAT_COLLECTION = "heartbeats"
HEARTBEAT_INTERVAL_S = 10
MISSED_BEATS_OFFLINE = 3
OFFLINE_AFTER_S = HEARTBEAT_INTERVAL_S * MISSED_BEATS_OFFLINE


def offline_after(beat_every: float = HEARTBEAT_INTERVAL_S) -> float:
    """Seconds without a beat after which a machine counts offline (3 missed beats).

    The single definition used by placement, health, status and takeover; pass the
    cluster's real ``beat_every`` when it is not the default.
    """
    return MISSED_BEATS_OFFLINE * beat_every


Clock = Callable[[], datetime]

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _fmt(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _engine_version() -> str:
    try:
        return metadata.version("culture-rules")
    except metadata.PackageNotFoundError:
        return "unknown"


class HeartbeatPublisher:
    """Writes this node's heartbeat; inject ``clock`` and ``load_reader`` for tests."""

    def __init__(
        self,
        store: StoreOps,
        machine: str,
        *,
        tools: Mapping[str, bool] | None = None,
        clock: Clock = _now,
        load_reader: Callable[[], Mapping[str, float]] = read_load,
        engine_version: str | None = None,
        capabilities: tuple[str, ...] = (),
    ) -> None:
        self._store = store
        self.machine = machine
        self._tools = dict(tools or {})
        self._clock = clock
        self._load_reader = load_reader
        self._version = engine_version or _engine_version()
        self._capabilities = sorted(set(capabilities))

    def document(self) -> dict[str, Any]:
        return {
            "id": self.machine,
            "machine": self.machine,
            "ts": _fmt(self._clock()),
            "load": dict(self._load_reader()),
            "tools": dict(self._tools),
            "engine_version": self._version,
            "capabilities": list(self._capabilities),
        }

    def beat(self) -> dict[str, Any]:
        """Write one heartbeat (replacing the previous one) and return it."""
        return self._store.put(HEARTBEAT_COLLECTION, self.document())

    def run(
        self,
        *,
        sleep: Callable[[float], None] = time.sleep,
        max_beats: int | None = None,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> None:
        """Beat every :data:`HEARTBEAT_INTERVAL_S` until stopped or ``max_beats`` reached.

        A store error on one beat (a primary step-down, a transient write conflict) is
        logged and the cadence kept: the loop never ends on it, so the machine does not go
        offline for good. ``max_beats`` counts attempts, failed or not.
        """
        beats = 0
        while not should_stop():
            try:
                self.beat()
            except StoreError as exc:
                log.warning("heartbeat for %s not written: %s", self.machine, exc)
            beats += 1
            if max_beats is not None and beats >= max_beats:
                return
            sleep(HEARTBEAT_INTERVAL_S)


def online_machines(
    store: StoreOps, now: datetime, *, beat_every: float = HEARTBEAT_INTERVAL_S
) -> set[str]:
    """Names whose latest heartbeat is less than ``offline_after(beat_every)`` old."""
    limit = offline_after(beat_every)
    online: set[str] = set()
    for document in store.find(HEARTBEAT_COLLECTION):
        ts = _parse(document.get("ts"))
        name = document.get("machine")
        if ts is not None and isinstance(name, str):
            if (now - ts).total_seconds() < limit:
                online.add(name)
    return online


def placeable_machines(
    store: StoreOps,
    now: datetime,
    *,
    requirement: tuple[str, ...] | None = None,
    beat_every: float = HEARTBEAT_INTERVAL_S,
) -> list[Machine]:
    """Enrolled, enabled, online engine nodes offering every required capability."""
    online = online_machines(store, now, beat_every=beat_every)
    needed = set(requirement or ())
    return [
        m
        for m in enrolled_machines(store)
        if m.enabled
        and "engine_node" in m.roles
        and m.name in online
        and needed <= set(m.capabilities)
    ]
