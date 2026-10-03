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
(call it from wherever ``Executor.deliver`` is called, passing the tokens used).

Every attempt of a step shares one idempotency key. Only a *completed* key is remembered
as done: a re-ask of it (a lost acknowledgement) replays without a new slot or a budget
check, and its tokens are not counted twice. A failed attempt frees its slot and adds the
tokens it used, but is not remembered, so its retry is admitted like new work - it waits
at the concurrency cap, is refused over budget, and its tokens are counted.

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
    "OVER_BUDGET",
    "USAGE_COLLECTION",
    "ActorLimits",
    "LimitedActor",
    "limits_from_config",
    "parse_limit_error",
]

USAGE_COLLECTION = "actor_usage"
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


def limits_from_config(config: ActorConfig) -> ActorLimits:
    """Read limits from an actor's culture.yaml entry, degrading invalid values."""
    extras: Mapping[str, Any] = config.extras
    budget = extras.get("token_budget")
    if not (_is_int(budget) and budget > 0):
        budget = None
    pct = extras.get("token_budget_warn_pct", DEFAULT_WARN_PCT)
    if not (_is_int(pct) and 1 <= pct <= 100):
        pct = DEFAULT_WARN_PCT
    cap = extras.get("max_concurrency")
    if not (_is_int(cap) and cap > 0):
        cap = None
    return ActorLimits(token_budget=budget, token_budget_warn_pct=pct, max_concurrency=cap)


def parse_limit_error(error: str | None) -> dict[str, Any]:
    """Decode the structured error a :class:`LimitedActor` puts on a refusal."""
    data = json.loads(error or "")
    if not isinstance(data, dict) or "code" not in data:
        raise ValueError("not a structured limit error")
    return data


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _tokens_of(result: InvocationResult) -> int:
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

    def _mutate(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        """CAS loop: ``fn(fresh_doc)`` edits it and returns a result, or None to abort."""
        for _ in range(_MAX_CAS_TRIES):
            current = self.store.get(USAGE_COLLECTION, self.actor)
            rev = current.get("rev") if current else None
            doc = self._fresh(current)
            outcome = fn(doc)
            if doc.get("_abort"):
                return outcome
            changes = {
                "day": doc["day"],
                "tokens": doc["tokens"],
                "inflight": doc["inflight"],
                "done": doc["done"][-_DONE_KEEP:],
                "actor": self.actor,
                "rev": (rev or 0) + 1,
            }
            res = self.store.update_if(
                USAGE_COLLECTION, self.actor, {"rev": rev}, changes, upsert=True
            )
            if res.won:
                return outcome
        raise RuntimeError(f"actor_usage/{self.actor}: too much contention")

    def usage(self) -> dict[str, Any]:
        """Today's usage: tokens, in_flight, limits and the warn/over flags."""
        doc = self._fresh(self.store.get(USAGE_COLLECTION, self.actor))
        budget = self.limits.token_budget
        tokens = doc["tokens"]
        return {
            "actor": self.actor,
            "day": doc["day"],
            "tokens": tokens,
            "in_flight": len(doc["inflight"]),
            "token_budget": budget,
            "max_concurrency": self.limits.max_concurrency,
            "budget_warning": bool(
                budget and tokens * 100 >= budget * self.limits.token_budget_warn_pct
            ),
            "over_budget": bool(budget and tokens >= budget),
        }

    # ------------------------------------------------------------ admission

    def _admit(self, key: str, deadline: datetime) -> tuple[str, dict[str, Any]]:
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

    def release(self, key: str, *, tokens: int = 0, completed: bool = False) -> None:
        """Free ``key``'s slot and add ``tokens`` used (for work that finished later).

        ``completed=True`` also remembers the key as done, so a re-ask replays without a
        slot or a budget check. Leave it False for a failure: the retry (same key) must be
        admitted, capped and counted like new work.
        """

        def fn(doc: dict[str, Any]) -> None:
            doc["inflight"] = [s for s in doc["inflight"] if s["key"] != key]
            doc["tokens"] += max(0, tokens)
            if completed and key not in doc["done"]:
                doc["done"].append(key)

        self._mutate(fn)

    def _drop_slot(self, key: str) -> None:
        def fn(doc: dict[str, Any]) -> None:
            doc["inflight"] = [s for s in doc["inflight"] if s["key"] != key]

        self._mutate(fn)

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
                tokens=_tokens_of(result),
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
