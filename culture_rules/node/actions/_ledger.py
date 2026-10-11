"""A bounded, in-process replay ledger of completed action results (PR #54 review).

``llm.complete`` and ``library.push`` replay a completed result for the same idempotency key
instead of repeating the work. The ledger lives as long as the node daemon, so it is capped:
an :class:`collections.OrderedDict` kept in least-recently-used order, which drops its oldest
entry once it holds ``size`` of them. A dropped key is simply asked again (both ports are
safe to repeat; see their module docs). Standard-library only.
"""

from __future__ import annotations

from collections import OrderedDict

from culture_rules.engine.actorport import InvocationResult

__all__ = ["DEFAULT_LEDGER_SIZE", "ReplayLedger"]

DEFAULT_LEDGER_SIZE = 1024
"""How many completed results a port keeps for replay."""


class ReplayLedger:
    """Completed results by idempotency key, at most ``size`` of them (LRU order)."""

    def __init__(self, size: int = DEFAULT_LEDGER_SIZE) -> None:
        if size < 1:
            raise ValueError("size must be at least 1")
        self._size = size
        self._items: OrderedDict[str, InvocationResult] = OrderedDict()

    def get(self, key: str) -> InvocationResult | None:
        """The result recorded for ``key`` (marking it recent), else None."""
        result = self._items.get(key)
        if result is not None:
            self._items.move_to_end(key)
        return result

    def put(self, key: str, result: InvocationResult) -> None:
        """Record ``result`` for ``key``, dropping the least recently used beyond the cap."""
        self._items[key] = result
        self._items.move_to_end(key)
        while len(self._items) > self._size:
            self._items.popitem(last=False)

    def __contains__(self, key: object) -> bool:
        return key in self._items

    def __len__(self) -> int:
        return len(self._items)
