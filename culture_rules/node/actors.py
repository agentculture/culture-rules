"""Actor adapters for a node, wired from the stored Actor definitions.

:class:`ActorRouter` is the executor's ``ports`` callable: given an
:class:`~culture_rules.engine.actorport.InvocationContext` it returns the ActorPort to use.

* When the step names an actor (``placement.actor``) whose stored definition (collection
  ``actors``) is enabled and has a factory for its ``kind``, the router builds that
  adapter once (cached per actor id and definition revision) and wraps it in
  :class:`~culture_rules.actors.limits.LimitedActor`, with limits read by
  :func:`~culture_rules.actors.limits.limits_from_config` from the actor's ``params``
  (``token_budget``, ``token_budget_warn_pct``, ``max_concurrency`` - the same field names
  as a culture.yaml agent entry).
* Otherwise the injected ``ports`` are used the way the executor routes a mapping:
  ``action:<kind>``, ``action``, then the step kind, then ``"*"``.

:func:`default_factories` gives the production adapters: ``agent`` -> one-shot
``colleague work`` (:class:`~culture_rules.actors.agent.ColleagueActor`), ``runner`` ->
registered commands only (:class:`~culture_rules.actors.code.CodeRunner`, inline scripts
refused), ``human`` -> asks (:class:`~culture_rules.actors.human.HumanAdapter`, only when
an event emitter is configured). :meth:`ActorRouter.release` frees a LimitedActor slot for
accepted work that finished later, marking the key done only when it completed; the
deliver paths use the store-only :func:`culture_rules.node.completions.release_slot`.
``MeshAgentActor`` (mesh replies, polled) is not among the production factories.
Standard-library only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.actors.config import ActorConfig
from culture_rules.actors.limits import LimitedActor, limits_from_config, tokens_of
from culture_rules.engine.actorport import (
    COMPLETED,
    ActorPort,
    InvocationContext,
    InvocationResult,
)
from culture_rules.model.actor import Actor
from culture_rules.store.port import StoreOps

__all__ = ["ACTORS_COLLECTION", "ActorRouter", "AdapterFactory", "default_factories"]

ACTORS_COLLECTION = "actors"

AdapterFactory = Callable[[Actor], ActorPort]
"""Builds the adapter for one stored actor definition."""


def default_factories(store: Any, *, emitter: Any = None) -> dict[str, AdapterFactory]:
    """The production adapter factories (see the module docstring)."""
    from culture_rules.actors.agent import ColleagueActor
    from culture_rules.actors.code import CodeRunner

    def agent(actor: Actor) -> ActorPort:
        params = actor.params
        return ColleagueActor(
            repo=params.get("repo") or actor.repo, engine=params.get("engine"), model=actor.model
        )

    def runner(actor: Actor) -> ActorPort:
        return CodeRunner(actor.params.get("commands") or {}, is_admin=lambda _identity: False)

    factories: dict[str, AdapterFactory] = {"agent": agent, "runner": runner}
    if emitter is not None:
        from culture_rules.actors.human import HumanAdapter

        factories["human"] = lambda actor: HumanAdapter(store, emitter)
    return factories


class ActorRouter:
    """Routes each invocation to a stored actor's (limited) adapter or an injected port."""

    def __init__(
        self,
        store: StoreOps,
        *,
        ports: Mapping[str, ActorPort] | None = None,
        factories: Mapping[str, AdapterFactory] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._ports = dict(ports or {})
        self._factories = dict(factories or {})
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache: dict[str, tuple[Any, LimitedActor]] = {}

    def __call__(self, ctx: InvocationContext) -> ActorPort | None:
        if ctx.actor:
            limited = self.limited(ctx.actor)
            if limited is not None:
                return limited
        return self._fallback(ctx)

    def _fallback(self, ctx: InvocationContext) -> ActorPort | None:
        ports = self._ports
        if ctx.kind == "action":
            kind = ctx.config.get("kind")
            return ports.get(f"action:{kind}") or ports.get("action") or ports.get("*")
        return ports.get(ctx.kind) or ports.get("*")

    def limited(self, actor_id: str) -> LimitedActor | None:
        """The cached LimitedActor for a stored, enabled actor with a known kind, else None."""
        doc = self._store.get(ACTORS_COLLECTION, actor_id)
        if doc is None:
            return None
        actor = Actor.from_dict(doc, strict=False)
        factory = self._factories.get(actor.kind)
        if not actor.enabled or factory is None:
            return None
        revision = (doc.get("updated_at"), doc.get("schema_version"))
        cached = self._cache.get(actor_id)
        if cached is not None and cached[0] == revision:
            return cached[1]
        config = ActorConfig(key=actor.id, kind=actor.kind, extras=dict(actor.params))
        limited = LimitedActor(
            factory(actor), actor.id, limits_from_config(config), self._store, clock=self._clock
        )
        self._cache[actor_id] = (revision, limited)
        return limited

    def release(self, actor_id: str, key: str, result: InvocationResult) -> bool:
        """Free ``key``'s slot on ``actor_id`` (accepted work finished); False if not limited.

        The key is remembered as done only for a completed result."""
        limited = self.limited(actor_id)
        if limited is None:
            return False
        limited.release(key, tokens=tokens_of(result), completed=result.outcome == COMPLETED)
        return True
