"""Machine: an explicitly enrolled host that placement may choose."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from culture_rules.model.common import SCHEMA_VERSION, Model, doc

__all__ = ["MACHINE_ROLES", "Machine"]

MachineRole = Literal["store_member", "engine_node", "runner"]
MACHINE_ROLES: tuple[str, ...] = ("store_member", "engine_node", "runner")


@dataclass(frozen=True, kw_only=True)
class Machine(Model):
    """An enrolled machine: identity (mesh server name), address, platform, capabilities."""

    name: str = doc("Machine identity (the mesh server name)")
    address: str | None = doc("Network address; defaults to the tailnet name", default=None)
    platform: str = doc("e.g. linux-aarch64", default="")
    capabilities: tuple[str, ...] = doc("Capabilities offered, e.g. gpu", default=())
    roles: tuple[MachineRole, ...] = doc("store_member | engine_node | runner", default=())
    description: str = doc("Free text", default="")
    enabled: bool = doc("Disabled machines receive no placements", default=True)
    schema_version: str = doc("Document schema version (MAJOR.MINOR)", default=SCHEMA_VERSION)
