"""Guards on the actor definition routes (server layer; the engine never sees them).

- ``model_taken``: create, update and enable refuse a candidate that would be an enabled
  endpoint actor holding a model another enabled endpoint actor already holds
  (:func:`culture_rules.actors.model_rule.model_holder`). Saving the actor disabled is fine.
- ``actor_in_use``: disable refuses while an enabled workflow step or rule still names the
  actor, by action or by placement (:func:`culture_rules.model.references.find_references`),
  unless ``force`` is set. Disabled and soft-deleted referrers do not count.

The checks run before the write, outside its transaction, so a concurrent writer can slip
between check and write; the rule is operator guidance, not a store invariant.
"""

from __future__ import annotations

from typing import Any

from culture_rules.actors.model_rule import is_endpoint_actor, model_holder
from culture_rules.model.references import find_references
from culture_rules.server.service import Conflict
from culture_rules.store.port import StoragePort

__all__ = ["check_disable", "check_enable", "check_save"]


def _live(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in docs if not d.get("deleted_at")]


def _refuse_if_taken(store: StoragePort, candidate: dict[str, Any]) -> None:
    if candidate.get("enabled") is False or not is_endpoint_actor(candidate):
        return  # model_holder does not look at the candidate's own enabled flag
    holder = model_holder(store.find("actors"), candidate)
    if holder is None:
        return
    model = candidate["params"]["model"]
    message = (
        f"model_taken: {model} is held by {holder}; disable {holder} first "
        "(or save this actor disabled)"
    )
    raise Conflict(
        message,
        [{"path": "params.model", "code": "model_taken", "message": message, "holder": holder}],
        code="model_taken",
    )


def check_save(store: StoragePort, body: Any, actor_id: str | None = None) -> None:
    """Create or update: evaluate the body as it would be stored (enabled unless it says not).
    On update the URL's id is the candidate's, so an actor never holds a model against itself
    (a mismatched body id is refused later, as ``id_mismatch``)."""
    if not isinstance(body, dict):
        return  # validation reports it
    candidate = {**body, "enabled": body.get("enabled", True) is not False}
    if actor_id is not None:
        candidate["id"] = actor_id
    _refuse_if_taken(store, candidate)


def check_enable(store: StoragePort, actor_id: str) -> None:
    """Enable: evaluate the stored actor as it would be once enabled."""
    doc = store.get("actors", actor_id)
    if doc is None or doc.get("deleted_at"):
        return  # set_enabled answers 404 / deleted
    _refuse_if_taken(store, {**doc, "enabled": True})


def check_disable(store: StoragePort, actor_id: str, force: bool = False) -> None:
    """Disable: refuse while enabled workflows or rules reference the actor."""
    doc = store.get("actors", actor_id)
    if force or doc is None or doc.get("deleted_at"):
        return
    refs = find_references(actor_id, _live(store.find("workflows")), _live(store.find("rules")))
    in_use = [r for r in refs if r["enabled"]]
    if not in_use:
        return
    entries = [
        {
            "path": f"{r['kind']}s/{r['id']}",
            "code": "actor_in_use",
            "message": f"{r['kind']} {r['id']} uses {actor_id} ({r['via']} at {r['path']})",
        }
        for r in in_use
    ]
    names = sorted({f"{r['kind']} {r['id']}" for r in in_use})
    message = (
        f"actor_in_use: {actor_id} is used by {', '.join(names)}; repoint or disable them, "
        "or pass force=true to disable anyway"
    )
    raise Conflict(message, entries, code="actor_in_use")
