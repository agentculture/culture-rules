"""Completions of accepted work: record the result, then free the actor's limit slot.

An actor that answers ``accepted`` keeps its :class:`~culture_rules.actors.limits.LimitedActor`
slot (``actor_usage.inflight``) until its completion arrives. Every path that reports such
a completion - :meth:`culture_rules.node.daemon.Node.deliver`, the API's ask answer
(:func:`culture_rules.actors.human.answer_ask`) and the node's redelivery of answered asks
(:func:`culture_rules.actors.human.redeliver`) - goes through :func:`deliver`, so the slot
is freed however the completion arrives, not only at the step's deadline.

:func:`release_slot` is store-only: it finds the step's actor from the step claim and the
run's pinned workflow (:func:`actor_of`) and releases the key on that actor's
``actor_usage`` document, marking it done only for a *completed* result (a failed attempt's
retry must be admitted like new work). It is idempotent: a key already released and done
is left alone, so its tokens are counted once. Standard-library only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors.limits import USAGE_COLLECTION, ActorLimits, LimitedActor, tokens_of
from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationResult
from culture_rules.engine.claims import CLAIMS_COLLECTION
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.store.port import StoreOps

__all__ = ["actor_of", "deliver", "release_slot", "step_attempt"]

Clock = Callable[[], datetime]


def _all_steps(steps: Any) -> Iterator[Mapping[str, Any]]:
    for step in steps:
        yield step
        yield from _all_steps(step.get("body") or ())


def actor_of(store: StoreOps, key: str) -> str | None:
    """The actor (``placement.actor``) of the step whose idempotency key is ``key``."""
    claim = store.get(CLAIMS_COLLECTION, key)
    if not claim or claim.get("kind") != "step":
        return None
    run = store.get(RUNS_COLLECTION, claim["run_id"]) or {}
    definition = (run.get("workflow") or {}).get("definition") or {}
    step_id = claim["step_id"].rsplit("/", 1)[-1]
    for step in _all_steps(definition.get("steps") or ()):
        if step.get("id") == step_id:
            return (step.get("placement") or {}).get("actor")
    return None


def step_attempt(store: StoreOps, key: str) -> int | None:
    """The attempt the step whose idempotency key is ``key`` is on now (None: unknown)."""
    claim = store.get(CLAIMS_COLLECTION, key)
    if not claim or claim.get("kind") != "step":
        return None
    run = store.get(RUNS_COLLECTION, claim["run_id"]) or {}
    for step in run.get("steps") or ():
        if step.get("key") == claim["step_id"]:
            return step.get("attempt")
    return None


def release_slot(
    store: StoreOps, key: str, result: InvocationResult, *, clock: Clock | None = None
) -> bool:
    """Free ``key``'s slot on its step's actor; False when no limited actor holds usage."""
    actor = actor_of(store, key)
    if not actor or store.get(USAGE_COLLECTION, actor) is None:
        return False
    limited = LimitedActor(None, actor, ActorLimits(), store, clock=clock)  # type: ignore[arg-type]
    limited.release(key, tokens=tokens_of(result), completed=result.outcome == COMPLETED)
    return True


def deliver(
    store: StoreOps,
    executor: Any,
    key: str,
    result: InvocationResult,
    *,
    clock: Clock | None = None,
    attempt: int | None = None,
) -> bool:
    """``executor.deliver`` the result, then free the actor's slot; True iff the run changed.

    The slot is released when the delivery changed the run, and for a completed result
    even when it did not (the run already recorded it, e.g. before a crash), so a retried
    delivery still frees a slot left behind. A stale failure is never released: its key
    may already hold a retry's slot. With ``attempt`` (the attempt that produced the
    result), ``Executor.deliver`` refuses it once the step is on another attempt, and the
    slot - then held by that other attempt - is left alone.
    """
    if attempt is None:
        changed = bool(executor.deliver(key, result))
    else:
        changed = bool(executor.deliver(key, result, attempt=attempt))
    finished = result.outcome in (COMPLETED, FAILED)
    if finished and (changed or result.outcome == COMPLETED):
        if not changed and attempt is not None and step_attempt(store, key) != attempt:
            return changed  # refused: another attempt holds the key's slot
        release_slot(store, key, result, clock=clock or getattr(executor, "_clock", None))
    return changed
