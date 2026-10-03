"""ActorPort: the one seam through which the run executor dispatches work (obligation o6).

Every adapter (agent, code runner, human ask, service call, ...) implements
:class:`ActorPort`. The executor (:mod:`culture_rules.engine.runs`) never talks to an
actor any other way. Standard-library only.

Contract
========

``invoke(input, idempotency_key, deadline, *, context)`` returns an
:class:`InvocationResult` whose ``outcome`` is one of

``completed``
    the work is done; ``output`` maps the step's output port names to values.
``accepted``
    long work was taken on; it completes later *via an event*: whoever observes the
    completion calls :meth:`culture_rules.engine.runs.Executor.deliver` with the same
    ``idempotency_key`` and a ``completed``/``failed`` result.
``failed``
    the work failed; ``retryable=False`` means retrying cannot help (bad input,
    permission denied) and the step fails at once instead of consuming its retry policy.
``blocked``
    the actor cannot take the work right now (at its concurrency cap, over budget,
    waiting on a dependency). The executor asks again later without consuming an
    attempt, bounded by the attempt's deadline.

Idempotency
-----------

``idempotency_key`` is :func:`culture_rules.engine.claims.idempotency_key` of
``(run_id, step key)``: identical for every attempt, retry, host and restart. Adapters
**must be idempotent on the key**: a second ``invoke`` with a key whose work already
happened returns that work's result (or ``accepted`` while it is still in progress) and
never repeats the side effect. This is what makes a lost acknowledgement safe: the
executor retries with the same key and the target deduplicates.

An adapter whose target cannot deduplicate sets ``supports_idempotency_key = False``.
The executor then never retries it blindly after a lost acknowledgement unless the work
is declared idempotent (``Action.idempotent``); it fails the step with ``unsafe_retry``.

Raising
-------

An exception from ``invoke`` means *no acknowledgement*: the executor cannot tell
whether the work happened, records the attempt as failed (``no_ack``) and retries per
policy with the same key. Adapters should honour ``deadline`` themselves; the executor
also times out ``accepted`` work whose deadline passes.

``context`` carries what an adapter may need to route or report: run and step ids, the
step kind (``"action"`` for a rule's terminal action), the host the step was placed on,
the step's ``config`` (or the action's kind/params) and the attempt number (1-based;
informational only - it never changes the key).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "ACCEPTED",
    "BLOCKED",
    "COMPLETED",
    "FAILED",
    "OUTCOMES",
    "ActorPort",
    "InvocationContext",
    "InvocationResult",
]

ACCEPTED = "accepted"
COMPLETED = "completed"
FAILED = "failed"
BLOCKED = "blocked"
OUTCOMES: tuple[str, ...] = (ACCEPTED, COMPLETED, FAILED, BLOCKED)


@dataclass(frozen=True)
class InvocationContext:
    """Routing and reporting facts handed to an adapter alongside the input."""

    run_id: str
    step_id: str
    kind: str
    host: str
    attempt: int = 1
    actor: str | None = None
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InvocationResult:
    """What an actor said about one invocation (or later, via an event)."""

    outcome: str
    output: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    retryable: bool = True

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}, got {self.outcome!r}")
        if not isinstance(self.output, Mapping):
            raise ValueError("output must be a mapping of port name -> value")

    @classmethod
    def completed(cls, output: Mapping[str, Any] | None = None) -> InvocationResult:
        return cls(COMPLETED, dict(output or {}))

    @classmethod
    def accepted(cls) -> InvocationResult:
        return cls(ACCEPTED)

    @classmethod
    def failed(cls, error: str, *, retryable: bool = True) -> InvocationResult:
        return cls(FAILED, error=error, retryable=retryable)

    @classmethod
    def blocked(cls, reason: str) -> InvocationResult:
        return cls(BLOCKED, error=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "output": dict(self.output),
            "error": self.error,
            "retryable": self.retryable,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> InvocationResult:
        return cls(
            outcome=data["outcome"],
            output=dict(data.get("output") or {}),
            error=data.get("error"),
            retryable=bool(data.get("retryable", True)),
        )


@runtime_checkable
class ActorPort(Protocol):
    """An adapter that performs work for the executor (see the module docstring).

    Adapters may also define ``supports_idempotency_key: bool`` (default True when
    absent).
    """

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        """Perform (or accept) the work identified by ``idempotency_key``."""
