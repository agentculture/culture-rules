"""Guards on the actor definition routes (server layer; the engine never sees them).

- ``model_taken``: create, update, enable, restore and import refuse a candidate that would
  be an enabled endpoint actor holding a model another enabled endpoint actor already holds
  (:func:`culture_rules.actors.model_rule.model_holder`). Saving the actor disabled is fine.
- ``actor_in_use``: disable and delete refuse while an enabled workflow step or rule still
  names the actor, by action or by placement
  (:func:`culture_rules.model.references.find_references`), unless ``force`` is set.
  Disabled and soft-deleted referrers do not count.

Every check is a no-op for a kind other than ``actors``, so the generic definition routes
call them unconditionally. The checks run before the write, outside its transaction, so a
concurrent writer can slip between check and write; the rule is operator guidance, not a
store invariant (``llm.complete`` refuses again at run time).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from culture_rules.actors.model_rule import effective_model, is_endpoint_actor, model_holder
from culture_rules.io import exchange
from culture_rules.model.references import find_references
from culture_rules.server.service import Conflict
from culture_rules.store.port import StoragePort

__all__ = [
    "check_delete",
    "check_disable",
    "check_enable",
    "check_import",
    "check_restore",
    "check_save",
]

ACTORS = "actors"


def _live(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in docs if not d.get("deleted_at")]


def _taken_entry(candidate: dict[str, Any], holder: str, path: str) -> dict[str, Any]:
    model = effective_model(candidate)
    message = (
        f"model_taken: {model} is held by {holder}; disable {holder} first "
        "(or save this actor disabled)"
    )
    return {"path": path, "code": "model_taken", "message": message, "holder": holder}


def _holder(actors: Any, candidate: dict[str, Any]) -> str | None:
    if candidate.get("enabled") is False or not is_endpoint_actor(candidate):
        return None  # model_holder does not look at the candidate's own enabled flag
    return model_holder(actors, candidate)


def _refuse_if_taken(store: StoragePort, candidate: dict[str, Any]) -> None:
    holder = _holder(store.find(ACTORS), candidate)
    if holder is None:
        return
    entry = _taken_entry(candidate, holder, "params.model")
    raise Conflict(entry["message"], [entry], code="model_taken")


def check_save(store: StoragePort, kind: str, body: Any, actor_id: str | None = None) -> None:
    """Create or update: evaluate the body as it would be stored (enabled unless it says not).
    On update the URL's id is the candidate's, so an actor never holds a model against itself
    (a mismatched body id is refused later, as ``id_mismatch``)."""
    if kind != ACTORS or not isinstance(body, dict):
        return  # validation reports a non-object body
    candidate = {**body, "enabled": body.get("enabled", True) is not False}
    if actor_id is not None:
        candidate["id"] = actor_id
    _refuse_if_taken(store, candidate)


def check_enable(store: StoragePort, kind: str, actor_id: str) -> None:
    """Enable: evaluate the stored actor as it would be once enabled."""
    doc = store.get(ACTORS, actor_id) if kind == ACTORS else None
    if doc is None or doc.get("deleted_at"):
        return  # set_enabled answers 404 / deleted
    _refuse_if_taken(store, {**doc, "enabled": True})


def check_restore(store: StoragePort, kind: str, actor_id: str) -> None:
    """Restore: evaluate the deleted actor as it would be once restored (its own enabled
    flag kept), so a restore never brings back a second holder."""
    doc = store.get(ACTORS, actor_id) if kind == ACTORS else None
    if doc is None or not doc.get("deleted_at"):
        return  # restore answers 404 / not deleted
    _refuse_if_taken(store, {**doc, "deleted_at": None})


def check_import(store: StoragePort, files: Mapping[str, str]) -> None:
    """Import: each enabled endpoint actor in the bundle against the stored actors with the
    bundle laid over them, so a bundle that disables one holder and adds the next passes."""
    incoming = [a.to_dict() for a in exchange.read_files(files).bundle.actors]
    if not incoming:
        return
    effective = {d["id"]: d for d in store.find(ACTORS)}
    effective.update({a["id"]: a for a in incoming})
    entries = [
        _taken_entry(actor, holder, f"actors/{actor['id']}")
        for actor in incoming
        if (holder := _holder(effective.values(), actor)) is not None
    ]
    if entries:
        message = "; ".join(e["message"] for e in entries)
        raise Conflict(message, entries, code="model_taken")


def _refuse_if_in_use(store: StoragePort, actor_id: str, verb: str) -> None:
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
        f"or pass force=true to {verb} anyway"
    )
    raise Conflict(message, entries, code="actor_in_use")


def _check_in_use(store: StoragePort, kind: str, actor_id: str, force: bool, verb: str) -> None:
    doc = store.get(ACTORS, actor_id) if kind == ACTORS and not force else None
    if doc is None or doc.get("deleted_at"):
        return
    _refuse_if_in_use(store, actor_id, verb)


def check_disable(store: StoragePort, kind: str, actor_id: str, force: bool = False) -> None:
    """Disable: refuse while enabled workflows or rules reference the actor."""
    _check_in_use(store, kind, actor_id, force, "disable")


def check_delete(store: StoragePort, kind: str, actor_id: str, force: bool = False) -> None:
    """Delete (soft): refuse while enabled workflows or rules reference the actor, whether
    or not the actor itself is still enabled."""
    _check_in_use(store, kind, actor_id, force, "delete")
