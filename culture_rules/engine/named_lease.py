"""A mesh-wide named lease: at most one holder at a time, taken over only after it lapses.

Run and step claims (:mod:`culture_rules.engine.claims`) lease a *piece of work*. Some
duties are instead held continuously by exactly one engine node - a long-lived connection
such as the Discord Gateway listener (:mod:`culture_rules.apps.discord_gateway`). A
:class:`NamedLease` is one document in :data:`LEASES_COLLECTION` whose id is the lease name
(e.g. ``discord-gateway:<actor id>``)::

    {"id": name, "holder": identity | None, "epoch": int,
     "expires_at": ISO-8601 | None, "acquired_at": ISO-8601 | None}

:meth:`NamedLease.acquire` acquires a free lease, renews one this identity already holds,
or takes over one whose ``expires_at`` has passed; every state change is a compare-and-set
(``update_if`` on ``holder`` and ``epoch``) inside a retried transaction
(:func:`~culture_rules.store.retry.run_transaction`), so two racing nodes never both win.
``epoch`` increments on every acquisition by a new holder (a fencing token); a renewal keeps
it. :meth:`NamedLease.release` frees the lease at once (only its holder can).

Expiry is judged by the acquiring node's clock: nodes keep NTP-synchronised clocks and the
lease TTL must be long relative to their skew. A holder that wants to guarantee no overlap
must stop its duty *before* its lease can lapse (see the Discord supervisor's fence).
Standard-library only.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from culture_rules.store.port import DuplicateKeyError, StoreOps
from culture_rules.store.retry import run_transaction

__all__ = ["LEASES_COLLECTION", "NamedLease"]

LEASES_COLLECTION = "leases"
"""Collection holding one document per named lease."""


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class NamedLease:
    """One identity's handle on the lease called ``name`` (see the module docstring)."""

    def __init__(
        self,
        store: object,
        name: str,
        holder: str,
        *,
        ttl: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(holder, str) or not holder:
            raise ValueError("holder must be a non-empty string")
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        self._store = store
        self.name = name
        self.holder = holder
        self.ttl = ttl
        self._clock = clock or (lambda: datetime.now(UTC))

    def acquire(self) -> datetime | None:
        """Acquire, renew or take over the lease; return its new expiry, or None if refused."""
        now = self._clock()
        expires = now + self.ttl

        def body(tx: StoreOps) -> datetime | None:
            doc = tx.get(LEASES_COLLECTION, self.name)
            stamp = {"expires_at": expires.isoformat()}
            if doc is None:
                changes = {
                    **stamp,
                    "holder": self.holder,
                    "epoch": 1,
                    "acquired_at": now.isoformat(),
                }
                res = tx.update_if(
                    LEASES_COLLECTION, self.name, {"holder": None}, changes, upsert=True
                )
                return expires if res.won else None
            current, epoch = doc.get("holder"), doc.get("epoch")
            expected = {"holder": current, "epoch": epoch}
            if current == self.holder:
                res = tx.update_if(LEASES_COLLECTION, self.name, expected, stamp)
                return expires if res.won else None
            lapsed = _parse(doc.get("expires_at"))
            if current is not None and lapsed is not None and lapsed > now:
                return None  # held by someone else, still live
            next_epoch = (epoch if isinstance(epoch, int) else 0) + 1
            changes = {
                **stamp,
                "holder": self.holder,
                "epoch": next_epoch,
                "acquired_at": now.isoformat(),
            }
            res = tx.update_if(LEASES_COLLECTION, self.name, expected, changes)
            return expires if res.won else None

        try:
            return run_transaction(self._store, body)
        except DuplicateKeyError:  # a racing first acquirer created it
            return None

    def release(self) -> bool:
        """Free the lease if this identity holds it; return whether it did."""

        def body(tx: StoreOps) -> bool:
            doc = tx.get(LEASES_COLLECTION, self.name)
            if doc is None or doc.get("holder") != self.holder:
                return False
            res = tx.update_if(
                LEASES_COLLECTION,
                self.name,
                {"holder": self.holder, "epoch": doc.get("epoch")},
                {"holder": None, "expires_at": None},
            )
            return res.won

        return run_transaction(self._store, body)
