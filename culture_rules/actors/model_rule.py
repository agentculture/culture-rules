"""The model-holder rule: one enabled endpoint actor per ``params.model``.

Pure and stdlib-only. Both the server guards and ``node/actions/llm.py`` import it, so
the rule has one definition. An *endpoint actor* is an ``agent`` actor with a non-empty
``params.base_url`` (it talks to an OpenAI-compatible endpoint). Mesh-reached agents
(no ``base_url``) may share a model name freely and are never part of the rule.
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


def _model(doc: dict[str, Any]) -> str | None:
    model = _params(doc).get("model")
    return model if isinstance(model, str) and model.strip() else None


def model_holder(actors: Iterable[dict[str, Any]], candidate: dict[str, Any]) -> str | None:
    """Id of another enabled, non-deleted endpoint actor with the candidate's model, or None.

    A candidate that is not an endpoint actor, or has no model, never conflicts. The
    candidate itself (same id) is never its own holder; disabled and soft-deleted
    (``deleted_at`` set) actors hold nothing.
    """
    if not is_endpoint_actor(candidate):
        return None
    model = _model(candidate)
    if model is None:
        return None
    cand_id = candidate.get("id")
    for doc in actors:
        if cand_id is not None and doc.get("id") == cand_id:
            continue
        if not doc.get("enabled") or doc.get("deleted_at"):
            continue
        if is_endpoint_actor(doc) and _model(doc) == model:
            return doc.get("id")
    return None
