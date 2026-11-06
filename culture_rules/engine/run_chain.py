"""Run chains: which run started a run, verified from the store. Standard-library only.

A rule can continue from another workflow: it fires on a ``rules.run.succeeded`` (or
``rules.run.failed``) event and its run starts. A step that acts on the earlier run must not
trust a wired input or an event field for which run that is; :func:`upstream` walks the link
**from the store**:

* ``run`` was started by its rule firing on that event (its id is the deterministic
  ``run_id_for(rule, event)``: a run started by hand with a pasted trigger is not);
* the trigger snapshot on ``run`` equals the event the upstream run's immutable completion
  record emitted (:func:`~culture_rules.node.run_events.verify_run_event`): no forged or
  edited event;
* that upstream run exists and ``succeeded`` (or ``failed``, when asked for).

Anything else raises :class:`LineageError` with the code :data:`CHAIN_UNVERIFIED`.
:func:`step_state` reads one step's state from a run document.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "CHAIN_UNVERIFIED",
    "LineageError",
    "RUN_FAILED",
    "RUN_SUCCEEDED",
    "step_state",
    "upstream",
]

CHAIN_UNVERIFIED = "chain_unverified"
RUN_SUCCEEDED = "rules.run.succeeded"
RUN_FAILED = "rules.run.failed"
_RUNS = "runs"  # culture_rules.engine.runs.RUNS_COLLECTION (not imported: no engine cycle)


class LineageError(Exception):
    """The chain cannot be verified: ``code`` (with ``detail``) is the refusal."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def step_state(run: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    """The state of step ``key`` in ``run`` (``None`` when absent)."""
    return next((s for s in run.get("steps", ()) if s.get("key") == key), None)


def upstream(store: Any, run: Mapping[str, Any], *, failed: bool = False) -> Mapping[str, Any]:
    """The verified run whose ``rules.run.succeeded`` event started ``run`` (module doc).

    ``failed``: the run whose verified ``rules.run.failed`` event started it instead, and
    that run failed."""
    # node layer, lazily
    from culture_rules.node.firing import run_id_for  # noqa: PLC0415
    from culture_rules.node.run_events import verify_run_event  # noqa: PLC0415

    trigger = run.get("trigger")
    want = RUN_FAILED if failed else RUN_SUCCEEDED
    if not isinstance(trigger, Mapping) or trigger.get("type") != want:
        raise LineageError(CHAIN_UNVERIFIED, "the run was not started by a run succeeding")
    rule_id, event_id = run.get("rule_id"), trigger.get("id")
    if not (isinstance(rule_id, str) and isinstance(event_id, str) and rule_id and event_id):
        raise LineageError(CHAIN_UNVERIFIED, "the run names no rule or trigger event")
    if run.get("id") != run_id_for(rule_id, event_id):
        raise LineageError(CHAIN_UNVERIFIED, "the run was not started by its rule's firing")
    why = verify_run_event(store, trigger)
    if why is not None:
        raise LineageError(CHAIN_UNVERIFIED, f"its trigger is not a genuine run event: {why}")
    data = trigger.get("data") if isinstance(trigger.get("data"), Mapping) else {}
    up = store.get(_RUNS, data.get("run_id")) if isinstance(data.get("run_id"), str) else None
    if not up or up.get("status") != ("failed" if failed else "succeeded"):
        raise LineageError(CHAIN_UNVERIFIED, "the upstream run did not succeed")
    return up
