"""Built-in ``newspaper.digest``: one delivered issue as Discord-sized Markdown chunks.

It reads the issue ``newspaper.issue`` stored for a date (``newspaper_issues``, the frozen
``email_payload.html`` that ``newspaper.render`` built) and writes it back as Markdown: the
title and date in bold, then each section's name in bold and its summary bullets (their
bold kept). A section
the render showed without a summary lists its headlines as links; an empty one says so. Link
targets sit in angle brackets (``[label](<url>)``) so Discord shows no embed per link.

Input: ``date`` (``YYYY-MM-DD``; may instead sit in ``config``). Config: ``max_chars`` (each
chunk's limit, 200 to 2000, default :data:`DEFAULT_MAX_CHARS`, under Discord's 2000). Chunks
break between sections; a section longer than a chunk breaks between its lines. Outputs:
``title``, ``chunks`` (in order) and ``count``. Final failures: ``bad_params`` (a malformed
date or max_chars) and ``no_issue`` (nothing stored for the date). Standard library only; the
same stored issue always gives the same chunks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date as Date
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actions.newspaper_issue import ISSUES_COLLECTION

__all__ = ["DEFAULT_MAX_CHARS", "DIGEST_BUILTIN", "NewspaperDigestPort", "digest_issue"]

DIGEST_BUILTIN = "newspaper.digest"
DEFAULT_MAX_CHARS = 1900
_MIN_CHARS, _MAX_CHARS = 200, 2000
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SKIPPED = {"meta", "date"}  # a result's source/age line; the masthead date (read apart)
_ITALIC = {"note", "empty"}


class _Refused(Exception):
    pass


class _Issue(HTMLParser):
    """Walks newspaper.render's HTML: ``h1`` + ``p.date``, then ``section > h2`` and its
    body (``p``, ``ul > li``, ``a``); a result item keeps only its first paragraph (the
    headline link), not its snippet or meta line."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.day = ""
        self.head_notes: list[str] = []
        self.sections: list[tuple[str, list[str]]] = []
        self._where: str | None = None  # "h1", "date", "h2", "p", "li"
        self._text: list[str] = []
        self._p_class: str | None = None
        self._li_paragraphs = 0
        self._skip = False
        self._href: str | None = None
        self._label: list[str] = []

    # -- helpers
    def _emit(self, line: str) -> None:
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            return
        if self.sections:
            self.sections[-1][1].append(line)
        else:
            self.head_notes.append(line)

    def _out(self, text: str) -> None:
        if self._href is not None:
            self._label.append(text)
        elif not self._skip:
            self._text.append(text)

    # -- parser hooks
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        cls = dict(attrs).get("class")
        if tag == "h1":
            self._where, self._text = "h1", []
        elif tag == "h2":
            self._where, self._text = "h2", []
        elif tag == "li":
            self._where, self._text, self._li_paragraphs = "li", [], 0
        elif tag == "p":
            self._p_tag(cls)
        elif tag == "a" and self._where in ("p", "li"):
            self._href, self._label = dict(attrs).get("href") or "", []
        elif tag in ("strong", "b") and self._where in ("p", "li"):
            self._out("**")

    def _p_tag(self, cls: str | None) -> None:
        if self._where == "li":
            self._li_paragraphs += 1
            self._skip = self._li_paragraphs > 1  # snippet and meta lines of a result
            return
        if cls == "date" and not self.sections:
            self._where, self._text = "date", []
            return
        self._where, self._text, self._p_class = "p", [], cls
        self._skip = cls in _SKIPPED

    def handle_endtag(self, tag: str) -> None:
        if tag in ("strong", "b") and self._where in ("p", "li"):
            self._out("**")
        elif tag == "a" and self._href is not None:
            self._end_link()
        elif tag == self._where and tag in ("h1", "h2", "li"):
            self._end_block(tag, "".join(self._text).strip())
        elif tag == "p":
            self._end_p()

    def _end_link(self) -> None:
        label = "".join(self._label).strip()
        href, self._href = self._href, None
        if not self._skip:
            self._text.append(f"[{label}](<{href}>)" if href else label)

    def _end_block(self, tag: str, text: str) -> None:
        if tag == "h1":
            self.title = text
        elif tag == "h2":
            self.sections.append((text, []))
        else:
            self._emit("- " + text)
        self._where = None

    def _end_p(self) -> None:
        if self._where == "date":
            self.day, self._where = "".join(self._text).strip(), None
        elif self._where == "p":
            self._end_paragraph()
        elif self._where == "li":
            self._skip = False

    def _end_paragraph(self) -> None:
        text = "".join(self._text).strip()
        if not self._skip and text:
            self._emit(f"_{text}_" if self._p_class in _ITALIC else text)
        self._where, self._skip, self._p_class = None, False, None

    def handle_data(self, data: str) -> None:
        if self._where is not None:
            self._out(data)


def _blocks(issue: _Issue) -> tuple[str, list[str]]:
    title = " ".join(part for part in (issue.title, issue.day) if part)
    head = "\n".join([f"**{title}**", *issue.head_notes])
    blocks = [head] + ["\n".join([f"**{name}**", *lines]) for name, lines in issue.sections]
    return title, blocks


def _pieces(block: str, limit: int) -> list[str]:
    """A block as pieces of at most ``limit`` characters, broken between lines."""
    if len(block) <= limit:
        return [block]
    out, current = [], ""
    for line in block.split("\n"):
        while len(line) > limit:  # one line longer than a chunk: cut it
            if current:
                out.append(current)
                current = ""
            out.append(line[:limit])
            line = line[limit:]
        joined = f"{current}\n{line}" if current else line
        if len(joined) > limit:
            out.append(current)
            joined = line
        current = joined
    if current:
        out.append(current)
    return out


def digest_issue(html: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
    """The issue's ``title`` and its Markdown ``chunks`` (module doc)."""
    issue = _Issue()
    issue.feed(html)
    issue.close()
    title, blocks = _blocks(issue)
    chunks: list[str] = []
    current = ""
    for block in blocks:
        for piece in _pieces(block, max_chars):
            joined = f"{current}\n\n{piece}" if current else piece
            if len(joined) > max_chars:
                chunks.append(current)
                joined = piece
            current = joined
    if current:
        chunks.append(current)
    return {"title": title, "chunks": chunks}


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    value = input.get(key)
    return config.get(key) if value is None else value


def _day(value: Any) -> str:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise _Refused("bad_params: date must be YYYY-MM-DD")
    try:
        Date.fromisoformat(value)
    except ValueError:
        raise _Refused("bad_params: date is not a calendar date") from None
    return value


def _limit(value: Any) -> int:
    if value is None:
        return DEFAULT_MAX_CHARS
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Refused("bad_params: max_chars must be a whole number")
    if not _MIN_CHARS <= value <= _MAX_CHARS:
        raise _Refused(f"bad_params: max_chars must be {_MIN_CHARS} to {_MAX_CHARS}")
    return value


class NewspaperDigestPort:
    """Built-in ``newspaper.digest`` (module doc). Reads the store; writes nothing."""

    supports_idempotency_key = True

    def __init__(self, store: Any) -> None:
        self._store = store

    def invoke(
        self,
        input: Mapping[str, Any],  # noqa: A002 - the ActorPort signature
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config if isinstance(context.config, Mapping) else {}
        try:
            day = _day(_param(input, config, "date"))
            limit = _limit(config.get("max_chars"))
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
        doc = self._store.get(ISSUES_COLLECTION, day) or {}
        payload = doc.get("email_payload") if isinstance(doc, Mapping) else None
        html = payload.get("html") if isinstance(payload, Mapping) else None
        if not isinstance(html, str) or not html:
            return InvocationResult.failed(f"no_issue: nothing stored for {day}", retryable=False)
        out = digest_issue(html, max_chars=limit)
        return InvocationResult.completed({**out, "count": len(out["chunks"])})
