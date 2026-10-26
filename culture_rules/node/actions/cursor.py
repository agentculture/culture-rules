"""Built-in ``cursor.advance``: move a stored cursor forward with compare-and-set.

A cursor is one document in :data:`SWEEP_CURSORS_COLLECTION`: ``{"id": <key>, "value":
<str>}`` (plus the store's envelope). A read step such as ``discord.sweep`` reads it and
returns what it read (``cursor``) and where the run got to (``next_cursor``); this step,
placed *after* the steps that act on what was read (the pushes), saves the new value. A run
whose pushes fail never reaches it, so the cursor stays put and the next run reads the same
items again: nothing is skipped. The two-step pattern in a workflow::

    sweep:   discord.sweep   -> messages, cursor, next_cursor, cursor_key
    push:    a loop over messages (library.push, ...)
    advance: cursor.advance  key=cursor_key, expected=cursor, value=next_cursor

Inputs (each may instead sit in ``config``; the input wins): ``key`` (required, a non-empty
string), ``expected`` (the value the read step saw; null means no cursor was stored) and
``value`` (the new value; a string or null).

The write is a compare-and-set (:meth:`StoreOps.update_if`): it lands only while the stored
value still equals ``expected``. Outcomes:

- stored value is ``expected`` and ``value`` differs: saved, ``advanced: true``;
- stored value is already ``value`` (nothing new, or this step retried after it landed):
  nothing is written, ``advanced: false``;
- anything else: ``cursor_moved`` (final) - an overlapping run moved it, and saving would
  rewind it or skip what that run read;
- ``value`` older than ``expected`` when both are numeric ids (Discord snowflakes, compared
  as integers), or null when ``expected`` is set: ``cursor_backwards`` (final).

Outputs: ``key``, ``value`` (the stored value now), ``previous`` (``expected``) and
``advanced``. Bad inputs fail ``bad_params`` (final); a store that gives up under contention
fails retryably. Standard library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.store.port import StoreError

__all__ = [
    "CURSOR_ADVANCE_BUILTIN",
    "SWEEP_CURSORS_COLLECTION",
    "CursorAdvancePort",
    "read_cursor",
]

CURSOR_ADVANCE_BUILTIN = "cursor.advance"
#: Stored cursors of sweeps (``_cursors`` is the store's own change-feed cursors).
SWEEP_CURSORS_COLLECTION = "sweep_cursors"
_MAX_KEY = 200
_MAX_VALUE = 200


class _Refused(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.retryable = retryable


def read_cursor(store: Any, key: str) -> Any:
    """The stored value of cursor ``key`` (``None`` when there is none)."""
    doc = store.get(SWEEP_CURSORS_COLLECTION, key)
    return doc.get("value") if doc else None


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    # present in the input wins even when null: a null ``expected`` (no cursor yet) wired
    # from the sweep must not fall back to a config value (unlike discord.sweep's options)
    return input[key] if key in input else config.get(key)


def _value(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > _MAX_VALUE:
        raise _Refused("bad_params", f"{name} must be a non-empty string or null")
    return value


def _backwards(expected: str | None, value: str | None) -> bool:
    if expected is None:
        return False
    if value is None:
        return True
    if expected.isdigit() and value.isdigit():
        return int(value) < int(expected)
    return False


def advance(store: Any, key: Any, expected: Any, value: Any) -> dict[str, Any]:
    """Compare-and-set cursor ``key`` from ``expected`` to ``value`` (module doc)."""
    if not isinstance(key, str) or not key or len(key) > _MAX_KEY:
        raise _Refused("bad_params", "key must be a non-empty string")
    expected, value = _value(expected, "expected"), _value(value, "value")
    out = {"key": key, "value": value, "previous": expected, "advanced": False}
    if value == expected:
        return out  # nothing new: save nothing
    if _backwards(expected, value):
        raise _Refused("cursor_backwards", f"{key}: {value!r} is older than {expected!r}")
    try:
        result = store.update_if(
            SWEEP_CURSORS_COLLECTION,
            key,
            {"value": expected},
            {"value": value},
            upsert=expected is None,
        )
    except StoreError as exc:
        raise _Refused("cursor_contended", f"{key}: {type(exc).__name__}", retryable=True)
    current = (result.document or {}).get("value")
    if result.won:
        return {**out, "advanced": True}
    if current == value:
        return out  # this step already landed (a retry)
    if current is None:  # we expected a cursor, and there is none at all
        raise _Refused("cursor_moved", f"{key}: expected {expected!r} but the cursor is gone")
    raise _Refused("cursor_moved", f"{key}: expected {expected!r} but another run left {current!r}")


class CursorAdvancePort:
    """Built-in ``cursor.advance`` (see the module docstring)."""

    supports_idempotency_key = True  # a retry after the write landed is a no-op

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
        try:
            out = advance(
                self._store,
                _param(input, config, "key"),
                _param(input, config, "expected"),
                _param(input, config, "value"),
            )
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=exc.retryable)
        return InvocationResult.completed(out)
