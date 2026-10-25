"""Built-in ``markdown.from_html``: HTML as Markdown, whole and in chunks of a size.

A generic step: it knows no app and no flow. It reads the common subset of HTML -
headings (``h1``-``h6``), paragraphs, lists, links, bold and italic - and writes Markdown:
a heading as a bold line, a list item as ``- ``, a link as ``[label](<url>)`` (the angle
brackets keep Discord and other chat apps from embedding a preview per link), bold as
``**x**`` and italic as ``_x_``. Text from the HTML has the Markdown characters escaped, so
``[Video] __init__`` stays text. ``script``, ``style`` and ``head`` are dropped.

Input: ``html`` (a string). Config (each may instead be an input):

* ``max_chars`` - each chunk's limit, 200 to 4000, default :data:`DEFAULT_MAX_CHARS`
  (Discord takes 2000 a message);
* ``drop_classes`` - elements with one of these classes are left out (e.g. a meta line);
* ``italic_classes`` - paragraphs with one of these classes are written in italics;
* ``item_paragraphs`` - keep only the first N paragraphs of each list item (unset: all).

Chunks break between blocks (a heading starts one); a block longer than a chunk breaks
between its lines, a line longer than a chunk at a space outside any link, and a heading
always opens the chunk that holds its block's first line. Outputs: ``markdown`` (the whole
document), ``chunks`` (in order) and ``count``. Final failure: ``bad_params``. Standard
library only; the same HTML and config always give the same output.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = ["DEFAULT_MAX_CHARS", "FROM_HTML_BUILTIN", "MarkdownFromHtmlPort", "html_to_markdown"]

FROM_HTML_BUILTIN = "markdown.from_html"
DEFAULT_MAX_CHARS = 1900
_MIN_CHARS, _MAX_CHARS = 200, 4000
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_HIDDEN = ("script", "style", "head")
_STRONG, _EM = ("strong", "b"), ("em", "i")
_MD = re.compile(r"([\\*_~`|>\[\]#])")
_LINK = re.compile(r"\[(?:\\.|[^\]\\])*\]\(<[^>]*>\)")


class _Refused(Exception):
    pass


def _md(text: str) -> str:
    """Markdown characters as literals."""
    return _MD.sub(r"\\\1", text)


def _href(url: str) -> str:
    """A link target that cannot leave its angle brackets."""
    return url.replace("<", "%3C").replace(">", "%3E").replace(" ", "%20")


class _Doc(HTMLParser):
    """Collects blocks of Markdown lines; a heading opens a block (module doc)."""

    def __init__(self, drop: frozenset[str], italic: frozenset[str], item_paragraphs: int | None):
        super().__init__(convert_charrefs=True)
        self.blocks: list[list[str]] = [[]]
        self._drop, self._italic, self._item_max = drop, italic, item_paragraphs
        self._where: str | None = None  # a heading tag, "p" or "li"
        self._text: list[str] = []
        self._p_italic = False
        self._item_paragraphs = 0
        self._dropping = 0  # depth inside dropped or hidden elements
        self._href: str | None = None
        self._label: list[str] = []
        self._mark: tuple[str, int] | None = None  # an open bold/italic: marker, start

    # -- helpers
    def _line(self, line: str) -> None:
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            self.blocks[-1].append(line)

    def _flush(self) -> None:
        """Text not inside a paragraph, item or heading becomes a line of its own."""
        if self._where is None and self._text:
            self._line("".join(self._text))
            self._text = []

    # -- parser hooks
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set((dict(attrs).get("class") or "").split())
        if self._dropping or tag in _HIDDEN or classes & self._drop:
            if tag not in ("br", "img", "hr", "meta", "link"):
                self._dropping += 1
            return
        if tag in _HEADINGS or tag in ("p", "li"):
            self._open(tag, classes)
        elif tag == "a" and self._href is None:
            self._href, self._label = dict(attrs).get("href") or "", []
        elif (tag in _STRONG or tag in _EM) and self._mark is None and self._href is None:
            self._mark = ("**" if tag in _STRONG else "_", len(self._text))
        elif tag == "br" and self._where is not None:
            self._text.append(" ")

    def _open(self, tag: str, classes: set[str]) -> None:
        if tag == "p" and self._where == "li":
            self._item_paragraphs += 1
            if self._item_max is not None and self._item_paragraphs > self._item_max:
                self._dropping += 1
            return
        self._flush()
        if tag in _HEADINGS:
            self.blocks.append([])
        self._where, self._text = tag, []
        self._p_italic = tag == "p" and bool(classes & self._italic)
        self._item_paragraphs = 0

    def handle_endtag(self, tag: str) -> None:
        if self._dropping:
            if tag not in ("br", "img", "hr", "meta", "link"):
                self._dropping -= 1
        elif tag == "a" and self._href is not None:
            self._end_link()
        elif self._mark is not None and (tag in _STRONG or tag in _EM):
            self._end_mark()
        elif tag == self._where:
            self._close(tag)

    def _end_link(self) -> None:
        label = "".join(self._label).strip()
        href, self._href = self._href or "", None
        self._text.append(f"[{label}](<{_href(href)}>)" if href else label)

    def _end_mark(self) -> None:
        (marker, start), self._mark = self._mark or ("", 0), None
        inner = "".join(self._text[start:]).strip()
        del self._text[start:]
        if inner:  # "** x **" would not render as bold
            self._text.append(f" {marker}{inner}{marker} ")

    def _close(self, tag: str) -> None:
        text = re.sub(r"\s+", " ", "".join(self._text)).strip()
        if tag in _HEADINGS:
            self._line(f"**{text}**" if text else "")
        elif tag == "li":
            self._line(f"- {text}" if text else "")
        elif text:
            self._line(f"_{text}_" if self._p_italic else text)
        self._where, self._text = None, []

    def handle_data(self, data: str) -> None:
        if self._dropping:
            return
        text = _md(data)
        if self._href is not None:
            self._label.append(text)
        elif self._where is not None or data.strip():
            self._text.append(text)

    def close(self) -> None:
        super().close()
        self._flush()


def _cut(line: str, width: int) -> int:
    """Where to break ``line`` within ``width``: the last space outside a link, else before a
    link that starts in range, else ``width`` (one token longer than a whole chunk)."""
    links = [m.span() for m in _LINK.finditer(line)]
    for i in range(width, 0, -1):
        if line[i] == " " and not any(a < i < b for a, b in links):
            return i
    starts = [a for a, _ in links if 0 < a < width]
    return max(starts) if starts else width


def _wrap(line: str, width: int) -> list[str]:
    out = []
    while len(line) > width:
        at = _cut(line, width)
        out.append(line[:at].rstrip())
        line = line[at:].lstrip()
    return [*out, line] if line else out


def _pieces(block: str, limit: int) -> list[str]:
    """A block as pieces of at most ``limit`` characters, broken between lines (module doc);
    its first line (a heading) always opens a piece with what follows it."""
    if len(block) <= limit:
        return [block]
    head, *lines = block.split("\n")
    width = max(limit - len(head) - 1, 1)  # the heading and a first line always fit together
    segments = [s for s in _wrap(head, limit)]
    segments += [s for line in lines for s in _wrap(line, width)]
    out, current = [], ""
    for segment in segments:
        joined = f"{current}\n{segment}" if current else segment
        if len(joined) > limit:
            out.append(current)
            joined = segment
        current = joined
    return [*out, current] if current else out


def _chunks(blocks: list[str], limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in blocks:
        for piece in _pieces(block, limit):
            joined = f"{current}\n\n{piece}" if current else piece
            if len(joined) > limit:
                chunks.append(current)
                joined = piece
            current = joined
    return [*chunks, current] if current else chunks


def html_to_markdown(
    html: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    drop_classes: frozenset[str] = frozenset(),
    italic_classes: frozenset[str] = frozenset(),
    item_paragraphs: int | None = None,
) -> dict[str, Any]:
    """``markdown`` and ``chunks`` of ``html`` (module doc)."""
    doc = _Doc(frozenset(drop_classes), frozenset(italic_classes), item_paragraphs)
    doc.feed(html)
    doc.close()
    blocks = ["\n".join(lines) for lines in doc.blocks if lines]
    return {"markdown": "\n\n".join(blocks), "chunks": _chunks(blocks, max_chars)}


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    value = input.get(key)
    return config.get(key) if value is None else value


def _classes(value: Any, name: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise _Refused(f"bad_params: {name} must be a class name or a list of them")
    return frozenset(value)


def _whole(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _Refused(f"bad_params: {name} must be a whole number from {low} to {high}")
    return value


class MarkdownFromHtmlPort:
    """Built-in ``markdown.from_html`` (module doc). Pure; touches nothing."""

    supports_idempotency_key = True

    def invoke(
        self,
        input: Mapping[str, Any],  # noqa: A002 - the ActorPort signature
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config if isinstance(context.config, Mapping) else {}
        get = lambda key: _param(input, config, key)  # noqa: E731
        try:
            html = get("html")
            if not isinstance(html, str):
                raise _Refused("bad_params: html must be a string")
            limit = get("max_chars")
            items = get("item_paragraphs")
            out = html_to_markdown(
                html,
                max_chars=(
                    DEFAULT_MAX_CHARS
                    if limit is None
                    else _whole(limit, "max_chars", _MIN_CHARS, _MAX_CHARS)
                ),
                drop_classes=_classes(get("drop_classes"), "drop_classes"),
                italic_classes=_classes(get("italic_classes"), "italic_classes"),
                item_paragraphs=None if items is None else _whole(items, "item_paragraphs", 1, 50),
            )
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
        return InvocationResult.completed({**out, "count": len(out["chunks"])})
