"""Rule: *when* work happens — Trigger -> [Condition] -> [Workflow] -> Action."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from culture_rules.model.action import Action
from culture_rules.model.common import SCHEMA_VERSION, Model, doc
from culture_rules.model.placement import Placement

__all__ = ["Rule", "Trigger", "WorkflowRef"]


@dataclass(frozen=True, kw_only=True)
class Trigger(Model):
    """What fires a rule: an event, a schedule, a manual run, ..."""

    kind: str = doc("Trigger type, e.g. event, schedule, manual")
    params: dict[str, Any] = doc("Kind-specific parameters", default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class WorkflowRef(Model):
    """A reference from a rule to a reusable workflow, with input mappings."""

    id: str = doc("Workflow id")
    version: int | None = doc("Pinned workflow version; null means latest", default=None)
    inputs: dict[str, str] = doc(
        "Workflow input name -> reference (e.g. trigger.data.number)", default_factory=dict
    )


@dataclass(frozen=True, kw_only=True)
class Rule(Model):
    """Binds a trigger (+ optional condition) to an optional workflow and a required action.

    Relationships to other rules are edges carried here, not extra stages.
    """

    id: str = doc("Stable rule id")
    name: str = doc("Display name")
    description: str = doc("Free text", default="")
    trigger: Trigger = doc("What fires the rule")
    condition: dict[str, Any] | None = doc(
        "Condition tree (JSON object); null means always", default=None
    )
    workflow: WorkflowRef | None = doc(
        "Workflow to run; null means Trigger -> Action", default=None
    )
    action: Action = doc("The terminal side effect (required)")
    placement: Placement | None = doc(
        "Where the trigger and condition evaluate (machine, actor or requirement); "
        "null means any eligible engine node",
        default=None,
    )
    must_after: tuple[str, ...] = doc(
        "Rule ids that must have succeeded before this rule fires", default=()
    )
    may_after: tuple[str, ...] = doc(
        "Rule ids whose exported outputs are visible if they ran", default=()
    )
    supersedes: tuple[str, ...] = doc(
        "Rule ids that do not fire for an event this rule matched", default=()
    )
    exclusive_group: str | None = doc(
        "Only the highest-priority matching rule of a group fires", default=None
    )
    priority: int = doc("Higher wins within an exclusive group", default=0)
    enabled: bool = doc("Disabled rules never fire", default=True)
    schema_version: str = doc("Document schema version (MAJOR.MINOR)", default=SCHEMA_VERSION)
