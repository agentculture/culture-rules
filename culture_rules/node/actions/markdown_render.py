"""Built-in ``markdown.render``: generic fields to one Markdown document (discord-surfaces t5).

Inputs (each may instead sit in ``config``; the input wins): ``title`` (a non-empty string),
``text`` (a string, possibly empty), and the optional ``author``, ``source``, ``url`` and
``id`` (a string or a number).
Nothing here knows about any one app; a workflow maps an event's fields onto these.

Outputs: ``markdown`` (the document) and ``content_base64`` (its UTF-8 bytes, base64, ready
for ``library.push``). The body is ``# <title>``, the text (if any), then a bullet per
optional field that is set (Author, Source, URL, ID). The output depends on the inputs alone -
no render time - so the same inputs give the same bytes (a library dedupes on them). Final
failure:
``bad_params`` (title missing or empty, or text not a string). Standard library only.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = ["MARKDOWN_RENDER_BUILTIN", "MarkdownRenderPort", "markdown_render"]

MARKDOWN_RENDER_BUILTIN = "markdown.render"
_BAD_PARAMS = "bad_params"
_OPTIONAL = (("author", "Author"), ("source", "Source"), ("url", "URL"), ("id", "ID"))


class _Refused(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(f"{_BAD_PARAMS}: {message}")


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    value = input.get(key)
    return config.get(key) if value is None else value


def _required(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Refused(f"{name} must be a non-empty string")
    return value


def _optional(value: Any, name: str) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _Refused(f"{name} must be a string or a number")
    return str(value)


def markdown_render(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, str]:
    """The rendered document (module doc), else :class:`_Refused`."""
    title = _required(_param(input, config, "title"), "title").strip()
    text = _param(input, config, "text")
    if not isinstance(text, str):  # may be empty: an attachment-only message has no text
        raise _Refused("text must be a string")
    text = text.strip("\n")
    lines = [f"# {title}", *(["", text] if text else [])]
    facts = []
    for key, label in _OPTIONAL:
        value = _optional(_param(input, config, key), key)
        if value is not None:
            facts.append(f"- {label}: {value}")
    if facts:
        lines += ["", *facts]
    markdown = "\n".join(lines) + "\n"
    encoded = base64.b64encode(markdown.encode("utf-8")).decode("ascii")
    return {"markdown": markdown, "content_base64": encoded}


class MarkdownRenderPort:
    """Built-in ``markdown.render`` (see the module docstring). Pure; touches nothing."""

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
            return InvocationResult.completed(markdown_render(input, config))
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
