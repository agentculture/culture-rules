"""Atlassian Document Format helpers: ADF to plain text and plain text to ADF.

Pure functions, stdlib only, no I/O. ``to_adf`` of one line is byte-identical to the
single-paragraph document the Jira client has always sent.
"""

from __future__ import annotations

import re
from typing import Any

_ORDERED = re.compile(r"^\d+\. (.*)$")
_BULLET = re.compile(r"^- (.*)$")
#: A longer start is not kept (and never parsed: ``int`` refuses very long digit strings).
_MAX_ORDER_DIGITS = 9


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
    paragraph each; consecutive ``N. `` lines become one ``orderedList`` (``attrs.order`` is
    its first number when that is not 1) and consecutive ``- `` lines one ``bulletList``.
    Blank lines separate and are dropped; a text of blank lines only is one empty paragraph,
    never an empty document.
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
            open_list = _open_list(kind, line)
            content.append(open_list)
        open_list["content"].append({"type": "listItem", "content": [_paragraph(body)]})
    return {"type": "doc", "version": 1, "content": content or [_paragraph("")]}


def _open_list(kind: str, first_line: str) -> dict[str, Any]:
    """A new list node; an ordered list starting above 1 keeps its start in ``attrs``."""
    node: dict[str, Any] = {"type": kind, "content": []}
    digits = first_line.split(".", 1)[0]
    if kind == "orderedList" and len(digits) <= _MAX_ORDER_DIGITS and int(digits) > 1:
        node["attrs"] = {"order": int(digits)}
    return node


def _attrs(node: dict[str, Any]) -> dict[str, Any]:
    """A node's ``attrs`` when it is a dict, else an empty one (external data)."""
    attrs = node.get("attrs")
    return attrs if isinstance(attrs, dict) else {}


def _content(node: dict[str, Any]) -> list[Any]:
    """A node's ``content`` when it is a list, else an empty one (external data)."""
    content = node.get("content")
    return content if isinstance(content, list) else []


def _inline(node: dict[str, Any]) -> str:
    kind = node.get("type")
    if kind == "text":
        return str(node.get("text", ""))
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji", "inlineCard"):
        attrs = _attrs(node)
        return str(attrs.get("text") or attrs.get("shortName") or attrs.get("url") or "")
    return _children(node, "")


def _children(node: dict[str, Any], sep: str) -> str:
    return sep.join(_inline(c) for c in _content(node) if isinstance(c, dict))


def _start(order: Any) -> int:
    """An ordered list's first number; anything but an int >= 1 reads as 1."""
    if isinstance(order, int) and not isinstance(order, bool) and order >= 1:
        return order
    return 1


def _list(node: dict[str, Any], kind: str) -> str:
    """A bullet or ordered list, one line per item that is a dict (others are skipped)."""
    start = _start(_attrs(node).get("order"))
    items = [item for item in _content(node) if isinstance(item, dict)]
    lines = []
    for index, item in enumerate(items):
        marker = f"{start + index}. " if kind == "orderedList" else "- "
        lines.append(marker + _blocks(_content(item), "\n"))
    return "\n".join(lines)


def _block(node: dict[str, Any]) -> str:
    kind = node.get("type")
    if kind in ("bulletList", "orderedList"):
        return _list(node, kind)
    if kind in ("text", "hardBreak", "mention"):
        return _inline(node)
    if kind in ("paragraph", "heading", "codeBlock"):
        return _children(node, "")
    content = _content(node)
    if any(isinstance(c, dict) and c.get("type") in _BLOCKS for c in content):
        return _blocks(content, "\n")
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
            return _blocks(_content(adf), "\n")
        return _block(adf)
    if isinstance(adf, str):
        return adf
    return ""
