"""Bounded retry of a whole transaction body on :class:`TransientStoreError`.

A context-manager transaction cannot re-run its own block, so code that wants the retry
hands the body over as a function: ``run_transaction(store, fn)`` opens a transaction,
calls ``fn(tx)`` and commits; when the adapter reports a transient failure (MongoDB's
``TransientTransactionError`` - a write conflict with a concurrent transaction), the
transaction has been rolled back and the body is run again in a fresh one, up to
``attempts`` times. Every other error propagates at once. ``fn`` must therefore only have
effects through ``tx`` (or be safe to repeat). Standard-library only.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import Any, TypeVar

from culture_rules.store.port import StoreOps, TransientStoreError

#: OS-entropy source for the backoff jitter (not security-relevant, but never a seeded PRNG).
_JITTER = secrets.SystemRandom()

__all__ = ["DEFAULT_ATTEMPTS", "run_transaction"]

DEFAULT_ATTEMPTS = 5
DEFAULT_BACKOFF_S = 0.02

T = TypeVar("T")


def run_transaction(
    store: Any,
    fn: Callable[[StoreOps], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF_S,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``fn(tx)`` in a transaction, re-running it on a transient failure (bounded)."""
    if not isinstance(attempts, int) or attempts < 1:
        raise ValueError("attempts must be a positive int")
    for attempt in range(1, attempts + 1):
        try:
            with store.transaction() as tx:
                return fn(tx)
        except TransientStoreError:
            if attempt == attempts:
                raise
            if backoff > 0:  # jittered, growing pause so racing writers spread out
                sleep(backoff * attempt * (1 + _JITTER.random()))
    raise AssertionError("unreachable")  # pragma: no cover
