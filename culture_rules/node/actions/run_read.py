"""Built-in ``run.read``: outputs of a step of an earlier run, so a later rule can build on it.

A generic step: a rule that continues from another workflow (``rules.run.succeeded``) gets
only that workflow's declared outputs in its trigger; ``run.read`` reaches the rest of what
the run produced - any step's outputs - without the earlier workflow having to change.

Which run: with no ``run_id`` it reads the run that started this one, verified from the
store (:func:`culture_rules.actors.lineage.upstream`: started by its rule firing on a genuine
run event, and that run succeeded), so a pasted trigger cannot point it elsewhere. With a
``run_id`` (input or config), it reads that run, which must have succeeded: the way to run a
continuation by hand for a run that already happened.

Config: ``step`` (the step id in the earlier run's workflow; required) and ``fields`` (the
output names to return; required, at least one). Outputs: each named field (``null`` when
the step did not output it) and ``run_id`` (the run read). Final failures: ``bad_params``,
``chain_unverified`` (no run id and no verified upstream run), ``run_not_found``,
``run_not_succeeded`` and ``step_not_found``. It reads the store and writes nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors.lineage import LineageError, upstream
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.runs import RUNS_COLLECTION

__all__ = ["RUN_READ_BUILTIN", "RunReadPort"]

RUN_READ_BUILTIN = "run.read"
_MAX_FIELDS = 20


def _fail(code: str, message: str) -> InvocationResult:
    return InvocationResult.failed(f"{code}: {message}", retryable=False)


def _fields(value: Any) -> list[str] | None:
    if isinstance(value, str):
        value = [value]
    ok = isinstance(value, (list, tuple)) and 0 < len(value) <= _MAX_FIELDS
    return list(value) if ok and all(isinstance(v, str) and v for v in value) else None


def _step_outputs(run: Mapping[str, Any], step: str) -> Mapping[str, Any] | None:
    """The outputs of top-level step ``step`` (not a loop body's iteration)."""
    for state in run.get("steps") or ():
        if state.get("def") == step and not state.get("loop"):
            outputs = state.get("outputs")
            return outputs if isinstance(outputs, Mapping) else {}
    return None


class RunReadPort:
    """Built-in ``run.read`` (module doc)."""

    supports_idempotency_key = True

    def __init__(self, store: Any) -> None:
        self._store = store

    def invoke(
        self,
        input: Mapping[str, Any],  # noqa: A002 - the ActorPort signature
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config if isinstance(context.config, Mapping) else {}
        step, fields = config.get("step"), _fields(config.get("fields"))
        if not isinstance(step, str) or not step or fields is None:
            return _fail("bad_params", "config.step and config.fields (1 to 20 names) are required")
        run = self._earlier(input.get("run_id") or config.get("run_id"), context.run_id)
        if isinstance(run, InvocationResult):
            return run
        outputs = _step_outputs(run, step)
        if outputs is None:
            return _fail("step_not_found", f"run {run.get('id')!r} has no step {step!r}")
        return InvocationResult.completed(
            {**{name: outputs.get(name) for name in fields}, "run_id": run.get("id")}
        )

    def _earlier(self, run_id: Any, own_id: str) -> Mapping[str, Any] | InvocationResult:
        if run_id is not None:
            if not isinstance(run_id, str) or not run_id:
                return _fail("bad_params", "run_id must be a run id")
            run = self._store.get(RUNS_COLLECTION, run_id)
            if not run:
                return _fail("run_not_found", f"no run {run_id!r}")
            if run.get("status") != "succeeded":
                return _fail("run_not_succeeded", f"run {run_id!r} is {run.get('status')!r}")
            return run
        own = self._store.get(RUNS_COLLECTION, own_id)
        try:
            return upstream(self._store, own or {})
        except LineageError as exc:
            return _fail("chain_unverified", str(exc))
