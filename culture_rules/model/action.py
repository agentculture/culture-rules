"""Action: the concrete side effect a rule ends in (required on every rule)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from culture_rules.model.common import Model, RetryPolicy, doc

__all__ = ["Action"]


@dataclass(frozen=True, kw_only=True)
class Action(Model):
    """A terminal operation: comment on a PR, send a message, call a service, ask a human...

    Deliberately has no actor slot: an action that invokes an actor capability
    names it inside ``params`` — actors are referenced, never a rule stage.
    """

    kind: str = doc("Action type, e.g. github.comment, mesh.message, http.call")
    name: str = doc("Display name", default="")
    params: dict[str, Any] = doc(
        "Kind-specific parameters; values may hold references such as workflow.outputs.x",
        default_factory=dict,
    )
    timeout_s: float | None = doc("Timeout in seconds (> 0)", default=None)
    retry: RetryPolicy | None = doc("Retry policy", default=None)
    idempotent: bool = doc("Safe to retry without an idempotency key", default=False)
    only_at_chain_end: bool = doc(
        "Run only where the run's chain ends (d21): skipped (chain_continues) when a live rule "
        "would continue the run - fire on the rules.run.* event it is about to emit, on the "
        "same concurrency key. One comment per chain of fix, review and publish runs",
        default=False,
    )
