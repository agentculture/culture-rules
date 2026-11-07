"""The actor a step's action is bound to, shared by the action ports (standard library only).

:func:`load_bound_actor` reads the enabled actor named by ``input.actor`` (else
``context.actor``). The ``machine.command``, ``http.call``, ``llm.complete``,
``decider.assess``, ``audio.transcribe``, ``library.push``, ``repodocs.sync`` and
``transcript.render`` ports use it (lifted from the ``machine.command`` port in the Boundary
release, so those apps share core code rather than one another).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.model.actor import Actor
from culture_rules.node.actors import ACTORS_COLLECTION

__all__ = ["load_bound_actor"]


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
