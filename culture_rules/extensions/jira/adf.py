"""Atlassian Document Format helpers: ADF to plain text and plain text to ADF.

Pure functions, stdlib only, no I/O. ``to_adf`` of one line is byte-identical to the
single-paragraph document the Jira client has always sent.
"""

from __future__ import annotations

import re
from typing import Any

_ORDERED = re.compile(r"^\d+\. (.*)$")
_BULLET = re.compile(r"^- (.*)$")


def _text_node(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _paragraph(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "content": [_text_node(text)]}


def _list_item(kind: str, text: str) -> tuple[str, str]:
    for name, pattern in (("orderedList", _ORDERED), ("bulletList", _BULLET)):
        match = pattern.match(text)
        if match:
            return name, match.group(1)
    return kind, text


def to_adf(text: str) -> dict[str, Any]:
    """Plain multi-line text as an ADF document.

    One line is one paragraph (exactly the legacy document). Several lines give one
    paragraph each; consecutive ``1. `` lines become one ``orderedList`` and consecutive
    ``- `` lines one ``bulletList``. Blank lines separate and are dropped.
    """
    if "\n" not in text and "\r" not in text:
        return {"type": "doc", "version": 1, "content": [_paragraph(text)]}
    content: list[dict[str, Any]] = []
    open_list: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line.strip():
            open_list = None
            continue
        kind, body = _list_item("paragraph", line)
        if kind == "paragraph":
            open_list = None
            content.append(_paragraph(line))
            continue
        if open_list is None or open_list["type"] != kind:
            open_list = {"type": kind, "content": []}
            content.append(open_list)
        open_list["content"].append({"type": "listItem", "content": [_paragraph(body)]})
    return {"type": "doc", "version": 1, "content": content}


def _inline(node: dict[str, Any]) -> str:
    kind = node.get("type")
    if kind == "text":
        return str(node.get("text", ""))
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji", "inlineCard"):
        attrs = node.get("attrs") or {}
        return str(attrs.get("text") or attrs.get("shortName") or attrs.get("url") or "")
    return _children(node, "")


def _children(node: dict[str, Any], sep: str) -> str:
    return sep.join(_inline(c) for c in node.get("content") or [] if isinstance(c, dict))


def _block(node: dict[str, Any]) -> str:
    kind = node.get("type")
    if kind in ("bulletList", "orderedList"):
        start = int((node.get("attrs") or {}).get("order", 1) or 1)
        lines = []
        for index, item in enumerate(node.get("content") or []):
            marker = f"{start + index}. " if kind == "orderedList" else "- "
            lines.append(marker + _blocks(item.get("content") or [], "\n"))
        return "\n".join(lines)
    if kind in ("paragraph", "heading", "codeBlock", "text", "hardBreak", "mention"):
        return _inline(node) if kind in ("text", "hardBreak", "mention") else _children(node, "")
    if any(isinstance(c, dict) and c.get("type") in _BLOCKS for c in node.get("content") or []):
        return _blocks(node.get("content") or [], "\n")
    return _children(node, "")


_BLOCKS = frozenset(
    {"paragraph", "heading", "codeBlock", "bulletList", "orderedList", "blockquote", "panel"}
)


def _blocks(nodes: list[Any], sep: str) -> str:
    return sep.join(_block(n) for n in nodes if isinstance(n, dict))


def flatten(adf: Any) -> str:
    """An ADF document (or node) as plain text, one block per line.

    Paragraphs and list items take a line each (``- `` and ``1. `` markers), hard breaks
    and code-block newlines stay newlines, mentions read as their text, and an unknown
    node contributes its text children. A string that is not ADF passes through as is.
    """
    if isinstance(adf, dict):
        if adf.get("type") == "doc":
            return _blocks(adf.get("content") or [], "\n")
        return _block(adf)
    if isinstance(adf, str):
        return adf
    return ""
