"""Built-in ``text.replace_refs``: numbered references in a text swapped for a template.

A text cites its sources as ``[n]``; the n-th item of a list says what that citation is. This
step rewrites each in-range reference with a configured output template filled from item n.
Nothing about the reference syntax or the output format is built in: both come from config.

Inputs (each may instead sit in ``config``; the input wins): ``text`` - the string to rewrite;
``items`` - a list of objects, numbered from 1; ``pattern`` (required, no default) - a regular
expression with exactly one group holding the number, e.g. ``\\[(\\d+)\\]``, which must pass
``regex_safety.unsafe_reason``; ``template`` (required, no default) - the replacement, where
``{n}`` is the number and ``{field}`` a top-level scalar field of item n (a str, int, float or
bool; nothing else is looked up, no attributes, no format specs, no evaluation).

Outputs: ``text`` (rewritten), ``replaced`` (the count of replacements) and ``missing_refs``
(sorted unique numbers whose reference was left as it was). A reference is left unchanged when
its number is outside 1..len(items), or when the template names a field item n lacks (or holds
as a list, object or null): that is a data gap, not a config error, so the step goes on and
lists the number. A group that is not all digits is left alone and not listed. Final
(non-retryable) failure: ``bad_params`` (missing or invalid pattern, template, text or items).
Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.model.regex_safety import unsafe_reason

__all__ = ["TEXT_REPLACE_REFS_BUILTIN", "TextReplaceRefsPort", "replace_refs"]

TEXT_REPLACE_REFS_BUILTIN = "text.replace_refs"
_BAD_PARAMS = "bad_params"
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_DIGITS = re.compile(r"[0-9]+")


class _Refused(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(f"{_BAD_PARAMS}: {message}")


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    value = input.get(key)
    return config.get(key) if value is None else value


def _pattern(value: Any) -> re.Pattern[str]:
    if not isinstance(value, str) or not value:
        raise _Refused("pattern is required (a regex with one numeric group)")
    try:
        compiled = re.compile(value)
    except re.error as exc:
        raise _Refused(f"pattern does not compile: {exc}") from exc
    if compiled.groups != 1:
        raise _Refused("pattern needs exactly one group, the reference number")
    reason = unsafe_reason(value)
    if reason:
        raise _Refused(f"pattern is unsafe: {reason}")
    return compiled


def _template(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise _Refused("template is required (the output for one reference)")
    return value


def _items(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(i, Mapping) for i in value):
        raise _Refused("items must be a list of objects")
    return value


def _fill(template: str, number: int, item: Mapping[str, Any]) -> str | None:
    """The template for item ``number``, or ``None`` if a named field is not a scalar there."""
    missing = False

    def one(match: re.Match[str]) -> str:
        nonlocal missing
        name = match.group(1)
        value = number if name == "n" else item.get(name)
        if isinstance(value, (str, int, float)):  # bool is an int
            return str(value)
        missing = True
        return ""

    out = _PLACEHOLDER.sub(one, template)
    return None if missing else out


def replace_refs(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """Rewrite the references (module doc), else raise :class:`_Refused`."""
    pattern = _pattern(_param(input, config, "pattern"))
    template = _template(_param(input, config, "template"))
    text = _param(input, config, "text")
    if not isinstance(text, str):
        raise _Refused("text must be a string")
    items = _items(_param(input, config, "items"))
    missing: set[int] = set()
    replaced = 0

    def swap(match: re.Match[str]) -> str:
        nonlocal replaced
        digits = match.group(1)
        if digits is None or not _DIGITS.fullmatch(digits):
            return match.group(0)
        number = int(digits)
        filled = _fill(template, number, items[number - 1]) if 1 <= number <= len(items) else None
        if filled is None:
            missing.add(number)
            return match.group(0)
        replaced += 1
        return filled

    out = pattern.sub(swap, text)
    return {"text": out, "replaced": replaced, "missing_refs": sorted(missing)}


class TextReplaceRefsPort:
    """Built-in ``text.replace_refs`` (see the module docstring). Pure, no I/O."""

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
            return InvocationResult.completed(replace_refs(input, config))
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
