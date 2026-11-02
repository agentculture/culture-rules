"""Generic text builtins. This module holds ``text.number``.

``text.number`` joins a list of objects into one text, each item rendered through a template and
numbered from 1 in list order. The format comes only from step config; nothing is built in.

Inputs (each may instead sit in ``config``; the input wins): ``items`` - a list of objects;
``template`` - text with ``{n}`` (the 1-based position) and ``{name}`` (a top-level scalar field
of the item), ``{{`` and ``}}`` for literal braces; ``separator`` - the string between rendered
items (required; ``""`` is allowed). Anything else in braces (attribute or index access,
conversions, empty or numeric names) is refused. ``n`` is always the position, never an item
field.

Outputs: ``text`` and ``count``. Final (non-retryable) failure: ``bad_params`` (not a list, an
item that is not an object, a template naming a field the item lacks or holds as a list,
object or null, a malformed template). Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = ["TEXT_NUMBER_BUILTIN", "TextNumberPort", "text_number"]

TEXT_NUMBER_BUILTIN = "text.number"
_BAD_PARAMS = "bad_params"
_TOKEN = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}|[{}]|[^{}]+")


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    value = input.get(key)
    return config.get(key) if value is None else value


def _parse(template: Any) -> list[tuple[str, str]]:
    """Tokens as ``("lit", text)`` or ``("field", name)``; a stray brace is refused."""
    if not isinstance(template, str) or not template:
        raise _Refused(_BAD_PARAMS, "template must be a non-empty string")
    out: list[tuple[str, str]] = []
    for match in _TOKEN.finditer(template):
        text = match.group(0)
        if match.group(1):
            out.append(("field", match.group(1)))
        elif text in ("{{", "}}"):
            out.append(("lit", text[0]))
        elif text in ("{", "}"):
            raise _Refused(_BAD_PARAMS, "template has a placeholder that is not {n} or {name}")
        else:
            out.append(("lit", text))
    return out


def _scalar(item: Mapping[str, Any], name: str, position: int) -> str:
    value = item.get(name)
    if value is None or isinstance(value, (list, dict)):
        raise _Refused(_BAD_PARAMS, f"item {position} has no scalar field {name!r}")
    return str(value)


def text_number(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """The numbered text and its count (module doc), else :class:`_Refused`."""
    items = _param(input, config, "items")
    if not isinstance(items, list):
        raise _Refused(_BAD_PARAMS, "items must be a list")
    tokens = _parse(_param(input, config, "template"))
    separator = _param(input, config, "separator")
    if not isinstance(separator, str):
        raise _Refused(_BAD_PARAMS, "separator must be a string")
    lines: list[str] = []
    for position, item in enumerate(items, start=1):
        if not isinstance(item, Mapping):
            raise _Refused(_BAD_PARAMS, f"item {position} is not an object")
        lines.append(
            "".join(
                (
                    text
                    if kind == "lit"
                    else str(position) if text == "n" else _scalar(item, text, position)
                )
                for kind, text in tokens
            )
        )
    return {"text": separator.join(lines), "count": len(lines)}


class TextNumberPort:
    """Built-in ``text.number`` (see the module docstring). Pure; touches nothing."""

    supports_idempotency_key = True

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
            return InvocationResult.completed(text_number(input, config))
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
