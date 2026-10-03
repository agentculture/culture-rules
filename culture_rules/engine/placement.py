"""Placement resolver: a machine, actor or requirement placement to exactly one host.

A pure function over ``(placement, machines snapshot, liveness snapshot, actors snapshot)``;
no I/O. A host is *eligible* when it is enrolled, enabled, online, not drained, and its
dispatch address is a LAN or tailnet address (never a public hostname such as
``rules.culture.dev``). Requirement placements pick the first eligible host by name, so the
result is deterministic for a given snapshot.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from culture_rules.model.actor import Actor
from culture_rules.model.machine import Machine
from culture_rules.model.placement import Placement
from culture_rules.model.validate import ValidationError

__all__ = [
    "PUBLIC_HOSTNAMES",
    "MachineState",
    "PlacementError",
    "Resolved",
    "is_dispatchable_address",
    "resolve_placement",
    "resolve_rule_placement",
    "resolve_step_placement",
]

#: Public-facing hostnames that must never be used as a peer dispatch address.
PUBLIC_HOSTNAMES: frozenset[str] = frozenset({"rules.culture.dev"})

_TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
_PRIVATE_SUFFIXES = (".ts.net", ".local", ".lan", ".internal", ".home.arpa")


@dataclass(frozen=True)
class MachineState:
    """Liveness/drain state of one machine, as reported by the heartbeat layer."""

    name: str
    online: bool
    drained: bool = False


@dataclass(frozen=True)
class Resolved:
    """The one concrete host a placement resolved to."""

    machine: str
    address: str
    actor: str | None = None


@dataclass(frozen=True)
class PlacementError:
    """Structured failure; ``rejected`` lists the hosts considered and why they were refused."""

    code: str
    message: str
    path: str = "placement"
    rejected: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "code": self.code,
            "message": self.message,
            "rejected": list(self.rejected),
        }

    def to_validation_error(self) -> ValidationError:
        return ValidationError(self.path, self.code, self.message)


def is_dispatchable_address(address: str) -> bool:
    """True for LAN, loopback and tailnet addresses; false for public hosts/IPs."""
    host = (address or "").strip().lower().rstrip(".")
    if not host or host in PUBLIC_HOSTNAMES:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if isinstance(ip, ipaddress.IPv4Address) and ip in _TAILNET_V4:
            return True
        return ip.is_private or ip.is_loopback or ip.is_link_local
    if any(ch in host for ch in "/:@ \t"):
        return False
    return "." not in host or host.endswith(_PRIVATE_SUFFIXES)


def _check_machine(
    machine: Machine | None, name: str, states: dict[str, MachineState]
) -> PlacementError | None:
    if machine is None:
        return PlacementError("placement.machine_unknown", f"machine {name!r} is not enrolled")
    if not machine.enabled:
        return PlacementError("placement.machine_disabled", f"machine {name!r} is disabled")
    state = states.get(name)
    if state is None or not state.online:
        return PlacementError("placement.machine_offline", f"machine {name!r} is not online")
    if state.drained:
        return PlacementError("placement.machine_drained", f"machine {name!r} is drained")
    address = machine.address or machine.name
    if not is_dispatchable_address(address):
        return PlacementError(
            "placement.address_refused",
            f"machine {name!r} address {address!r} is not a LAN/tailnet address",
        )
    return None


def _resolved(machine: Machine, actor: str | None = None) -> Resolved:
    return Resolved(machine.name, machine.address or machine.name, actor)


def resolve_placement(
    placement: Placement | None,
    machines: Iterable[Machine],
    states: Iterable[MachineState],
    actors: Iterable[Actor],
) -> Resolved | PlacementError:
    """Resolve ``placement`` to one eligible host. ``None`` means any eligible host."""
    by_name = {mc.name: mc for mc in machines}
    state_by_name = {s.name: s for s in states}
    actor_list = list(actors)
    if placement is None:
        return _resolve_requirement((), by_name, state_by_name, actor_list)
    form = placement.form
    if form == "machine":
        name = placement.machine or ""
        err = _check_machine(by_name.get(name), name, state_by_name)
        return err or _resolved(by_name[name])
    if form == "actor":
        return _resolve_actor(placement.actor or "", by_name, state_by_name, actor_list)
    if form == "requirement":
        return _resolve_requirement(
            tuple(placement.requirement or ()), by_name, state_by_name, actor_list
        )
    return PlacementError(
        "placement.invalid_form",
        "placement must set exactly one of machine, actor or requirement",
    )


def _resolve_actor(
    actor_id: str,
    by_name: dict[str, Machine],
    states: dict[str, MachineState],
    actors: list[Actor],
) -> Resolved | PlacementError:
    actor = next((x for x in actors if x.id == actor_id), None)
    if actor is None:
        return PlacementError("placement.actor_unknown", f"actor {actor_id!r} is not defined")
    if not actor.enabled:
        return PlacementError("placement.actor_disabled", f"actor {actor_id!r} is disabled")
    if not actor.machine:
        return PlacementError(
            "placement.actor_unplaced", f"actor {actor_id!r} does not live on a machine"
        )
    err = _check_machine(by_name.get(actor.machine), actor.machine, states)
    return err or _resolved(by_name[actor.machine], actor_id)


def _resolve_requirement(
    required: tuple[str, ...],
    by_name: dict[str, Machine],
    states: dict[str, MachineState],
    actors: list[Actor],
) -> Resolved | PlacementError:
    need = set(required)
    advertised: dict[str, set[str]] = {
        name: set(mc.capabilities) for name, mc in by_name.items() if mc.enabled
    }
    for actor in actors:
        if actor.enabled and actor.machine in advertised:
            advertised[actor.machine] |= set(actor.capabilities)
    matching = sorted(name for name, caps in advertised.items() if need <= caps)
    label = ", ".join(sorted(need)) or "any"
    if not matching:
        return PlacementError(
            "placement.requirement_unmet",
            f"no enrolled machine or actor advertises required capability: {label}",
            path="placement.requirement",
        )
    rejected: list[str] = []
    for name in matching:
        err = _check_machine(by_name[name], name, states)
        if err is None:
            return _resolved(by_name[name])
        rejected.append(f"{name}: {err.code}")
    return PlacementError(
        "placement.no_eligible_host",
        f"no eligible host for requirement ({label}); all matching hosts refused",
        path="placement.requirement",
        rejected=tuple(rejected),
    )


def resolve_step_placement(
    step: Any,
    machines: Iterable[Machine],
    states: Iterable[MachineState],
    actors: Iterable[Actor],
) -> Resolved | PlacementError:
    """Resolve a workflow ``Step``'s placement."""
    return resolve_placement(step.placement, machines, states, actors)


def resolve_rule_placement(
    rule: Any,
    machines: Iterable[Machine],
    states: Iterable[MachineState],
    actors: Iterable[Actor],
) -> Resolved | PlacementError:
    """Resolve a ``Rule``'s placement (deviation d1) exactly as a step's."""
    return resolve_placement(rule.placement, machines, states, actors)
