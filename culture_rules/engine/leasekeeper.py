"""Keep a claim's lease alive while its holder is busy (standard-library only).

The run executor invokes an actor synchronously, and an adapter may block for minutes
(a colleague agent run, a code runner). The step's claim lease is much shorter
(:data:`~culture_rules.engine.claims.DEFAULT_LEASE`), so without renewal another host
would see it lapse and take the step over while it is still running, doing the work
twice. A :class:`LeaseKeeper` is a context manager around the blocking call: a daemon
thread calls ``renew`` every ``interval`` seconds (the executor uses a third of the
lease) until the block exits - normally or by an exception - and then stops and is
joined, so no thread outlives the call.

``renew`` returns whether the lease is still held. False (the claim was lost, or the
holder chose to let it lapse, e.g. past the step's deadline) ends the renewals; an
exception from ``renew`` (a transient store error) is logged and the next interval
tries again. Tests can drive renewals deterministically with :meth:`LeaseKeeper.renew_now`
or replace the keeper through the executor's ``lease_keeper`` factory.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any

__all__ = ["JOIN_TIMEOUT_S", "KeeperFactory", "LeaseKeeper"]

log = logging.getLogger(__name__)

JOIN_TIMEOUT_S = 10.0
"""How long stopping waits for a renewal in flight (a slow store call) to return."""

Renew = Callable[[], bool]
KeeperFactory = Callable[[Renew, float], AbstractContextManager[Any]]
"""Builds the keeper for one blocking call from ``(renew, interval_seconds)``."""


class LeaseKeeper(AbstractContextManager["LeaseKeeper"]):
    """Calls ``renew`` every ``interval`` seconds while the ``with`` block runs."""

    def __init__(self, renew: Renew, interval: float, *, name: str = "lease-keeper") -> None:
        if interval <= 0 or math.isnan(interval):  # NaN is never > 0, so it is refused too
            raise ValueError("interval must be a positive number of seconds")
        self._renew = renew
        self.interval = float(interval)
        self.name = name
        self.renewals = 0
        self.lost = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> LeaseKeeper:
        """Start the renewal thread (idempotent)."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        """Stop renewing and join the thread."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(JOIN_TIMEOUT_S)

    @property
    def alive(self) -> bool:
        """Whether the renewal thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def __enter__(self) -> LeaseKeeper:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    # ------------------------------------------------------------------ renewing

    def renew_now(self) -> bool:
        """Renew once, now; return whether the lease is still held."""
        with self._lock:
            if self.lost or self._stop.is_set():
                return not self.lost
            try:
                held = bool(self._renew())
            except Exception as exc:  # noqa: BLE001 - transient: the next interval retries
                log.warning("%s: lease renewal failed: %s", self.name, exc)
                return True
            if held:
                self.renewals += 1
            else:
                self.lost = True
            return held

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            if not self.renew_now():
                return
