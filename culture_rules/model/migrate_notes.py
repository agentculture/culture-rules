"""Move a workflow's plain-text ``description`` into ``notes`` (c17, h14, c35, h32).

``notes`` is the merged free-text field; ``description`` now means plain text only. The
migration is one pure function over a workflow definition (a mapping, as stored or as read
from a committed ``docs/rules`` file), reused by ``POST /workflows/migrate-notes`` and by the
migration of the committed definitions. Standard-library only.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

__all__ = ["WORKFLOW_NODE", "migrate_notes", "notes_changes"]

WORKFLOW_NODE = "workflow"
"""The node name :func:`notes_changes` uses for the workflow itself (steps use their ids)."""


def _movable(node: Mapping[str, Any]) -> bool:
    notes, description = node.get("notes"), node.get("description")
    return not notes and isinstance(description, str) and description != ""


def _walk(steps: Any, found: list[dict[str, Any]]) -> None:
    for step in steps if isinstance(steps, list) else ():
        if isinstance(step, dict):
            found.append(step)
            _walk(step.get("body"), found)


def _nodes(definition: dict[str, Any]) -> list[dict[str, Any]]:
    """The workflow and every step (loop bodies included), in document order."""
    found = [definition]
    _walk(definition.get("steps"), found)
    return found


def notes_changes(definition: Mapping[str, Any]) -> list[str]:
    """The nodes :func:`migrate_notes` would change: ``"workflow"`` and/or step ids."""
    if not isinstance(definition, Mapping):
        return []
    nodes = _nodes(dict(definition) if not isinstance(definition, dict) else definition)
    return [
        WORKFLOW_NODE if node is definition else str(node.get("id"))
        for node in nodes
        if _movable(node)
    ]


def migrate_notes(definition: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``definition`` with each node's ``description`` moved to its ``notes``.

    Applied per node: the workflow and every step, loop bodies included. When a node's
    ``notes`` is empty and its ``description`` is not, ``notes`` becomes the description
    byte for byte and ``description`` becomes ``""``. A node whose ``notes`` is already set
    is left completely alone, description included. Pure (the input is never mutated) and
    idempotent: migrating the result changes nothing.
    """
    out = copy.deepcopy(dict(definition))
    for node in _nodes(out):
        if _movable(node):
            node["notes"] = node["description"]
            node["description"] = ""
    return out
