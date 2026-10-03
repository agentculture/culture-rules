"""Actor: *who/what* performs work. Referenced from steps; never a rule stage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from culture_rules.model.common import SCHEMA_VERSION, Model, doc

__all__ = ["ACTOR_KINDS", "CONFIG_SOURCES", "Actor"]

ActorKind = Literal["agent", "human", "service", "daemon", "runner", "robot"]
ConfigSource = Literal["db", "repo"]

ACTOR_KINDS: tuple[str, ...] = ("agent", "human", "service", "daemon", "runner", "robot")
CONFIG_SOURCES: tuple[str, ...] = ("db", "repo")


@dataclass(frozen=True, kw_only=True)
class Actor(Model):
    """A generic actor with a concrete kind and a list of capabilities."""

    id: str = doc("Stable actor id (agents: the mesh nick)")
    name: str = doc("Display name")
    description: str = doc("Free text", default="")
    kind: ActorKind = doc("agent | human | service | daemon | runner | robot")
    capabilities: tuple[str, ...] = doc("What this actor can do", default=())
    harness: str | None = doc("Agent harness, e.g. claude, codex, colleague", default=None)
    model: str | None = doc("Model the harness runs", default=None)
    machine: str | None = doc("Machine the actor lives on", default=None)
    config_source: ConfigSource = doc(
        "Where the definition comes from: db record or repo config", default="db"
    )
    repo: str | None = doc(
        "Repo whose root config defines the actor (config_source=repo)", default=None
    )
    params: dict[str, Any] = doc("Kind-specific settings (no secret values)", default_factory=dict)
    enabled: bool = doc("Disabled actors receive no work", default=True)
    schema_version: str = doc("Document schema version (MAJOR.MINOR)", default=SCHEMA_VERSION)
