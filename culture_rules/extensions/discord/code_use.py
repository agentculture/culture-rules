"""Built-in ``button.code.use``: use up the clicked button's one-time code.

A workflow whose run was started by a ``discord.button.clicked`` event (a click on a bot
message's button) places this step first, before any step that acts on the click, so a
replayed or second click gets nothing. The code and the clicked ``custom_id`` come from the
run's stored click event - never from workflow inputs or params, which a clicker cannot
forge but also must not be trusted with.

Inputs (each may instead sit in ``config``; the input wins): ``value`` (optional) - a
mapping every key of which the code's stored value must hold with an equal value; omitted,
the step acts on the button's whole stored value (the ``data.value`` the gateway copied
onto the click). A button posted without ``value`` gets no code, so its clicks always fail
``code_not_found`` here.

The write is compare-and-set (:func:`~culture_rules.extensions.discord.buttons.use_code`):
the code must exist and be unexpired (``code_not_found``), belong to the clicked
``custom_id`` and hold the ``value`` (``code_mismatch``), and be unused - or already used
by this same run, which is that run's retry and passes. Otherwise ``code_used``. All three
are final, and so is ``not_started_by_click`` (the run is not its rule's firing on a
stored click event, e.g. it was started by hand through the API).

Outputs: ``value`` (the mapping the code was used for), ``custom_id`` (the clicked
button's) and ``used`` (always true on completion). A store that gives up under
contention fails ``store_unavailable``, retryable. Standard library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.extensions.discord.buttons import (
    CODE_NOT_FOUND,
    stored_click,
    use_code,
)
from culture_rules.store.port import StoreError

__all__ = ["BUTTON_CODE_USE_BUILTIN", "NOT_CLICK", "ButtonCodeUsePort"]

BUTTON_CODE_USE_BUILTIN = "button.code.use"
NOT_CLICK = "not_started_by_click"
"""The run was not started by a ``discord.button.clicked`` event through its rule."""


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    return input[key] if key in input else config.get(key)


class ButtonCodeUsePort:
    """Built-in ``button.code.use`` (see the module docstring)."""

    supports_idempotency_key = True  # use_code passes the same run again on a retry

    def __init__(self, store: Any) -> None:
        self._store = store

    def invoke(
        self,
        input: Mapping[str, Any],  # noqa: A002 - the ActorPort signature
        _idempotency_key: str,
        _deadline: datetime | None,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config if isinstance(context.config, Mapping) else {}
        declared = _param(input, config, "value")
        if declared is not None and not isinstance(declared, Mapping):
            return InvocationResult.failed("bad_params: value must be a mapping", retryable=False)
        try:
            run = self._store.get(RUNS_COLLECTION, context.run_id)
        except StoreError as exc:
            return InvocationResult.failed(
                f"store_unavailable: {type(exc).__name__}", retryable=True
            )
        envelope = stored_click(self._store, run or {})
        if envelope is None:
            return InvocationResult.failed(NOT_CLICK, retryable=False)
        data = envelope.get("data")
        data = data if isinstance(data, Mapping) else {}
        code = data.get("code")
        if not isinstance(code, str) or not code:
            return InvocationResult.failed(CODE_NOT_FOUND, retryable=False)
        act_on = declared if declared is not None else data.get("value")
        if not isinstance(act_on, Mapping):
            return InvocationResult.failed("bad_params: value must be a mapping", retryable=False)
        refusal = use_code(
            self._store,
            code,
            custom_id=data.get("custom_id"),
            value=act_on,
            run_id=context.run_id,
            now=datetime.now(UTC),
        )
        if refusal is not None:
            return InvocationResult.failed(refusal, retryable=False)
        return InvocationResult.completed(
            {"value": dict(act_on), "custom_id": data.get("custom_id"), "used": True}
        )
