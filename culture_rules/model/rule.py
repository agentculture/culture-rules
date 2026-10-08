"""Rule: *when* work happens — Trigger -> [Condition] -> [Workflow] -> Action."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from culture_rules.model.action import Action
from culture_rules.model.common import SCHEMA_VERSION, Model, doc
from culture_rules.model.placement import Placement

__all__ = ["Rule", "TRIGGER_KINDS", "Trigger", "WorkflowRef"]

TRIGGER_KINDS = ("event", "schedule", "probe", "manual")


@dataclass(frozen=True, kw_only=True)
class Trigger(Model):
    """What fires a rule. Kinds and their required params:

    - ``event``: ``type`` (non-empty event type string)
    - ``schedule``: ``cron``; optional ``tz``
    - ``probe``: ``actor``, ``command``, ``schedule`` (cron), ``mode`` (change|condition);
      optional ``args``
    - ``manual``: no params
    """

    kind: str = doc("Trigger kind: event, schedule, probe or manual")
    params: dict[str, Any] = doc("Kind-specific parameters", default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class WorkflowRef(Model):
    """A reference from a rule to a reusable workflow, with input mappings."""

    id: str = doc("Workflow id")
    version: int | None = doc("Pinned workflow version; null means latest", default=None)
    inputs: dict[str, str | dict[str, Any]] = doc(
        "Workflow input name -> reference string (e.g. trigger.data.number), "
        '{"$ref": path}, {"$literal": value} or {"$var": name} (a shared variable\'s '
        "current value at firing time)",
        default_factory=dict,
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
    on_failure: Action | None = doc(
        "Side effect run exactly once when the run ends failed (never on success, "
        "supersession or cancellation); same shape and routing as action, and its params may "
        "also read run.error.step, run.error.code and run.error.message. Null means none",
        default=None,
    )
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
    concurrency_key: str | None = doc(
        "Concurrency key template, e.g. pr-fixer:{trigger.data.repository}#{trigger.data.number} "
        "(only {trigger.<path>} placeholders). The resolved key is global: every rule whose "
        "template resolves to the same string shares one active run and one attempt budget, so "
        "use a distinct template (a namespace prefix) to isolate a rule. An active run or "
        "pending firing with the same key deduplicates new firings; the newest one fires once "
        "the run ends. A key that does not resolve skips the firing "
        "(concurrency_key_unresolved). None preserves independent firings.",
        default=None,
    )
    max_attempts: int | None = doc(
        "Attempt budget (>= 1): after ``max_attempts`` admitted runs for a concurrency key "
        "(by any rule sharing it; the smallest max_attempts among those rules applies), "
        "further firings are skipped until a non-self push (github.pr.synchronize with "
        "self_authored false) or green checks (github.pr.checks_settled with conclusion "
        "success) resets the counter. ``None`` means no budget.",
        default=None,
    )
    schema_version: str = doc("Document schema version (MAJOR.MINOR)", default=SCHEMA_VERSION)
