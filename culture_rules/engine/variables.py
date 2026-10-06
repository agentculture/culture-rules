"""Shared variables as the engine reads them: the node capability and current values.

A rule references a shared variable through its condition (``{"var": name}``) or a
workflow input mapped as ``{"$var": name}``
(:func:`~culture_rules.model.variable_refs.rule_variable_refs`). The engine resolves those
references from the ``variables`` collection *inside* the evaluating transaction, so a
condition and the inputs of the run it fires see one consistent value, and the next event
after a variable changes sees the new value with no rule edited.

A node advertises :data:`VARIABLES_CAPABILITY` on its heartbeat when it resolves variables.
A rule that references a variable is only evaluated where the capability is present;
anywhere else matching refuses it (``variables_unsupported``) instead of reading the
reference as missing - a missing operand makes ``not(a in vars.x)`` true, so evaluating it
without the value would fire the rule wrongly.

A node binary older than variables cannot refuse anything (it does not know the reference
exists), so the save path also refuses a variable-referencing rule while any *online* node
does not advertise the capability (:func:`nodes_without_variables`; deviation d7). A
heartbeat from such a node has no ``capabilities`` field at all. Standard-library only.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from culture_rules.machines.heartbeat import (
    HEARTBEAT_COLLECTION,
    HEARTBEAT_INTERVAL_S,
    online_machines,
)
from culture_rules.store.port import VARIABLES_COLLECTION, StoreOps

__all__ = [
    "NODE_CAPABILITIES",
    "VARIABLES_CAPABILITY",
    "defined_variables",
    "nodes_without_variables",
    "variable_values",
]

VARIABLES_CAPABILITY = "variables"
"""Heartbeat capability: this node resolves shared variables in conditions and inputs."""
NODE_CAPABILITIES: tuple[str, ...] = (VARIABLES_CAPABILITY,)
"""What a node of this version advertises by default."""


def _latest(doc: Any) -> dict[str, Any] | None:
    versions = doc.get("versions") if isinstance(doc, dict) else None
    if not isinstance(versions, list) or not versions or not isinstance(versions[-1], dict):
        return None
    return versions[-1]


def variable_values(ops: StoreOps, names: Iterable[str]) -> dict[str, Any]:
    """The current value of each defined variable in ``names`` (undefined ones are absent).

    Reads through ``ops`` (a transaction or the store), so values come from the same
    snapshot as whatever else the caller reads with it.
    """
    out: dict[str, Any] = {}
    for name in sorted(set(names)):
        latest = _latest(ops.get(VARIABLES_COLLECTION, name))
        if latest is not None and "value" in latest:
            out[name] = copy.deepcopy(latest["value"])
    return out


def defined_variables(ops: StoreOps) -> set[str]:
    """The names of every defined variable (one that has at least one version)."""
    return {
        str(doc.get("name") or doc.get("id"))
        for doc in ops.find(VARIABLES_COLLECTION)
        if _latest(doc) is not None
    }


def nodes_without_variables(
    ops: StoreOps, now: datetime, *, beat_every: float = HEARTBEAT_INTERVAL_S
) -> list[str]:
    """Online nodes (the heartbeat rule of :func:`online_machines`) whose latest heartbeat
    does not advertise :data:`VARIABLES_CAPABILITY`, sorted. Offline nodes do not count."""
    online = online_machines(ops, now, beat_every=beat_every)
    lacking = set()
    for beat in ops.find(HEARTBEAT_COLLECTION):
        name = beat.get("machine")
        if name not in online:
            continue
        caps = beat.get("capabilities")
        if not isinstance(caps, list) or VARIABLES_CAPABILITY not in caps:
            lacking.add(name)
    return sorted(lacking)
