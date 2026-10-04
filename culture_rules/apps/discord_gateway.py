"""The Discord Gateway listener: one live connection per bot, held by one node mesh-wide.

A Discord ``app`` actor (``params.surface == "discord"``) that declares
``discord.message.created`` in ``params.events`` is listened to over the Discord Gateway.
Every engine node runs a :class:`GatewaySupervisor` from a stage of its cycle
(:meth:`~culture_rules.node.daemon.Node.run_once`, right after the probe stage); the
supervisors coordinate through a mesh-wide :class:`~culture_rules.engine.named_lease.NamedLease`
named ``discord-gateway:<actor id>``, so exactly one node holds the connection.

Threading
=========
:meth:`GatewaySupervisor.tick` runs on the node's cycle thread. Per actor it acquires or
renews the lease (every third of the TTL) and, while it is held and the actor is enabled,
keeps one **listener thread** running; the listener owns the gateway connection (for
discord.py: its own asyncio event loop) and reconnects with a capped exponential backoff
when the connection drops. A connection that resumes - or a new holder that reconnects -
may see recent messages again; :func:`~culture_rules.events.hook_sink.sink` dedupes them on
the message id (the delivery id), so each message lands once.

While a listener runs, a :class:`~culture_rules.engine.leasekeeper.LeaseKeeper` thread
renews the lease as well, so a long synchronous stage of the node cycle (an actor
invocation in the drive stage) cannot let it lapse.

No overlap
==========
The listener is *fenced*: it disconnects as soon as the local clock passes the lease expiry
minus a third of the TTL, or as soon as a renewal fails or is refused (lost to another
node). Another node can take the lease only once it has expired, so the old holder has
stopped before the new one starts (given NTP-synchronised clocks, skew well under a third
of the TTL). Disabling or deleting the actor stops the listener and releases the lease;
:meth:`GatewaySupervisor.shutdown` (the node stopping) does the same, so another node takes
over at once.

Secrets and logs
================
``connection.bot_token`` must be a ``grant:<NAME>`` reference; a literal is refused (the
actor is not listened to) and never logged. The token is resolved through
:func:`~culture_rules.actors.secrets.resolve` (injectable) in the listener thread, right
before each connect, and never from the environment. Logs carry the actor id, outcomes and
exception *types* only - never the token or message content.

The ``discord`` extra (discord.py) is imported lazily, inside the listener. Without it the
supervisor logs once and does nothing - in particular it takes no lease, so a node without
the extra never starves one that has it.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from culture_rules.actors import secrets
from culture_rules.engine.leasekeeper import JOIN_TIMEOUT_S, KeeperFactory, LeaseKeeper
from culture_rules.engine.named_lease import NamedLease
from culture_rules.events.hook_sink import sink

__all__ = [
    "ACTORS_COLLECTION",
    "GATEWAY_EVENT",
    "LEASE_PREFIX",
    "MAX_CONTENT",
    "DiscordPyGateway",
    "Gateway",
    "GatewayOptions",
    "GatewaySupervisor",
    "gateway_intents",
    "message_data",
]

log = logging.getLogger("culture_rules.apps.discord_gateway")

ACTORS_COLLECTION = "actors"
GATEWAY_EVENT = "discord.message.created"
LEASE_PREFIX = "discord-gateway:"
MAX_CONTENT = 2000
"""Longest ``data.content`` carried on an event (Discord's own message limit)."""
_SINK_ATTEMPTS = 3

OnMessage = Callable[[Mapping[str, Any]], None]


class Gateway(Protocol):
    """A Discord Gateway client (the real one is :class:`DiscordPyGateway`; tests fake it)."""

    def available(self) -> bool:
        """Whether this client can run here (e.g. its library is installed)."""

    def connect(self, token: str, on_message: OnMessage, should_stop: Callable[[], bool]) -> None:
        """Connect and call ``on_message`` with each created message (a mapping with ``id``,
        ``guild_id``, ``channel_id``, ``author_id``, ``author_name``, ``bot``, ``content``,
        ``created_at``, ``url``) until ``should_stop()`` is true - then disconnect and
        return. Raise (or return) when the connection is lost for good; the supervisor
        reconnects."""


def gateway_intents() -> Any:
    """discord.py intents: the defaults (guilds, guild messages) plus message content."""
    import discord  # noqa: PLC0415 - the optional discord extra, imported lazily

    intents = discord.Intents.default()
    intents.guilds = True
    intents.guild_messages = True
    intents.message_content = True  # privileged: enable it for the bot in the dev portal
    return intents


def _message_dict(msg: Any) -> dict[str, Any]:
    author = msg.author
    created = getattr(msg, "created_at", None)
    guild = getattr(msg, "guild", None)
    return {
        "id": str(msg.id),
        "guild_id": str(guild.id) if guild is not None else None,
        "channel_id": str(msg.channel.id),
        "author_id": str(author.id),
        "author_name": str(getattr(author, "name", "") or ""),
        "bot": bool(getattr(author, "bot", False)),
        "content": msg.content or "",
        "created_at": created.isoformat() if created is not None else None,
        "url": getattr(msg, "jump_url", None),
    }


class DiscordPyGateway:
    """The production :class:`Gateway`: a discord.py ``Client`` on its own event loop.

    discord.py reconnects and resumes the session by itself; :meth:`connect` returns once
    ``should_stop()`` turns true (a watchdog task closes the client) or the client gives up.
    """

    def __init__(self, *, poll: float = 0.5) -> None:
        self._poll = poll
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:  # locate it without paying for the import
            try:
                self._available = importlib.util.find_spec("discord") is not None
            except (ImportError, ValueError):
                self._available = False
        return self._available

    def connect(self, token: str, on_message: OnMessage, should_stop: Callable[[], bool]) -> None:
        import asyncio  # noqa: PLC0415

        import discord  # noqa: PLC0415 - the optional discord extra, imported lazily

        intents = gateway_intents()
        poll = self._poll

        async def main() -> None:
            client = discord.Client(intents=intents)

            async def on_message_event(msg: Any) -> None:
                await asyncio.to_thread(on_message, _message_dict(msg))

            client.event(_named(on_message_event, "on_message"))

            async def watchdog() -> None:
                while not should_stop():
                    await asyncio.sleep(poll)
                await client.close()

            async with client:
                guard = asyncio.create_task(watchdog())
                try:
                    await client.start(token, reconnect=True)
                finally:
                    guard.cancel()

        asyncio.run(main())


def _named(fn: Any, name: str) -> Any:
    fn.__name__ = name  # discord.py dispatches events by the coroutine's name
    return fn


def message_data(msg: Mapping[str, Any]) -> dict[str, Any]:
    """The compact event ``data`` for one gateway message (content truncated)."""

    def opt(key: str) -> str | None:
        value = msg.get(key)
        return None if value is None else str(value)

    return {
        "guild_id": opt("guild_id"),
        "channel_id": opt("channel_id"),
        "message_id": opt("id"),
        "author_id": opt("author_id"),
        "author_name": opt("author_name"),
        "bot": bool(msg.get("bot", False)),
        "content": str(msg.get("content") or "")[:MAX_CONTENT],
        "created_at": opt("created_at"),
        "url": opt("url"),
    }


@dataclass(frozen=True, kw_only=True)
class GatewayOptions:
    """Lease and reconnect tuning (tests shorten the backoff and drop the keeper thread)."""

    ttl: timedelta = timedelta(seconds=30)
    """The lease TTL; renewed every third of it, the listener is fenced at two thirds."""
    keeper: KeeperFactory | None = LeaseKeeper
    """Builds the renewal thread run beside a listener (``None``: renew from ticks only)."""
    backoff: float = 1.0
    """First reconnect pause, seconds (doubles per consecutive failure)."""
    max_backoff: float = 60.0
    """Longest reconnect pause, seconds."""


def _view(doc: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    params = doc.get("params") if isinstance(doc.get("params"), Mapping) else {}
    connection = doc.get("connection") or params.get("connection") or {}
    return params, connection if isinstance(connection, Mapping) else {}


class _Session:
    """One actor's lease, listener thread and keeper on this node."""

    def __init__(self, actor_id: str, lease: NamedLease, doc: Mapping[str, Any]) -> None:
        self.actor_id = actor_id
        self.lease = lease
        self.lock = threading.Lock()
        self.renew_lock = threading.Lock()
        self.doc: Mapping[str, Any] = doc
        self.valid_until: datetime | None = None
        self.renewed_at: datetime | None = None
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.keeper: Any = None

    def snapshot(self) -> Mapping[str, Any]:
        with self.lock:
            return self.doc

    def listening(self) -> bool:
        return self.thread is not None and self.thread.is_alive() and not self.stop.is_set()


class GatewaySupervisor:
    """Holds the Discord Gateway connections this node owns (see the module docstring)."""

    def __init__(
        self,
        store: Any,
        identity: str,
        *,
        gateway: Gateway | None = None,
        resolve_secret: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        options: GatewayOptions | None = None,
    ) -> None:
        self._store = store
        self.identity = identity
        self._gateway = gateway if gateway is not None else DiscordPyGateway()
        self._resolve = resolve_secret or secrets.resolve
        self._clock = clock or (lambda: datetime.now(UTC))
        self._options = options or GatewayOptions()
        self._sessions: dict[str, _Session] = {}
        self._stragglers: list[threading.Thread] = []
        self._warned: set[str] = set()
        self._unavailable_logged = False

    # ------------------------------------------------------------------ cycle

    def tick(self) -> list[str]:
        """Reconcile with the actors: renew leases, start/stop listeners; return the ids
        of the actors this node is listening for."""
        wanted = self._wanted()
        if wanted and not self._gateway.available():
            if not self._unavailable_logged:
                log.info("discord extra not installed: the discord gateway stage is idle")
                self._unavailable_logged = True
            wanted = {}  # take no lease: a node with the extra must be able to hold it
        for actor_id in [a for a in self._sessions if a not in wanted]:
            self._end(actor_id, release=True)
        failure: Exception | None = None
        for actor_id, doc in wanted.items():
            try:
                self._tick_one(actor_id, doc)
            except Exception as exc:  # noqa: BLE001 - other actors still get their turn
                failure = failure or exc
        if failure is not None:
            raise failure
        return self.listening()

    def listening(self) -> list[str]:
        return sorted(a for a, s in self._sessions.items() if s.listening())

    def shutdown(self) -> None:
        """Stop every listener and release every lease this node holds."""
        for actor_id in list(self._sessions):
            self._end(actor_id, release=True)

    def threads_alive(self) -> bool:
        """Whether any listener or keeper thread is still running (tests: no leaks)."""
        threads = [s.thread for s in self._sessions.values()] + self._stragglers
        keepers = [s.keeper for s in self._sessions.values()]
        return any(t is not None and t.is_alive() for t in threads) or any(
            k is not None and getattr(k, "alive", False) for k in keepers
        )

    # ------------------------------------------------------------------ reconcile

    def _wanted(self) -> dict[str, Mapping[str, Any]]:
        out: dict[str, Mapping[str, Any]] = {}
        for doc in self._store.find(ACTORS_COLLECTION, {"kind": "app"}):
            params, connection = _view(doc)
            actor_id = doc.get("id")
            if (doc.get("surface") or params.get("surface")) != "discord":
                continue
            if GATEWAY_EVENT not in (params.get("events") or ()):
                continue
            if doc.get("enabled", True) is False:
                continue
            if not secrets.is_secret_ref(connection.get("bot_token")):
                if actor_id not in self._warned:
                    self._warned.add(actor_id)
                    log.warning(
                        "discord gateway %s: connection.bot_token must be a grant:<NAME> "
                        "reference; not listening",
                        actor_id,
                    )
                continue
            self._warned.discard(actor_id)
            out[actor_id] = doc
        return out

    def _tick_one(self, actor_id: str, doc: Mapping[str, Any]) -> None:
        session = self._sessions.get(actor_id)
        if session is None:
            lease = NamedLease(
                self._store,
                LEASE_PREFIX + actor_id,
                self.identity,
                ttl=self._options.ttl,
                clock=self._clock,
            )
            session = self._sessions[actor_id] = _Session(actor_id, lease, doc)
        with session.lock:
            session.doc = doc
            due = session.renewed_at is None or self._clock() >= (
                session.renewed_at + self._options.ttl / 3
            )
        held = self._renew(session) if due else not self._fenced(session)
        if held and not session.listening():
            self._start(session)
        elif not held and session.thread is not None:
            self._stop_listener(session)

    def _renew(self, session: _Session) -> bool:
        """Acquire/renew the lease; on refusal or failure, fence the listener at once."""
        with session.renew_lock:
            try:
                expires = session.lease.acquire()
            except Exception as exc:  # noqa: BLE001 - a failed renewal means: disconnect now
                log.warning(
                    "discord gateway %s: lease renewal failed (%s); disconnecting",
                    session.actor_id,
                    type(exc).__name__,
                )
                expires = None
            with session.lock:
                session.valid_until = expires
                session.renewed_at = self._clock() if expires is not None else None
            if expires is None:
                session.stop.set()
            return expires is not None

    def _fenced(self, session: _Session) -> bool:
        with session.lock:
            until = session.valid_until
        return until is None or self._clock() >= until - self._options.ttl / 3

    def _start(self, session: _Session) -> None:
        self._stop_listener(session)
        stop = session.stop = threading.Event()
        session.thread = threading.Thread(
            target=self._listen,
            args=(session, stop),
            name=f"discord-gateway-{session.actor_id}",
            daemon=True,
        )
        session.thread.start()
        factory = self._options.keeper
        if factory is not None:
            keeper = factory(lambda: self._renew(session), self._options.ttl.total_seconds() / 3)
            keeper.__enter__()
            session.keeper = keeper
        log.info("discord gateway %s: listening (lease held)", session.actor_id)

    def _stop_listener(self, session: _Session) -> None:
        session.stop.set()
        keeper, session.keeper = session.keeper, None
        if keeper is not None:
            keeper.__exit__(None, None, None)
        thread, session.thread = session.thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(JOIN_TIMEOUT_S)
            if thread.is_alive():
                self._stragglers.append(thread)
                log.warning("discord gateway %s: listener did not stop in time", session.actor_id)

    def _end(self, actor_id: str, *, release: bool) -> None:
        session = self._sessions.pop(actor_id)
        was_listening = session.thread is not None
        self._stop_listener(session)
        if release:
            try:
                session.lease.release()
            except Exception as exc:  # noqa: BLE001 - it lapses on its own anyway
                log.warning(
                    "discord gateway %s: lease release failed (%s)", actor_id, type(exc).__name__
                )
        if was_listening:
            log.info("discord gateway %s: stopped listening", actor_id)

    # ------------------------------------------------------------------ listener thread

    def _listen(self, session: _Session, stop: threading.Event) -> None:
        def should_stop() -> bool:
            return stop.is_set() or self._fenced(session)

        failures = 0
        while not should_stop():
            _, connection = _view(session.snapshot())
            try:
                bot_auth = self._resolve(str(connection.get("bot_token")))
            except Exception as exc:  # noqa: BLE001 - logged by type only; retried
                failures += 1
                log.warning(
                    "discord gateway %s: cannot resolve the bot token (%s)",
                    session.actor_id,
                    type(exc).__name__,
                )
            else:
                try:
                    self._gateway.connect(
                        bot_auth, lambda msg: self._on_message(session, msg), should_stop
                    )
                    failures = 0
                except Exception as exc:  # noqa: BLE001 - reconnect; the type only is logged
                    failures += 1
                    log.warning(
                        "discord gateway %s: connection lost (%s); reconnecting",
                        session.actor_id,
                        type(exc).__name__,
                    )
                finally:
                    del bot_auth
            if should_stop():
                break
            stop.wait(self._backoff(failures))

    def _backoff(self, failures: int) -> float:
        opts = self._options
        return min(opts.max_backoff, opts.backoff * (2 ** max(0, failures - 1)))

    def _on_message(self, session: _Session, msg: Mapping[str, Any]) -> None:
        doc = session.snapshot()
        _, connection = _view(doc)
        data = message_data(msg)
        if not data["message_id"]:
            return
        channels = connection.get("channels")
        if channels and data["channel_id"] not in {str(c) for c in channels}:
            return
        guild = connection.get("guild_id")
        if guild and data["guild_id"] is not None and data["guild_id"] != str(guild):
            return
        author = data["author_name"] or data["author_id"]
        for attempt in range(1, _SINK_ATTEMPTS + 1):
            try:
                sink(
                    self._store,
                    doc,
                    GATEWAY_EVENT,
                    data,
                    delivery_id=data["message_id"],
                    author=author,
                )
                return
            except Exception as exc:  # noqa: BLE001 - retried, then dropped (type logged)
                log.warning(
                    "discord gateway %s: writing a message failed (%s), attempt %d",
                    session.actor_id,
                    type(exc).__name__,
                    attempt,
                )
                time.sleep(0.05 * attempt)
