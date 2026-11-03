"""Built-in ``text.validate``: check a text against explicit rules, answer with a verdict.

A refusal is a verdict, not a failure: the step completes with ``ok`` false and ``reasons``
saying why, so a later step branches with ``when`` (e.g. fall back to something else). Nothing
is dropped silently and the text is returned unchanged either way.

Inputs (each may instead sit in ``config``; the input wins). Every rule is explicit and none is
on by default; with no rule given the text is ok.

- ``text`` (required): a string, else the step fails ``bad_params`` (final, not retryable).
- ``allowed``: a list of objects, with ``allowed_field``: the key whose values are the only
  URLs a markdown link (``[label](url)``) in the text may point to. A value is usable only when
  it is a string without whitespace whose scheme is in ``link_schemes`` and that has a host
  (``link_schemes`` unset: ``http`` and ``https``). Both given or neither, else ``bad_params``.
- ``require_links`` (bool): at least one link.
- ``line_shape``: ``"bullets"`` (every non-blank line is a bullet: ``-``, ``*``, a bullet dot or
  ``1.``) or ``"linked_bullets"`` (a bullet that also holds a link).
- ``link_schemes``: a list of schemes, e.g. ``["http", "https"]``.

Outputs: ``ok`` (bool), ``reasons`` (short codes, in rule order, empty when ok), ``text``
(unchanged) and ``links`` (every link URL in order). Reason codes: ``no_links``,
``link_not_allowed``, ``not_bullets``, ``bullet_without_link``. Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = ["TEXT_VALIDATE_BUILTIN", "TextValidatePort", "text_validate"]

TEXT_VALIDATE_BUILTIN = "text.validate"
_BAD_PARAMS = "bad_params"
# Link text may hold one balanced level of brackets, so "[[1]](url)" shows "[1]" as the link.
_LINK_RE = re.compile(
    r"\[((?:[^\[\]\n]|\[[^\[\]\n]{0,300}\]){1,300})\]\(((?:[^()\s]|\([^()\s]*\)){1,2048})\)"
)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d{1,3}[.)])\s+")
_SHAPES = ("bullets", "linked_bullets")
_DEFAULT_SCHEMES = ("http", "https")


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    value = input.get(key)
    return config.get(key) if value is None else value


def _schemes(value: Any) -> tuple[str, ...]:
    if value is None:
        return _DEFAULT_SCHEMES
    if not isinstance(value, (list, tuple)) or not value:
        raise _Refused(_BAD_PARAMS, "link_schemes must be a non-empty list of schemes")
    if not all(isinstance(s, str) and s for s in value):
        raise _Refused(_BAD_PARAMS, "link_schemes must hold non-empty strings")
    return tuple(s.lower() for s in value)


def _usable(url: Any, schemes: tuple[str, ...]) -> bool:
    if not isinstance(url, str) or not url or re.search(r"\s", url):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    return bool(host) and parts.scheme in schemes


def _allowed_set(allowed: Any, field: Any, schemes: tuple[str, ...]) -> set[str] | None:
    if allowed is None and field is None:
        return None
    if not isinstance(allowed, (list, tuple)) or not isinstance(field, str) or not field:
        raise _Refused(_BAD_PARAMS, "allowed (a list of objects) needs allowed_field (a key)")
    values = (item.get(field) for item in allowed if isinstance(item, Mapping))
    return {v for v in values if _usable(v, schemes)}


def _lines(text: str) -> list[tuple[bool, str]]:
    """(is_bullet, content) for every non-blank line."""
    out = []
    for line in text.splitlines():
        if line.strip():
            match = _BULLET_RE.match(line)
            out.append((True, line[match.end() :].strip()) if match else (False, line.strip()))
    return out


def text_validate(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """The verdict (module doc), else :class:`_Refused` for bad parameters."""
    text = _param(input, config, "text")
    if not isinstance(text, str):
        raise _Refused(_BAD_PARAMS, "text must be a string")
    schemes = _schemes(_param(input, config, "link_schemes"))
    allowed = _allowed_set(
        _param(input, config, "allowed"), _param(input, config, "allowed_field"), schemes
    )
    shape = _param(input, config, "line_shape")
    if shape is not None and shape not in _SHAPES:
        raise _Refused(_BAD_PARAMS, f"line_shape must be one of {', '.join(_SHAPES)}")
    require = _param(input, config, "require_links")
    if require is not None and not isinstance(require, bool):
        raise _Refused(_BAD_PARAMS, "require_links must be a boolean")

    links = [m.group(2) for m in _LINK_RE.finditer(text)]
    reasons: list[str] = []
    if require and not links:
        reasons.append("no_links")
    if allowed is not None and any(url not in allowed for url in links):
        reasons.append("link_not_allowed")
    if shape:
        lines = _lines(text)
        if not all(bullet for bullet, _ in lines):
            reasons.append("not_bullets")
        elif shape == "linked_bullets" and not all(_LINK_RE.search(c) for _, c in lines):
            reasons.append("bullet_without_link")
    return {"ok": not reasons, "reasons": reasons, "text": text, "links": links}


class TextValidatePort:
    """Built-in ``text.validate`` (see the module docstring). Pure; touches nothing."""

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
            return InvocationResult.completed(text_validate(input, config))
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
