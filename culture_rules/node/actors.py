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
* A rule action (kind ``"action"``) that names an actor in ``params.actor`` runs through
  the injected action port (``action:<kind>``, ``action``, ``"*"``) wrapped in a
  LimitedActor with *that* actor's limits (cached per actor id, action kind and definition
  revision); ``context.actor`` is the id, so the port loads the actor document itself.
  A named actor that is missing or disabled gets a port answering a non-retryable
  ``failed`` with error :data:`~culture_rules.engine.runs.ACTOR_UNAVAILABLE` - never a
  fallback to another action port.
* Otherwise the injected ``ports`` are used the way the executor routes a mapping:
  ``action:<kind>``, ``action``, then the step kind, then ``"*"``.

:func:`default_factories` gives the production adapters: ``agent`` -> one-shot
``colleague work`` (:class:`~culture_rules.actors.agent.ColleagueActor`), or, when the
actor's ``params`` name a ``bridge_url``, an async cultureagent bridge session
(:class:`~culture_rules.actors.agent.BridgeAgentActor`; ``params``: ``bridge_url``,
``callback_url`` the API base URL the bridge posts its callbacks to (``POST
/bridge-invocations/{id}/events``; an ``{id}`` placeholder in it is filled instead),
``bridge_token`` a ``grant:`` reference for the bridge's bearer token,
``model``/``sandbox``/``mode`` defaults - a ``read-only`` sandbox cannot be widened by a
step - and an optional ``max_bound_input_chars`` that refuses bound inputs the bridge would
cut),
``runner`` ->
registered commands only (:class:`~culture_rules.actors.code.CodeRunner`, inline scripts
refused), ``human`` -> asks (:class:`~culture_rules.actors.human.HumanAdapter`, only when
an event emitter is configured). :meth:`ActorRouter.release` frees a LimitedActor slot for
accepted work that finished later, marking the key done only when it completed; the
deliver paths use the store-only :func:`culture_rules.node.completions.release_slot`.
``MeshAgentActor`` (mesh replies, polled) is not among the production factories.
Standard-library only.
"""

from __future__ import annotations

import inspect
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
from culture_rules.engine.runs import ACTOR_UNAVAILABLE
from culture_rules.model.actor import Actor
from culture_rules.store.port import StoreOps

__all__ = ["ACTORS_COLLECTION", "ActorRouter", "AdapterFactory", "default_factories"]

ACTORS_COLLECTION = "actors"

AdapterFactory = Callable[[Actor], ActorPort]
"""Builds the adapter for one stored actor definition."""


def default_factories(store: Any, *, emitter: Any = None) -> dict[str, AdapterFactory]:
    """The production adapter factories (see the module docstring)."""
    from culture_rules.actors.agent import BridgeAgentActor, ColleagueActor
    from culture_rules.actors.code import CodeRunner

    def agent(actor: Actor, doc: Mapping[str, Any]) -> ActorPort:
        """``doc`` is the raw stored document (required): the bridge adapter's security
        snapshot, never a sanitised model dump that may have dropped fields."""
        params = actor.params
        if params.get("bridge_url"):
            return BridgeAgentActor(
                store,
                bridge_url=str(params["bridge_url"]),
                callback_url=params.get("callback_url"),
                token=params.get("bridge_token"),
                defaults={
                    "model": params.get("model") or actor.model,
                    "sandbox": params.get("sandbox"),
                    "mode": params.get("mode"),
                    "locked_instruction": params.get("locked_instruction"),
                },
                actor_id=actor.id,
                max_bound_input_chars=params.get("max_bound_input_chars"),
                # the raw stored document (round 5): the model drops fields like deleted_at
                actor_doc=doc,
            )
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
        self._action_cache: dict[tuple[str, str], tuple[Any, ActorPort, LimitedActor]] = {}

    def __call__(self, ctx: InvocationContext) -> ActorPort | None:
        if ctx.kind == "action":
            params = ctx.config.get("params") or {}
            if params.get("actor") is not None:
                return self._action_port(ctx, params["actor"])
            return self._fallback(ctx)
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

    def _action_port(self, ctx: InvocationContext, actor_id: Any) -> ActorPort | None:
        """The named actor's limited action port, or an ``actor_unavailable`` port."""
        doc = self._store.get(ACTORS_COLLECTION, actor_id) if isinstance(actor_id, str) else None
        if _tombstoned(doc):  # judged on the raw document: the model drops deleted_at
            return _Unavailable()
        actor = Actor.from_dict(doc, strict=False) if doc is not None else None
        if actor is None or not actor.enabled:
            return _Unavailable()
        inner = self._fallback(ctx)
        if inner is None:
            return None  # the executor fails the step with no_actor_port
        revision = (doc.get("updated_at"), doc.get("schema_version"))
        key = (actor.id, str(ctx.config.get("kind")))
        cached = self._action_cache.get(key)
        if cached is not None and cached[0] == revision and cached[1] is inner:
            return cached[2]
        config = ActorConfig(key=actor.id, kind=actor.kind, extras=dict(actor.params))
        limited = LimitedActor(
            inner, actor.id, limits_from_config(config), self._store, clock=self._clock
        )
        self._action_cache[key] = (revision, inner, limited)
        return limited

    def limited(self, actor_id: str) -> LimitedActor | None:
        """The cached LimitedActor for a stored, enabled actor with a known kind, else None."""
        doc = self._store.get(ACTORS_COLLECTION, actor_id)
        if doc is None or _tombstoned(doc):  # before conversion and before the cache
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
            _build(factory, actor, doc),
            actor.id,
            limits_from_config(config),
            self._store,
            clock=self._clock,
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


def _tombstoned(doc: Any) -> bool:
    """A soft-deleted actor document (``deleted_at`` set): never routed to."""
    return isinstance(doc, Mapping) and bool(doc.get("deleted_at"))


def _build(factory: Any, actor: Actor, doc: Mapping[str, Any]) -> ActorPort:
    """``factory(actor, doc)`` for a factory that takes the raw stored document (the
    security snapshot, round 5), else ``factory(actor)``."""
    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return factory(actor)
    if len(params) >= 2:
        return factory(actor, dict(doc))
    return factory(actor)


class _Unavailable:
    """The port for a rule action naming an unknown or disabled actor: always refuses."""

    supports_idempotency_key = True

    def invoke(
        self,
        _input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        return InvocationResult.failed(ACTOR_UNAVAILABLE, retryable=False)
