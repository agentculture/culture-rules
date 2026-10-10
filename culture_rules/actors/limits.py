"""Per-actor token budgets and concurrency caps, as an ActorPort wrapper.

:class:`LimitedActor` wraps any :class:`~culture_rules.engine.actorport.ActorPort`:

* at the **concurrency cap** it returns ``blocked`` - the executor asks again later
  without consuming an attempt, so "blocked" *is* the queue;
* **over budget** it returns a non-retryable ``failed`` whose ``error`` is a JSON object
  ``{"code": "over_budget", "actor", "token_budget", "tokens_used", "day", "message"}``
  (see :func:`parse_limit_error`).

Field names align with culture's ``culture.yaml`` agent entry
(``culture_core/config.py``): ``token_budget`` (positive int, tokens per UTC day) and
``token_budget_warn_pct`` (int 1..100, default 80). Invalid values degrade to unset /
default exactly as culture does. Culture only warns; here the budget also refuses, which
is the rules engine's choice. ``max_concurrency`` is this engine's own field. All three
are read from an actor's ``culture.yaml`` entry (they land in ``ActorConfig.extras``).

Counters live in the store collection ``actor_usage`` (one document per actor) and are
changed only with ``update_if`` compare-and-set on a ``rev`` counter, so caps hold across
hosts and threads. In-flight slots are keyed by idempotency key (a re-ask of the same key
never takes a second slot) and carry the attempt's deadline, so a slot whose completion
never arrives is reaped. Slots of ``accepted`` work are freed by :meth:`LimitedActor.release`
(every deliver path goes through :func:`culture_rules.node.completions.deliver`, which
calls it with the tokens used).

Every attempt of a step shares one idempotency key. Only a *completed* key is remembered
as done: a re-ask of it (a lost acknowledgement) replays without a new slot or a budget
check, and its tokens are not counted twice. A failed attempt frees its slot and adds the
tokens it used, but is not remembered, so its retry is admitted like new work - it waits
at the concurrency cap, is refused over budget, and its tokens are counted.

Concurrency pools (#35, d28/d29): an actor whose ``concurrency_pool`` names a pool (a
non-empty string) keeps its in-flight slots in the pool's shared document,
``actor_usage`` id ``pool:<name>`` (:func:`pool_doc_id`), so every actor naming that pool
counts against one slot count across hosts - one model server, one queue. The pool's cap is
the smallest ``max_concurrency`` among the pool's enabled actors (:func:`pool_cap`,
resolved by :class:`~culture_rules.node.actors.ActorRouter` per invocation): no extra
setting, and the strictest member wins, so adding a member can only tighten the server's
cap. A pool whose members declare no cap shares a count but is unlimited. Token budgets and
the completed-key memory stay on the actor's own document. An actor without a pool behaves
exactly as before (one document holds its slots, tokens and done keys).

Standard library only.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from culture_rules.actors.config import ActorConfig
from culture_rules.engine.actorport import (
    ACCEPTED,
    COMPLETED,
    ActorPort,
    InvocationContext,
    InvocationResult,
)

__all__ = [
    "DEFAULT_WARN_PCT",
    "POOL_PREFIX",
    "OVER_BUDGET",
    "USAGE_COLLECTION",
    "ActorLimits",
    "LimitedActor",
    "limits_from_config",
    "parse_limit_error",
    "pool_cap",
    "pool_doc_id",
    "pool_holding",
    "pool_of",
    "tokens_of",
]

USAGE_COLLECTION = "actor_usage"
POOL_PREFIX = "pool:"
"""Id prefix of a concurrency pool's shared slot document in :data:`USAGE_COLLECTION`."""
OVER_BUDGET = "over_budget"
DEFAULT_WARN_PCT = 80  # culture AgentConfig.token_budget_warn_pct default
_DONE_KEEP = 256  # completed keys remembered per actor (lost-ack replays)
_MAX_CAS_TRIES = 50


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class ActorLimits:
    """Limits for one actor; ``None`` means unlimited."""

    token_budget: int | None = None
    token_budget_warn_pct: int = DEFAULT_WARN_PCT
    max_concurrency: int | None = None
    concurrency_pool: str | None = None


def pool_doc_id(pool: str) -> str:
    """The ``actor_usage`` id of pool ``pool``'s shared slot document."""
    return f"{POOL_PREFIX}{pool}"


def _pool_name(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _cap(value: Any) -> int | None:
    return value if _is_int(value) and value > 0 else None


def pool_of(actor_doc: Mapping[str, Any] | None) -> str | None:
    """The ``concurrency_pool`` a stored actor document names (None: no pool)."""
    params = (actor_doc or {}).get("params") or {}
    return _pool_name(params.get("concurrency_pool")) if isinstance(params, Mapping) else None


def pool_cap(store: Any, pool: str, collection: str = "actors") -> int | None:
    """The cap of ``pool``: the smallest ``max_concurrency`` among the enabled, not deleted
    actors naming it; None when none of them declares one (shared count, no cap)."""
    caps = [
        cap
        for doc in store.find(collection)
        if doc.get("enabled", True) is not False
        and not doc.get("deleted_at")
        and pool_of(doc) == pool
        and (cap := _cap((doc.get("params") or {}).get("max_concurrency"))) is not None
    ]
    return min(caps) if caps else None


def pool_holding(store: Any, key: str, likely: str | None = None) -> str | None:
    """The pool whose slot document holds ``key`` (#35, Codex P2): ``likely`` first (the
    actor's pool now), else any pool. A slot is released where it was taken, so an actor
    moved to another pool, or out of one, while its work ran never leaks its slot."""
    if likely:
        doc = store.get(USAGE_COLLECTION, pool_doc_id(likely))
        if doc and any(s.get("key") == key for s in doc.get("inflight") or ()):
            return likely
    for doc in store.find(USAGE_COLLECTION):
        pool = doc.get("pool")
        if (
            isinstance(pool, str)
            and doc.get("id") == pool_doc_id(pool)
            and any(s.get("key") == key for s in doc.get("inflight") or ())
        ):
            return pool
    return None


def limits_from_config(config: ActorConfig) -> ActorLimits:
    """Read limits from an actor's culture.yaml entry, degrading invalid values.

    ``max_concurrency`` here is the actor's own; for an actor in a pool the router replaces
    it with :func:`pool_cap`."""
    extras: Mapping[str, Any] = config.extras
    budget = extras.get("token_budget")
    if not (_is_int(budget) and budget > 0):
        budget = None
    pct = extras.get("token_budget_warn_pct", DEFAULT_WARN_PCT)
    if not (_is_int(pct) and 1 <= pct <= 100):
        pct = DEFAULT_WARN_PCT
    return ActorLimits(
        token_budget=budget,
        token_budget_warn_pct=pct,
        max_concurrency=_cap(extras.get("max_concurrency")),
        concurrency_pool=_pool_name(extras.get("concurrency_pool")),
    )


def parse_limit_error(error: str | None) -> dict[str, Any]:
    """Decode the structured error a :class:`LimitedActor` puts on a refusal."""
    data = json.loads(error or "")
    if not isinstance(data, dict) or "code" not in data:
        raise ValueError("not a structured limit error")
    return data


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def tokens_of(result: InvocationResult) -> int:
    """Tokens a result reports (``output.tokens`` or ``output.usage.total_tokens``), else 0."""
    out = result.output
    for value in (out.get("tokens"), (out.get("usage") or {}).get("total_tokens")):
        if _is_int(value) and value > 0:
            return value
    return 0


class LimitedActor:
    """An :class:`ActorPort` enforcing :class:`ActorLimits` over a shared store."""

    def __init__(
        self,
        inner: ActorPort,
        actor: str,
        limits: ActorLimits,
        store: Any,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.inner = inner
        self.actor = actor
        self.limits = limits
        self.store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self.supports_idempotency_key = getattr(inner, "supports_idempotency_key", True)

    # ------------------------------------------------------------- counters

    def _day(self) -> str:
        return self._clock().astimezone(UTC).strftime("%Y-%m-%d")

    def _fresh(self, doc: Mapping[str, Any] | None) -> dict[str, Any]:
        """The usage doc normalised to today, with expired slots reaped."""
        now = _iso(self._clock())
        day = self._day()
        base = dict(doc or {})
        if base.get("day") != day:
            base["day"], base["tokens"] = day, 0
        base["inflight"] = [s for s in base.get("inflight", []) if s["until"] > now]
        base.setdefault("tokens", 0)
        base.setdefault("done", [])
        return base

    @property
    def _pool(self) -> str | None:
        return self.limits.concurrency_pool

    def _slots_id(self) -> str:
        """The document holding this actor's in-flight slots: its pool's, else its own."""
        return pool_doc_id(self._pool) if self._pool else self.actor

    def _mutate(
        self, fn: Callable[[dict[str, Any]], Any], *, slots: bool = False, pool: str | None = None
    ) -> Any:
        """CAS loop: ``fn(fresh_doc)`` edits it and returns a result; it sets ``_abort`` on
        the doc to write nothing. ``slots=True`` edits the pool's slot document (``pool``:
        that pool's, whatever the actor's pool is now)."""
        pool = pool or (self._pool if slots else None)
        slots = pool is not None
        doc_id = pool_doc_id(pool) if pool else self.actor
        for _ in range(_MAX_CAS_TRIES):
            current = self.store.get(USAGE_COLLECTION, doc_id)
            rev = current.get("rev") if current else None
            doc = self._fresh(current)
            outcome = fn(doc)
            if doc.get("_abort"):
                return outcome
            if slots:
                changes = {"pool": pool, "inflight": doc["inflight"]}
            else:
                changes = {
                    "day": doc["day"],
                    "tokens": doc["tokens"],
                    "inflight": doc["inflight"],
                    "done": doc["done"][-_DONE_KEEP:],
                    "actor": self.actor,
                }
            changes["rev"] = (rev or 0) + 1
            res = self.store.update_if(USAGE_COLLECTION, doc_id, {"rev": rev}, changes, upsert=True)
            if res.won:
                return outcome
        raise RuntimeError(f"actor_usage/{doc_id}: too much contention")

    def usage(self) -> dict[str, Any]:
        """Today's usage: tokens, in_flight, limits and the warn/over flags."""
        doc = self._fresh(self.store.get(USAGE_COLLECTION, self.actor))
        slots = self._fresh(self.store.get(USAGE_COLLECTION, self._slots_id()))
        budget = self.limits.token_budget
        tokens = doc["tokens"]
        return {
            "actor": self.actor,
            "day": doc["day"],
            "tokens": tokens,
            "in_flight": len(slots["inflight"]),
            "token_budget": budget,
            "max_concurrency": self.limits.max_concurrency,
            "concurrency_pool": self._pool,
            "budget_warning": bool(
                budget and tokens * 100 >= budget * self.limits.token_budget_warn_pct
            ),
            "over_budget": bool(budget and tokens >= budget),
        }

    # ------------------------------------------------------------ admission

    def _admit(self, key: str, deadline: datetime) -> tuple[str, dict[str, Any]]:
        if self._pool:
            return self._admit_pooled(key, deadline)

        def fn(doc: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            held = {s["key"] for s in doc["inflight"]}
            if key in doc["done"] or key in held:
                doc["_abort"] = True  # replay of known work: no new slot, no budget check
                return ("replay_done" if key in doc["done"] else "replay_held"), doc
            budget = self.limits.token_budget
            if budget is not None and doc["tokens"] >= budget:
                doc["_abort"] = True
                return OVER_BUDGET, doc
            cap = self.limits.max_concurrency
            if cap is not None and len(doc["inflight"]) >= cap:
                doc["_abort"] = True
                return "blocked", doc
            doc["inflight"].append({"key": key, "until": _iso(deadline)})
            return "admit", doc

        return self._mutate(fn)

    def _admit_pooled(self, key: str, deadline: datetime) -> tuple[str, dict[str, Any]]:
        """Admission in a pool: done keys and the budget on the actor's own document, the
        slot taken by compare-and-set on the pool's document."""
        own = self._fresh(self.store.get(USAGE_COLLECTION, self.actor))
        if key in own["done"]:
            return "replay_done", own
        pool = self._fresh(self.store.get(USAGE_COLLECTION, self._slots_id()))
        if any(s["key"] == key for s in pool["inflight"]):
            return "replay_held", own
        budget = self.limits.token_budget
        if budget is not None and own["tokens"] >= budget:
            return OVER_BUDGET, own

        def take(doc: dict[str, Any]) -> str:
            if any(s["key"] == key for s in doc["inflight"]):
                doc["_abort"] = True
                return "replay_held"
            cap = self.limits.max_concurrency
            if cap is not None and len(doc["inflight"]) >= cap:
                doc["_abort"] = True
                return "blocked"
            doc["inflight"].append({"key": key, "until": _iso(deadline), "actor": self.actor})
            return "admit"

        return self._mutate(take, slots=True), own

    def release(self, key: str, *, tokens: int = 0, completed: bool = False) -> None:
        """Free ``key``'s slot and add ``tokens`` used (for work that finished later).

        ``completed=True`` also remembers the key as done, so a re-ask replays without a
        slot or a budget check. Leave it False for a failure: the retry (same key) must be
        admitted, capped and counted like new work. Releasing a key that holds no slot and
        is already done is a no-op (a repeated delivery counts its tokens once).
        """

        holder = pool_holding(self.store, key, self._pool)
        pooled_held = self._drop_slot(key, holder) if holder else False

        def fn(doc: dict[str, Any]) -> None:
            held = pooled_held or any(s["key"] == key for s in doc["inflight"])
            if not held and key in doc["done"]:
                doc["_abort"] = True
                return
            doc["inflight"] = [s for s in doc["inflight"] if s["key"] != key]
            doc["tokens"] += max(0, tokens)
            if completed and key not in doc["done"]:
                doc["done"].append(key)

        self._mutate(fn)

    def _drop_slot(self, key: str, pool: str | None = None) -> bool:
        """Free ``key``'s slot (in ``pool``'s document, else the actor's pool's for a pooled
        actor); True if held."""

        def fn(doc: dict[str, Any]) -> bool:
            if not any(s["key"] == key for s in doc["inflight"]):
                doc["_abort"] = True
                return False
            doc["inflight"] = [s for s in doc["inflight"] if s["key"] != key]
            return True

        return bool(self._mutate(fn, slots=bool(pool or self._pool), pool=pool))

    # ----------------------------------------------------------- the port

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        verdict, doc = self._admit(idempotency_key, deadline)
        if verdict == OVER_BUDGET:
            return InvocationResult.failed(self._over_budget_error(doc), retryable=False)
        if verdict == "blocked":
            cap = self.limits.max_concurrency
            return InvocationResult.blocked(f"actor {self.actor} at concurrency cap {cap}")
        try:
            result = self.inner.invoke(input, idempotency_key, deadline, context=context)
        except BaseException:
            if verdict == "admit":
                self._drop_slot(idempotency_key)
            raise
        if result.outcome == ACCEPTED:
            return result  # slot stays held until release()
        if result.outcome == "blocked":
            if verdict == "admit":
                self._drop_slot(idempotency_key)
            return result
        if verdict != "replay_done":  # a done key's tokens were counted the first time
            self.release(
                idempotency_key,
                tokens=tokens_of(result),
                completed=result.outcome == COMPLETED,
            )
        return result

    def _over_budget_error(self, doc: Mapping[str, Any]) -> str:
        used, budget = doc["tokens"], self.limits.token_budget
        return json.dumps(
            {
                "code": OVER_BUDGET,
                "actor": self.actor,
                "token_budget": budget,
                "tokens_used": used,
                "day": doc["day"],
                "message": f"actor {self.actor} used {used} of {budget} tokens on {doc['day']}",
            },
            sort_keys=True,
        )
