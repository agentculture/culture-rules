"""``machine.command`` action port: run a registered command on the bound runner actor.

Params (``culture_rules.model.action_kinds``): ``actor`` (the runner actor id; falls back to
``context.actor``), ``command`` (a name in the actor's ``params.commands`` registry) and
optional ``args``, a mapping of the command's declared param names to values (anything
else is refused). The mapping is passed straight to the runner, where
:func:`culture_rules.actors.code.bind_argv` type-checks each value against the declared
param type and substitutes it into the command's fixed argv template, one value per template
part. Nothing is ever handed to a shell, so ``; rm -rf / $(x) |`` reaches the process as
one literal argument. Inline ``script`` text is never forwarded from action params.

The port delegates to :class:`~culture_rules.actors.code.CodeRunner`, built exactly as the
node's ``runner`` factory builds it (registered commands only, no admin), and cached per
actor id and definition revision so an in-process retry with the same idempotency key
replays the completed result. Limits (``LimitedActor``) are applied by the router, not
here. Standard-library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors.code import CodeRunner
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.model.actor import Actor
from culture_rules.node.actors import ACTORS_COLLECTION

__all__ = ["MachineCommandPort", "load_bound_actor"]


def load_bound_actor(
    store: Any, input: Mapping[str, Any], context: InvocationContext
) -> tuple[Actor | None, Mapping[str, Any] | None, InvocationResult | None]:
    """The enabled actor named by ``input.actor`` (else ``context.actor``) and its raw doc.

    Returns ``(actor, doc, None)`` or ``(None, None, failure)``."""
    actor_id = input.get("actor") or context.actor
    if not isinstance(actor_id, str) or not actor_id:
        return None, None, InvocationResult.failed("actor_missing", retryable=False)
    doc = store.get(ACTORS_COLLECTION, actor_id)
    if doc is None:
        return (
            None,
            None,
            InvocationResult.failed(f"actor_not_found: {actor_id!r}", retryable=False),
        )
    actor = Actor.from_dict(doc, strict=False)
    if not actor.enabled:
        return None, None, InvocationResult.failed(f"actor_disabled: {actor_id!r}", retryable=False)
    return actor, doc, None


class MachineCommandPort:
    """ActorPort for ``machine.command`` (see the module docstring)."""

    supports_idempotency_key = False  # CodeRunner's ledger is in-memory, per process

    def __init__(self, store: Any) -> None:
        self._store = store
        self._runners: dict[str, tuple[Any, CodeRunner]] = {}

    def _runner(self, actor: Actor, doc: Mapping[str, Any]) -> CodeRunner:
        revision = (doc.get("updated_at"), doc.get("schema_version"), repr(actor.params))
        cached = self._runners.get(actor.id)
        if cached is not None and cached[0] == revision:
            return cached[1]
        runner = CodeRunner(actor.params.get("commands") or {}, is_admin=lambda _identity: False)
        self._runners[actor.id] = (revision, runner)
        return runner

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        actor, doc, failure = load_bound_actor(self._store, input, context)
        if failure is not None:
            return failure
        if actor is None or doc is None:  # pragma: no cover - load_bound_actor guarantees it
            return InvocationResult.failed("actor_not_found", retryable=False)
        if actor.kind != "runner":
            return InvocationResult.failed(
                f"actor_kind_mismatch: machine.command needs a runner actor, "
                f"{actor.id!r} is {actor.kind!r}",
                retryable=False,
            )
        runner = self._runner(actor, doc)
        command = input.get("command")
        args = input.get("args")
        if args is not None and not isinstance(args, Mapping):
            return InvocationResult.failed(
                "args must be a mapping of declared param name -> value", retryable=False
            )
        return runner.invoke(
            {"command": command, "args": dict(args or {})},
            idempotency_key,
            deadline,
            context=context,
        )
