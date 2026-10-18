"""The pre-t12 form of a workflow definition, rebuilt from the migrated one (t12).

``migrate_notes`` moved each node's plain-text ``description`` into ``notes``. This is its
inverse for nodes whose ``description`` is empty: the historical v1 digest is computed from
the description, so tests that exercise the v1 path rebuild the old definition with this
instead of pinning a stale digest in a test.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import Any


def nodes(definition: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """The workflow and every step, loop bodies included."""
    yield definition
    stack = list(definition.get("steps") or [])
    while stack:
        step = stack.pop(0)
        if isinstance(step, dict):
            yield step
            stack[:0] = list(step.get("body") or [])


def pre_migration(definition: dict[str, Any]) -> dict[str, Any]:
    """A copy of a migrated definition with each node's ``notes`` back in ``description``."""
    out = copy.deepcopy(definition)
    for node in nodes(out):
        if node.get("notes") and not node.get("description"):
            node["description"], node["notes"] = node["notes"], ""  # the committed form had ""
    return out
