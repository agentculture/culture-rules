"""Machine: an explicitly enrolled host that placement may choose."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from culture_rules.model.common import SCHEMA_VERSION, Model, doc

__all__ = ["INSTALL_MODES", "MACHINE_ROLES", "Machine", "MachineInstall"]

MachineRole = Literal["store_member", "engine_node", "runner"]
MACHINE_ROLES: tuple[str, ...] = ("store_member", "engine_node", "runner")

InstallMode = Literal["install_sh", "shared_venv"]
INSTALL_MODES: tuple[str, ...] = ("install_sh", "shared_venv")


@dataclass(frozen=True, kw_only=True)
class MachineInstall(Model):
    """How the node on this machine is installed; mirrors deploy/node/install.sh's flags.

    ``install_sh`` re-runs ``install.sh`` with these fields as its flags. ``shared_venv`` is a
    machine whose API and node share one venv (spark): it is never installed by
    ``install.sh``, so the remaining fields do not apply to it. ``None`` on an optional field
    means "install.sh's default".
    """

    mode: InstallMode = doc("install_sh | shared_venv (API and node in one venv)")
    node_name: str | None = doc(
        "install.sh --node-name; defaults to the machine name", default=None
    )
    secrets: tuple[str, ...] = doc(
        "Grant secret NAMES ([A-Z0-9_]+) passed as install.sh --secret; never values", default=()
    )
    gate_run_as: str | None = doc(
        "install.sh --gate-run-as: a 'sudo -n -u <user> ...' run-as prefix", default=None
    )
    extras: tuple[str, ...] | None = doc("install.sh --extras (joined with ',')", default=None)
    wheelhouse: str | None = doc("install.sh --wheelhouse (offline install dir)", default=None)
    python: str | None = doc("install.sh --python (venv Python version)", default=None)
    events_host: str | None = doc("install.sh --events-host", default=None)
    events_port: int | None = doc("install.sh --events-port (1-65535)", default=None)


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
    install: MachineInstall | None = doc(
        "How the node here is installed (rollout); absent on records that predate it",
        default=None,
    )
    schema_version: str = doc("Document schema version (MAJOR.MINOR)", default=SCHEMA_VERSION)

    def to_dict(self) -> dict[str, Any]:
        """As :meth:`Model.to_dict`, minus ``install`` when unset, so a record that predates
        the install section saves back exactly as it was stored."""
        data = super().to_dict()
        if data.get("install") is None:
            data.pop("install", None)
        return data
