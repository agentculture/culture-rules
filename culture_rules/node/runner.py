"""What ``culture-rules node run`` needs: open the store, the event source, the ports.

The node talks to the store directly - it *is* the engine, not an API client. This is the
only CLI verb that does; the CLI module imports this package lazily, inside the handler.
Every seam here is a module-level function so tests (and embedders) can replace it.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.events.source import EventFabricError, EventSource
from culture_rules.store.port import StoragePort, StoreError

__all__ = [
    "NodeSetupError",
    "NoopAction",
    "default_host",
    "default_ports",
    "open_event_source",
    "open_store",
    "run_node",
]

log = logging.getLogger("culture_rules.node")


class NodeSetupError(RuntimeError):
    """The node cannot start: the store is not configured or not reachable."""


def default_host() -> str:
    """This machine's name (the short hostname)."""
    return socket.gethostname().split(".")[0]


def open_store() -> StoragePort:
    """The MongoDB store configured by ``CULTURE_RULES_MONGO_*`` (needs the ``store`` extra).

    Raises :class:`~culture_rules.store.port.StoreError` (``ConfigError``) when unset.
    """
    from culture_rules.store.mongo import MongoConfig, MongoStore  # noqa: PLC0415 - lazy

    return MongoStore(MongoConfig.from_env())


def open_event_source(host: str) -> EventSource | None:
    """This host's durable events-cli subscription, or None when events-cli is missing."""
    from culture_rules.events.events_cli_adapter import EventsCliSource  # noqa: PLC0415

    try:
        source = EventsCliSource.for_host(host)
        source.ensure()
    except EventFabricError as exc:
        log.warning("no event source for %s: %s", host, exc)
        return None
    return source


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
    source = open_event_source(host)
    node = Node(
        store,
        host,
        actors=default_ports(store, host),
        adapters=default_factories(store),
        event_source=source,
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
