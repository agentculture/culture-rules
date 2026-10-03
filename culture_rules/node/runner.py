"""What ``culture-rules node run`` needs: open the store, the event source, the ports.

The node talks to the store directly - it *is* the engine, not an API client. This is the
only CLI verb that does; the CLI module imports this package lazily, inside the handler.
Every seam here is a module-level function so tests (and embedders) can replace it.

Production wiring done by :func:`run_node`:

* **Human asks** - :func:`open_emitter` publishes through events-cli when it is installed
  and otherwise records each emitted envelope (e.g. ``human.ask.requested``) into the
  store's ``events`` collection, so ``human`` actors always get a
  :class:`~culture_rules.actors.human.HumanAdapter`.
* **Run reports** - :func:`open_reporter` posts finished-run summaries to
  ``CULTURE_RULES_REPORT_CHANNEL`` (unset: no reporter) through the agent mesh
  (``culture channel message``) when ``culture`` is on PATH, else to the log.
* **Logs** - :func:`~culture_rules.ops.logs.configure_logging` with the node's host.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess  # nosec B404 - argv list only, never a shell (MeshPoster)
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.reports import RunReporter
from culture_rules.events.emit import Emitter
from culture_rules.events.ingest import EVENTS_COLLECTION, event_document
from culture_rules.events.source import EventFabricError, EventSource
from culture_rules.ops.logs import configure_logging
from culture_rules.ops.nodename import node_name
from culture_rules.store.port import DuplicateKeyError, StoragePort, StoreError

__all__ = [
    "REPORT_CHANNEL_ENV",
    "LoggingPoster",
    "MeshPoster",
    "NodeSetupError",
    "NoopAction",
    "StoreEventSink",
    "default_host",
    "default_ports",
    "open_emitter",
    "open_event_source",
    "open_reporter",
    "open_store",
    "run_node",
]

REPORT_CHANNEL_ENV = "CULTURE_RULES_REPORT_CHANNEL"
"""Mesh channel finished-run summaries are posted to (unset: no run reports)."""
MESH_POST_TIMEOUT_S = 15.0

log = logging.getLogger("culture_rules.node")


class NodeSetupError(RuntimeError):
    """The node cannot start: the store is not configured or not reachable."""


def default_host() -> str:
    """This machine's node name: ``CULTURE_RULES_NODE_NAME``, else the short hostname."""
    return node_name()


def open_store() -> StoragePort:
    """The MongoDB store configured by ``CULTURE_RULES_MONGO_*`` (needs the ``store`` extra).

    Raises :class:`~culture_rules.store.port.StoreError` (``ConfigError``) when unset.
    """
    from culture_rules.store.mongo import MongoConfig, MongoStore  # noqa: PLC0415 - lazy

    return MongoStore(MongoConfig.from_env())


def open_event_source(host: str) -> EventSource | None:
    """This host's durable events-cli subscriptions, or None when they cannot be set up.

    Any setup failure - events-cli missing, a subscription it rejects, the broker
    unreachable - degrades the node to running without ingest instead of crashing it.
    """
    from culture_rules.events.events_cli_adapter import open_host_source  # noqa: PLC0415

    try:
        source = open_host_source(host)
        source.ensure()
    except EventFabricError as exc:
        log.warning("no event source for %s: %s", host, exc)
        return None
    return source


class StoreEventSink:
    """Records emitted envelopes into the store's ``events`` collection (no event bus).

    The stored document has the ingest shape, so triggers, replay and the asks UI read it
    like any ingested event; a re-published envelope id is stored once.
    """

    def __init__(self, store: Any, host: str) -> None:
        self._store = store
        self._host = host

    def publish(self, envelope: Mapping[str, Any]) -> None:
        try:
            self._store.insert(EVENTS_COLLECTION, event_document(envelope, host=self._host))
        except DuplicateKeyError:
            pass


def _events_cli_client() -> Any:
    """An events-cli publish client for the configured broker (needs the ``events`` extra)."""
    from culture_rules.events.events_cli_adapter import open_client  # noqa: PLC0415

    return open_client()


def open_emitter(store: Any, host: str) -> Emitter:
    """The node's emitter: events-cli when available, else recorded into the store."""
    from culture_rules.events.events_cli_adapter import EventsCliSink  # noqa: PLC0415

    source = f"app://culture-rules/{host}"
    try:
        sink: Any = EventsCliSink(_events_cli_client())
    except Exception as exc:  # noqa: BLE001 - any events-cli/paho setup failure: fall back
        log.warning("no event bus for %s (%s); recording emitted events in the store", host, exc)
        sink = StoreEventSink(store, host)
    return Emitter(sink, source=source)


class LoggingPoster:
    """A :class:`~culture_rules.engine.reports.ChannelPoster` that only logs the report."""

    def post(self, channel: str, text: str) -> None:
        log.info("run report for %s: %s", channel, text)


class MeshPoster:
    """Posts to a Culture mesh channel with ``culture channel message <channel> <text>``."""

    def __init__(
        self,
        executable: str,
        *,
        run: Callable[..., Any] | None = None,
        timeout: float = MESH_POST_TIMEOUT_S,
    ) -> None:
        self._executable = executable
        self._run = run or subprocess.run
        self._timeout = timeout

    def post(self, channel: str, text: str) -> None:
        argv = [self._executable, "channel", "message", channel, text]
        self._run(  # nosec B603 - fixed argv list, shell=False
            argv, check=True, capture_output=True, text=True, timeout=self._timeout
        )


def open_reporter(env: Mapping[str, str] | None = None) -> RunReporter | None:
    """A run reporter for ``CULTURE_RULES_REPORT_CHANNEL``, or None when it is unset."""
    channel = ((os.environ if env is None else env).get(REPORT_CHANNEL_ENV) or "").strip()
    if not channel:
        return None
    culture = shutil.which("culture")
    poster: Any = MeshPoster(culture) if culture else LoggingPoster()
    return RunReporter(poster, channel=channel)


class NoopAction:
    """The built-in ``noop`` action: completes at once, does nothing."""

    supports_idempotency_key = True

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        return InvocationResult.completed({})


def default_ports(store: StoragePort, host: str) -> dict[str, Any]:
    """Ports for work that names no stored actor (stored actors are wired by the router)."""
    del store, host
    return {"action:noop": NoopAction()}


def run_node(host: str | None = None, *, once: bool = False, idle: float = 1.0) -> dict[str, Any]:
    """Open everything, run the node (one cycle with ``once``) and summarise what it did."""
    from culture_rules.node.actors import default_factories  # noqa: PLC0415
    from culture_rules.node.daemon import Node  # noqa: PLC0415

    host = host or default_host()
    try:
        store = open_store()
    except StoreError as exc:
        raise NodeSetupError(f"cannot open the store: {exc}") from exc
    configure_logging(host=host)
    source = open_event_source(host)
    node = Node(
        store,
        host,
        actors=default_ports(store, host),
        adapters=default_factories(store, emitter=open_emitter(store, host)),
        event_source=source,
        reporter=open_reporter(),
    )
    if once:
        report = node.run_once()
        return {
            "host": host,
            "cycles": 1,
            "events": source is not None,
            "report": report.to_dict(),
        }
    import signal  # noqa: PLC0415

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: node.stop())
    cycles = node.run(idle=idle)
    return {
        "host": host,
        "cycles": cycles,
        "events": source is not None,
        "errors": [f"{type(e).__name__}: {e}" for e in node.errors[-20:]],
    }
