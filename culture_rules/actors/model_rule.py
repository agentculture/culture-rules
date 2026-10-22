"""The model-holder rule: one enabled endpoint actor per model.

Pure and stdlib-only. Both the server guards and ``node/actions/llm.py`` import it, so
the rule has one definition. An *endpoint actor* is an ``agent`` actor with a non-empty
``params.base_url`` (it talks to an OpenAI-compatible endpoint). Mesh-reached agents
(no ``base_url``) may share a model name freely and are never part of the rule. An
actor's model is the one its requests use: ``params.model``, else its own ``model``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _params(doc: dict[str, Any]) -> dict[str, Any]:
    params = doc.get("params")
    return params if isinstance(params, dict) else {}


def is_endpoint_actor(doc: dict[str, Any]) -> bool:
    """True only for kind ``agent`` with a non-empty string ``params.base_url``."""
    if doc.get("kind") != "agent":
        return False
    base_url = _params(doc).get("base_url")
    return isinstance(base_url, str) and bool(base_url.strip())


def effective_model(doc: dict[str, Any]) -> str | None:
    """The model an actor's requests use: ``params.model``, else ``model``; None when unset."""
    for model in (_params(doc).get("model"), doc.get("model")):
        if isinstance(model, str) and model.strip():
            return model.strip()
    return None


def _holders(actors: Iterable[dict[str, Any]], candidate: dict[str, Any]) -> list[str]:
    """Ids of the other enabled, non-deleted endpoint actors with the candidate's model."""
    if not is_endpoint_actor(candidate):
        return []
    model = effective_model(candidate)
    if model is None:
        return []
    cand_id = candidate.get("id")
    return [
        doc.get("id")
        for doc in actors
        if not (cand_id is not None and doc.get("id") == cand_id)
        and doc.get("enabled")
        and not doc.get("deleted_at")
        and is_endpoint_actor(doc)
        and effective_model(doc) == model
    ]


def model_holder(actors: Iterable[dict[str, Any]], candidate: dict[str, Any]) -> str | None:
    """Id of another enabled, non-deleted endpoint actor with the candidate's model, or None.

    A candidate that is not an endpoint actor, or has no model, never conflicts. The
    candidate itself (same id) is never its own holder; disabled and soft-deleted
    (``deleted_at`` set) actors hold nothing.
    """
    holders = _holders(actors, candidate)
    return holders[0] if holders else None


def senior_holder(actors: Iterable[dict[str, Any]], candidate: dict[str, Any]) -> str | None:
    """Like :func:`model_holder`, but only a holder whose id sorts before the candidate's.

    The run-time check: when two enabled endpoint actors already share a model, the one
    with the lowest id keeps serving and only the others are refused (every node picks
    the same winner, since actors carry no creation time)."""
    cand_id = str(candidate.get("id") or "")
    senior = sorted(str(h) for h in _holders(actors, candidate) if str(h) < cand_id)
    return senior[0] if senior else None
