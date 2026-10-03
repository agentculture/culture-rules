"""Machine enrolment: explicit, dry-run unless ``apply``. Records live in ``machines``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from culture_rules.model.machine import Machine
from culture_rules.model.validate import validate
from culture_rules.store.port import StoreOps

__all__ = ["MACHINES_COLLECTION", "EnrolResult", "enrol", "enrolled_machines", "unenrol"]

MACHINES_COLLECTION = "machines"

Action = Literal["create", "update", "unchanged", "delete", "absent"]


@dataclass(frozen=True)
class EnrolResult:
    """What an enrol/unenrol did (``applied``) or would do (dry run)."""

    machine: str
    action: Action
    applied: bool

    def to_dict(self) -> dict[str, object]:
        return {"machine": self.machine, "action": self.action, "applied": self.applied}


def _load(store: StoreOps, name: str) -> Machine | None:
    stored = store.get(MACHINES_COLLECTION, name)
    return Machine.from_dict(_strip(stored), strict=False) if stored else None


def _strip(document: dict) -> dict:
    return {k: v for k, v in document.items() if k not in ("id", "updated_at")}


def enrol(store: StoreOps, machine: Machine, *, apply: bool = False) -> EnrolResult:
    """Create or update a Machine record; writes only when ``apply`` is true."""
    errors = validate(machine)
    if errors:
        raise ValueError("; ".join(f"{e.path}: {e.message}" for e in errors))
    current = _load(store, machine.name)
    if current is None:
        action: Action = "create"
    elif current == machine:
        action = "unchanged"
    else:
        action = "update"
    if apply and action != "unchanged":
        store.put(MACHINES_COLLECTION, {"id": machine.name, **machine.to_dict()})
    return EnrolResult(machine.name, action, applied=apply and action != "unchanged")


def unenrol(store: StoreOps, name: str, *, apply: bool = False) -> EnrolResult:
    """Remove a Machine record; deletes only when ``apply`` is true."""
    exists = store.get(MACHINES_COLLECTION, name) is not None
    action: Action = "delete" if exists else "absent"
    if apply and exists:
        store.delete(MACHINES_COLLECTION, name)
    return EnrolResult(name, action, applied=apply and exists)


def enrolled_machines(store: StoreOps) -> list[Machine]:
    """All enrolled machines (tolerant read), ordered by name."""
    return [Machine.from_dict(_strip(d), strict=False) for d in store.find(MACHINES_COLLECTION)]
