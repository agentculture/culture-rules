"""Actor references and repoint: pure, stdlib, plain dicts in and new dicts out.

A workflow step references an actor through ``config.action.params.actor`` (an action) or
``placement.actor`` (a placement). A rule does the same through ``action`` / ``on_failure``
and ``placement``. Steps nest through ``body`` (``for_each`` loops) at any depth.

Step paths read ``summarise.body[summary]``: the top-level step id, then ``.body[<id>]`` for
each loop level. A rule's path is ``action``, ``on_failure`` or ``placement``.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Iterator
from typing import Any

__all__ = ["NoMatchingStep", "find_references", "repoint_workflow"]


class NoMatchingStep(ValueError):
    """The workflow has no step whose action names the actor; the server maps it to 409/422."""

    def __init__(self, workflow_id: str, actor_id: str) -> None:
        super().__init__(f"workflow {workflow_id!r} has no step whose action actor is {actor_id!r}")
        self.workflow_id = workflow_id
        self.actor_id = actor_id


def _walk(steps: Any, prefix: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    for step in steps or ():
        if not isinstance(step, dict):
            continue
        path = f"{prefix}.body[{step.get('id')}]" if prefix else str(step.get("id"))
        yield path, step
        yield from _walk(step.get("body"), path)


def _action_actor(action: Any) -> Any:
    params = action.get("params") if isinstance(action, dict) else None
    return params.get("actor") if isinstance(params, dict) else None


def _placement_actor(placement: Any) -> Any:
    return placement.get("actor") if isinstance(placement, dict) else None


def find_references(
    actor_id: str, workflows: Iterable[dict[str, Any]], rules: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Every workflow step and rule that names ``actor_id``, by action or by placement."""
    found: list[dict[str, Any]] = []

    def add(kind: str, doc: dict[str, Any], path: str, via: str) -> None:
        found.append(
            {
                "kind": kind,
                "id": doc.get("id"),
                "path": path,
                "via": via,
                "enabled": bool(doc.get("enabled", True)),
            }
        )

    for wf in workflows:
        for path, step in _walk(wf.get("steps")):
            config = step.get("config")
            action = config.get("action") if isinstance(config, dict) else None
            if _action_actor(action) == actor_id:
                add("workflow", wf, path, "action")
            if _placement_actor(step.get("placement")) == actor_id:
                add("workflow", wf, path, "placement")
    for rule in rules:
        for key in ("action", "on_failure"):
            if _action_actor(rule.get(key)) == actor_id:
                add("rule", rule, key, "action")
        if _placement_actor(rule.get("placement")) == actor_id:
            add("rule", rule, "placement", "placement")
    return found


def _mentions(text: str, actor_id: str) -> bool:
    """Whether ``text`` names ``actor_id`` as a whole id (``a1`` does not match ``a10``)."""
    pattern = r"(?<![A-Za-z0-9._-])" + re.escape(actor_id) + r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9])"
    return re.search(pattern, text) is not None


def repoint_workflow(
    doc: dict[str, Any], frm: str, to: str
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Rewrite ``config.action.params.actor == frm`` to ``to`` on a copy of ``doc``.

    Returns ``(new_doc, changed step paths, warnings)``. Description and notes are left
    untouched; a step whose notes mention ``frm`` and a ``placement.actor == frm`` earn a
    warning (the placement is left unchanged). Raises :class:`NoMatchingStep` when no step
    names ``frm`` in its action.
    """
    new = copy.deepcopy(doc)
    changed: list[str] = []
    warnings: list[str] = []
    for path, step in _walk(new.get("steps")):
        config = step.get("config")
        action = config.get("action") if isinstance(config, dict) else None
        if _action_actor(action) == frm:
            action["params"]["actor"] = to
            changed.append(path)
        notes = step.get("notes")
        if isinstance(notes, str) and _mentions(notes, frm):
            warnings.append(f"step {path} notes mention {frm!r}; notes were not changed")
        if _placement_actor(step.get("placement")) == frm:
            warnings.append(f"step {path} is placed on actor {frm!r}; placement was not changed")
    notes = new.get("notes")
    if isinstance(notes, str) and _mentions(notes, frm):
        warnings.append(f"workflow notes mention {frm!r}; notes were not changed")
    if not changed:
        raise NoMatchingStep(str(doc.get("id")), frm)
    return new, changed, warnings
