"""A fake Discord Gateway (no network) shared by the gateway and node-stage tests.

:class:`FakeHub` is "Discord": it holds the message stream, tracks which listeners are
connected at once (``max_live`` must stay 1 for one bot) and, like a gateway resume or a
takeover replaying history, re-sends the last ``replay`` messages to every new connection.
:class:`FakeGateway` is one node's gateway client.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any


def eventually(pred: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Poll ``pred`` until true or ``timeout`` (real) seconds pass."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


def message(n: int, *, channel: str = "100", guild: str = "1", content: str = "hi") -> dict:
    return {
        "id": str(9000 + n),
        "guild_id": guild,
        "channel_id": channel,
        "author_id": "42",
        "author_name": "alice",
        "bot": False,
        "content": content,
        "created_at": "2026-10-04T10:00:00+00:00",
        "url": f"https://discord.com/channels/{guild}/{channel}/{9000 + n}",
    }


class FakeHub:
    def __init__(self, *, replay: int = 3) -> None:
        self._lock = threading.Lock()
        self.live: list[str] = []
        self.max_live = 0
        self.connects: list[tuple[str, str]] = []
        self.replay = replay
        self.history: list[dict] = []
        self.inbox: queue.Queue[tuple[str, Any]] = queue.Queue()

    def enter(self, name: str, token: str) -> list[dict]:
        with self._lock:
            self.live.append(name)
            self.max_live = max(self.max_live, len(self.live))
            self.connects.append((name, token))
            return list(self.history[-self.replay :]) if self.replay else []

    def leave(self, name: str) -> None:
        with self._lock:
            self.live.remove(name)

    def live_now(self) -> list[str]:
        with self._lock:
            return list(self.live)

    def post(self, msg: Mapping[str, Any]) -> None:
        with self._lock:
            self.history.append(dict(msg))
        self.inbox.put(("msg", dict(msg)))

    def drop(self) -> None:
        """The connection drops (the listener should reconnect and resume)."""
        self.inbox.put(("drop", None))


class FakeGateway:
    def __init__(self, hub: FakeHub, name: str, *, available: bool = True) -> None:
        self.hub = hub
        self.name = name
        self._available = available

    def available(self) -> bool:
        return self._available

    def connect(
        self,
        token: str,
        on_message: Callable[[Mapping[str, Any]], None],
        should_stop: Callable[[], bool],
    ) -> None:
        replayed = self.hub.enter(self.name, token)
        try:
            for msg in replayed:
                on_message(msg)
            while not should_stop():
                try:
                    kind, payload = self.hub.inbox.get(timeout=0.005)
                except queue.Empty:
                    continue
                if kind == "drop":
                    raise ConnectionResetError("gateway closed")
                on_message(payload)
        finally:
            self.hub.leave(self.name)
