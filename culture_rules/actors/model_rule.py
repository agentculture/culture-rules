"""The model-holder rule: one enabled endpoint actor per model.

Pure and stdlib-only. Both the server guards and ``node/actions/_endpoint.py`` (the
endpoint-actor ports) import it, so the rule has one definition. An *endpoint actor* is an
``agent`` actor with a non-empty ``params.base_url`` (it talks to an OpenAI-compatible
endpoint). Mesh-reached agents (no ``base_url``) may share a model name freely and are never
part of the rule. An actor's model is the one its requests use: ``params.model``, else its
own ``model``.

What one holder holds is the model *key*: the model, plus ``@<params.domain>`` when the actor
sets a domain. A decider actor serves one domain of one model (``clef@general``,
``clef@code``), so one decider actor per domain is allowed, and two for one domain are not.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

#: Action kinds that call an actor's OpenAI-compatible endpoint, so the actor must be an
#: endpoint actor and is held to the one-holder-per-model rule.
ENDPOINT_KINDS = ("llm.complete", "audio.transcribe", "decider.assess")


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


def model_key(doc: dict[str, Any]) -> str | None:
    """What the actor holds: its model, plus ``@<domain>`` when ``params.domain`` is set."""
    model = effective_model(doc)
    if model is None:
        return None
    domain = _params(doc).get("domain")
    if isinstance(domain, str) and domain.strip():
        return f"{model}@{domain.strip()}"
    return model


def _holders(actors: Iterable[dict[str, Any]], candidate: dict[str, Any]) -> list[str]:
    """Ids of the other enabled, non-deleted endpoint actors with the candidate's model key."""
    if not is_endpoint_actor(candidate):
        return []
    model = model_key(candidate)
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
        and model_key(doc) == model
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
