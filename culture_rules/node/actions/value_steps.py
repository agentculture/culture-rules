"""Generic value builtins: ``text.join``, ``time.add`` and ``date.local``.

They are pure steps (no store, no network); every format comes from step config or inputs, and
nothing is built in. Each input may instead sit in ``config``; the input wins. Final
(non-retryable) failure for all three: ``bad_params``.

``text.join`` joins a list of scalars into one text. Inputs: ``items`` - a list whose members are
strings, ints or floats (a number is written with ``str()``; booleans, null, lists and objects
are refused, never guessed at); ``separator`` - a string (required; ``""`` is allowed). An empty
list gives ``""``. Outputs: ``text`` and ``count``.

``time.add`` adds an exact elapsed duration to an instant. Inputs: ``instant`` - an ISO-8601
time with a UTC offset (a trailing ``Z`` is fine); ``seconds``, ``minutes``, ``hours``,
``days`` - numbers (ints or floats, negative allowed), at least one given, summed. A day is
exactly 24 hours of elapsed time (86400 s), the arithmetic is on the UTC timeline, so a day
added across a daylight-saving change lands 24 hours later, not at the same wall-clock time.
Output: ``instant``, the result in UTC (``+00:00``).

``date.local`` writes an instant as local time in an IANA zone. Inputs: ``instant`` (as above),
``tz`` - an IANA zone such as ``Asia/Jerusalem`` (needs the system tz database; its absence is
reported as such), ``format`` - a ``strftime`` format (``%Y-%m-%d`` for the date, ``%H%M`` for the
time of day). Outputs: ``text`` (the formatted local time) and ``local`` (the local time as
ISO-8601 with its offset). Daylight saving is applied by the zone rules: an instant is converted
by its own offset on that day.

Standard library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actions.text_steps import _BAD_PARAMS, _param, _Refused

__all__ = [
    "DATE_LOCAL_BUILTIN",
    "DateLocalPort",
    "TEXT_JOIN_BUILTIN",
    "TIME_ADD_BUILTIN",
    "TextJoinPort",
    "TimeAddPort",
    "date_local",
    "text_join",
    "time_add",
]

TEXT_JOIN_BUILTIN = "text.join"
TIME_ADD_BUILTIN = "time.add"
DATE_LOCAL_BUILTIN = "date.local"
_UNITS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}


def _bad(message: str) -> _Refused:
    return _Refused(_BAD_PARAMS, message)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def text_join(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """The joined text and its count (module doc), else :class:`_Refused`."""
    items = _param(input, config, "items")
    if not isinstance(items, list):
        raise _bad("items must be a list")
    separator = _param(input, config, "separator")
    if not isinstance(separator, str):
        raise _bad("separator must be a string")
    parts: list[str] = []
    for position, item in enumerate(items, start=1):
        if not (isinstance(item, str) or _is_number(item)):
            raise _bad(f"item {position} is not a string or a number")
        parts.append(str(item))
    return {"text": separator.join(parts), "count": len(parts)}


def _instant(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise _bad("instant must be an ISO-8601 time")
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        raise _bad("instant is not an ISO-8601 time") from None
    if moment.tzinfo is None:
        raise _bad("instant needs a UTC offset (for example a trailing Z)")
    return moment


def time_add(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """The instant moved by the summed duration, in UTC (module doc), else :class:`_Refused`."""
    moment = _instant(_param(input, config, "instant"))
    amounts: list[tuple[Any, int]] = []
    for unit, factor in _UNITS.items():
        amount = _param(input, config, unit)
        if amount is None:
            continue
        if not _is_number(amount):
            raise _bad(f"{unit} must be a number")
        amounts.append((amount, factor))
    if not amounts:
        raise _bad("give at least one of seconds, minutes, hours, days")
    try:  # a huge int overflows the float sum, a huge sum the timedelta or the datetime
        total = sum((float(amount) * factor for amount, factor in amounts), 0.0)
        result = moment.astimezone(UTC) + timedelta(seconds=total)
    except (OverflowError, ValueError):
        raise _bad("the result is outside the supported time range") from None
    return {"instant": result.isoformat()}


def _zone(name: Any) -> ZoneInfo:
    if not isinstance(name, str) or not name:
        raise _bad("tz is required, an IANA zone such as Asia/Jerusalem")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise _bad(f"tz {name!r} is unknown, or the system tz database is missing") from None
    except ValueError:
        raise _bad(f"tz {name!r} is not a valid IANA zone name") from None


def date_local(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """The instant in the zone, formatted (module doc), else :class:`_Refused`."""
    moment = _instant(_param(input, config, "instant"))
    zone = _zone(_param(input, config, "tz"))
    fmt = _param(input, config, "format")
    if not isinstance(fmt, str) or not fmt:
        raise _bad("format must be a non-empty strftime string")
    local = moment.astimezone(zone)
    try:
        text = local.strftime(fmt)
    except ValueError:
        raise _bad("format is not a valid strftime string") from None
    return {"text": text, "local": local.isoformat()}


class _PurePort:
    """A pure builtin: ``compute`` over the input and the step config."""

    supports_idempotency_key = True
    _compute: Any = None

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config if isinstance(context.config, Mapping) else {}
        try:
            return InvocationResult.completed(type(self)._compute(input, config))
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)


class TextJoinPort(_PurePort):
    """Built-in ``text.join`` (see the module docstring)."""

    _compute = staticmethod(text_join)


class TimeAddPort(_PurePort):
    """Built-in ``time.add`` (see the module docstring)."""

    _compute = staticmethod(time_add)


class DateLocalPort(_PurePort):
    """Built-in ``date.local`` (see the module docstring)."""

    _compute = staticmethod(date_local)
