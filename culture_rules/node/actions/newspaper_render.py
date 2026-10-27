"""Rendering for the built-in ``newspaper.render`` (#53): one dated issue as HTML and an EPUB.

:class:`~culture_rules.node.actions.newspaper.NewspaperRenderPort` is the port; this module is
the pure part it calls (:func:`render_issue`).

Input
=====

``date`` (``YYYY-MM-DD``, the issue's local date), ``title`` (default
:data:`DEFAULT_TITLE`) and ``sections`` (1..:data:`MAX_SECTIONS`). When at least one section
has results and none of those sections has a summary (cortex was down, or the summary window
closed first), the masthead carries :data:`SUMMARIES_UNAVAILABLE` once, in the email and in
the EPUB's first chapter; an empty section needs no summary and does not count.
Each section is ``{name, results, summary}``: ``results`` a list of ``{title, url, snippet,
age, source}`` (search.web's shape) and ``summary`` the model's text or null. A ``for_each``
loop yields one list per output port, so ``results`` and ``summaries`` may instead be given as
top-level lists parallel to ``sections``; a key on the section itself wins. An element of
``results`` may also be a for_each result object, ``{results: [...]}`` (newspaper.filter's
output), or ``{}`` for an iteration that produced none. A summary may also
be ``{text: ...}`` (llm.complete's output).

Each section renders as exactly one of:

* **No results** - the section's results are empty: the line :data:`NO_RESULTS`, never the
  summary (h6: nothing is filled in).
* **The summary** - it passes the link check: every non-blank line is a bullet (the locked
  brief's format), every bullet carries a link, and every link is an ``http``/``https`` URL
  that is one of *this* section's result URLs (c19/h5: every summarised item links its
  source; a paragraph or heading fails the check).
* **The results** - title linked, snippet, ``source · age`` - followed by
  :data:`SUMMARY_UNAVAILABLE` when the summary is missing, or :data:`LINK_NOTE` when it failed
  the link check (the summary is dropped).

The summary is markdown-ish: ``-``/``*``/``•``/``1.`` bullets, ``[text](url)`` links,
``**bold**``, other lines as paragraphs (:func:`summary_to_xhtml`; the link check admits
bullets only). Each line is split into link and text tokens; a text token (and a link's
label) is escaped and then bolded, and an anchor is built last from the escaped URL, so no
markdown substitution ever touches a generated tag or attribute. Raw HTML in a summary is
shown as text and never passed through.
Every field is escaped, XML-invalid control characters are stripped, long fields are
truncated, and only ``http``/``https`` URLs become links.

Output
======

``subject`` (``<title> <date>``), ``html`` (the email body), ``epub_base64``, ``size`` (EPUB
bytes) and ``sections_rendered``. The EPUB is EPUB 3 with an EPUB 2 NCX: ``mimetype`` first
and stored, ``META-INF/container.xml``, ``OEBPS/content.opf``, ``nav.xhtml``, ``toc.ncx``,
``style.css`` and one chapter per section (``s01.xhtml``...), no remote resources. It is
deterministic: fixed zip timestamps and permissions, stable ids, ``dcterms:modified`` from the
date, so the same input gives identical bytes. An HTML body over :data:`HTML_CAP` or an EPUB
over :data:`EPUB_CAP` fails (``html_too_large`` / ``epub_too_large``). Standard-library only.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date as Date
from typing import Any
from urllib.parse import urlsplit

from culture_rules.node.actions._epub import (
    ENCODING,
    EPUB_NS,
    INVALID_XML,
    OEBPS,
    XHTML_NS,
    XML_DECL,
    container,
    esc,
    nav_body,
    ncx,
    opf,
    pack,
)

__all__ = [
    "DEFAULT_TITLE",
    "EPUB_CAP",
    "HTML_CAP",
    "LINK_NOTE",
    "NO_RESULTS",
    "SUMMARIES_UNAVAILABLE",
    "SUMMARY_UNAVAILABLE",
    "RenderError",
    "http_host",
    "render_issue",
    "summary_links",
    "summary_to_xhtml",
]

DEFAULT_TITLE = "Daily newspaper"
NO_RESULTS = "No results."
SUMMARY_UNAVAILABLE = "Summary unavailable."
SUMMARIES_UNAVAILABLE = "Summaries were unavailable for this issue."
LINK_NOTE = "Summary withheld: it linked to a page outside the results of this section."
HTML_CAP = 1 << 20  # 1 MiB
EPUB_CAP = 5 << 20  # 5 MiB
MAX_SECTIONS = 20
MAX_RESULTS = 50
_LIMITS = {"title": 300, "snippet": 1000, "source": 200, "age": 80, "url": 2048}
_NAME_MAX = 80
_ISSUE_TITLE_MAX = 120
_SUMMARY_MAX = 20000
_ELLIPSIS = "…"


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LINK_RE = re.compile(r"\[([^\]\n]{1,300})\]\(((?:[^()\s]|\([^()\s]*\)){1,2048})\)")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d{1,3}[.)])\s+")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s*")

_CSS = (
    "body{font-family:Georgia,serif;line-height:1.45;margin:0 auto;max-width:42em;"
    "padding:0 1em}\n"
    "h1,h2{font-family:Helvetica,Arial,sans-serif}\n"
    ".date,.meta,.note,.empty{color:#555;font-size:0.9em}\n"
    ".note,.empty{font-style:italic}\n"
    "ul{padding-left:1.2em}\nli{margin-bottom:0.6em}\np{margin:0.2em 0}\n"
)


class RenderError(ValueError):
    """The issue cannot be rendered; the message starts with its code."""


@dataclass(frozen=True)
class _Section:
    name: str
    results: tuple[Mapping[str, str], ...]
    summary: str | None


# ------------------------------------------------------------------ text and links


def _clean(value: Any, limit: int) -> str:
    text = INVALID_XML.sub("", value if isinstance(value, str) else "").strip()
    return text if len(text) <= limit else text[:limit] + _ELLIPSIS


def http_host(url: Any) -> str | None:
    """The lower-cased host of an ``http``/``https`` URL, else None (no whitespace allowed)."""
    if not isinstance(url, str) or not url or INVALID_XML.search(url) or re.search(r"\s", url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    return host if parts.scheme in ("http", "https") and host else None


def _format(text: str) -> str:
    """Escape one plain-text token, then turn ``**bold**`` into ``<strong>``.

    Only ever called on text that is not markup yet, never on generated tags or attributes,
    so a ``**`` inside a URL can't reach an ``href`` (Codex r1 #4).
    """
    return _BOLD_RE.sub(r"<strong>\1</strong>", esc(text))


def _anchor(url: str, label: str) -> str:
    """A link built last: the escaped URL in the attribute, the formatted label as content."""
    if http_host(url) is None:
        return _format(label)
    return f'<a href="{esc(url)}">{_format(label)}</a>'


def summary_links(text: str) -> list[str]:
    """Every link URL in a summary, in order."""
    return [match.group(2) for match in _LINK_RE.finditer(text)]


def _inline(text: str) -> str:
    """One line as XHTML: split into link and text tokens, format each token on its own."""
    out, pos = [], 0
    for match in _LINK_RE.finditer(text):
        out.append(_format(text[pos : match.start()]))
        out.append(_anchor(match.group(2), match.group(1)))
        pos = match.end()
    out.append(_format(text[pos:]))
    return "".join(out)


def _lines(text: str) -> list[tuple[bool, str]]:
    """(is_bullet, content) for every non-blank line."""
    lines = []
    for line in text.splitlines():
        if not line.strip():
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            lines.append((True, line[bullet.end() :].strip()))
        else:
            lines.append((False, _HEADING_RE.sub("", line).strip()))
    return lines


def summary_to_xhtml(text: str) -> str:
    """Convert a markdown-ish summary to escaped XHTML: bullets to ``ul``, lines to ``p``."""
    out: list[str] = []
    in_list = False
    for bullet, content in _lines(text):
        if bullet != in_list:
            out.append("<ul>" if bullet else "</ul>")
            in_list = bullet
        out.append(f"<li>{_inline(content)}</li>" if bullet else f"<p>{_inline(content)}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def _summary_ok(summary: str, results: Sequence[Mapping[str, str]]) -> bool:
    """The link check (module doc): bullets only, each sourced from this section's results.

    The locked brief asks for bullets and nothing else, so any non-bullet line (a paragraph,
    a heading) fails the check: an unsourced paragraph can't ride along with one linked
    bullet (Codex r1 #5). The section then renders its results instead.
    """
    allowed = {r["url"] for r in results if http_host(r["url"])}
    links = summary_links(summary)
    if not links or any(url not in allowed for url in links):
        return False
    return all(bullet and _LINK_RE.search(content) for bullet, content in _lines(summary))


# ------------------------------------------------------------------ input


def _fail(path: str, reason: str) -> RenderError:
    return RenderError(f"bad_input: {path}: {reason}")


def _date(value: Any) -> str:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise _fail("date", "must be YYYY-MM-DD")
    try:
        Date.fromisoformat(value)
    except ValueError:
        raise _fail("date", "not a calendar date") from None
    return value


def _title(value: Any) -> str:
    if value is None:
        return DEFAULT_TITLE
    text = _clean(value, _ISSUE_TITLE_MAX)
    if not text:
        raise _fail("title", "must be a non-empty string")
    return text


def _result(item: Mapping[str, Any]) -> dict[str, str]:
    return {key: _clean(item.get(key), limit) for key, limit in _LIMITS.items()}


def _summary(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("text")
    if not isinstance(value, str) or not value.strip():
        return None
    return value[:_SUMMARY_MAX]


def _parallel(payload: Mapping[str, Any], key: str) -> Sequence[Any]:
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _fail(key, "must be a list parallel to sections")
    return value


def _pick(raw: Mapping[str, Any], key: str, parallel: Sequence[Any], index: int) -> Any:
    if key in raw:
        return raw[key]
    return parallel[index] if index < len(parallel) else None


def _section(raw: Any, index: int, results: Sequence[Any], summaries: Sequence[Any]) -> _Section:
    path = f"sections[{index}]"
    if not isinstance(raw, Mapping):
        raise _fail(path, "must be an object")
    name = _clean(raw.get("name"), _NAME_MAX)
    if not name:
        raise _fail(f"{path}.name", "required, a non-empty string")
    found = _pick(raw, "results", results, index)
    if isinstance(found, Mapping):  # a for_each result object: {"results": [...]} or {}
        found = found.get("results")
    if found is None:
        found = []
    if not isinstance(found, list):
        raise _fail(f"{path}.results", "must be a list")
    kept = tuple(_result(item) for item in found if isinstance(item, Mapping))[:MAX_RESULTS]
    return _Section(name, kept, _summary(_pick(raw, "summary", summaries, index)))


def _sections(payload: Mapping[str, Any]) -> list[_Section]:
    raw = payload.get("sections")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SECTIONS:
        raise _fail("sections", f"must be a list of 1 to {MAX_SECTIONS} sections")
    results, summaries = _parallel(payload, "results"), _parallel(payload, "summaries")
    return [_section(item, n, results, summaries) for n, item in enumerate(raw)]


# ------------------------------------------------------------------ XHTML fragments


def _cid(n: int) -> str:
    """A section's stable id and chapter file stem (1-based)."""
    return f"s{n:02d}"


def _result_item(result: Mapping[str, str]) -> str:
    parts = [f"<p>{_anchor(result['url'], result['title'] or result['url'])}</p>"]
    if result["snippet"]:
        parts.append(f"<p>{esc(result['snippet'])}</p>")
    meta = " · ".join(value for value in (result["source"], result["age"]) if value)
    if meta:
        parts.append(f'<p class="meta">{esc(meta)}</p>')
    return f"<li>{''.join(parts)}</li>"


def _note(text: str) -> str:
    return f'<p class="note">{esc(text)}</p>'


def _body(section: _Section) -> str:
    if not section.results:
        return f'<p class="empty">{esc(NO_RESULTS)}</p>'
    if section.summary is not None and _summary_ok(section.summary, section.results):
        return summary_to_xhtml(section.summary)
    items = "".join(_result_item(result) for result in section.results)
    note = SUMMARY_UNAVAILABLE if section.summary is None else LINK_NOTE
    return f"<ul>{items}</ul>{_note(note)}"


def _summaries_unavailable(sections: list[_Section]) -> bool:
    """Whether the issue had results to summarise and not one summary (module doc)."""
    wanted = [section for section in sections if section.results]
    return bool(wanted) and all(section.summary is None for section in wanted)


def _masthead(title: str, day: str, unavailable: bool) -> str:
    head = f'<h1>{esc(title)}</h1><p class="date">{esc(day)}</p>'
    return head + (_note(SUMMARIES_UNAVAILABLE) if unavailable else "")


# ------------------------------------------------------------------ HTML


def _html(title: str, day: str, sections: list[_Section], unavailable: bool) -> str:
    body = "".join(
        f'<section id="{_cid(n)}"><h2>{esc(s.name)}</h2>{_body(s)}</section>'
        for n, s in enumerate(sections, 1)
    )
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"/>'
        f"<title>{esc(title)} {esc(day)}</title><style>{_CSS}</style></head>"
        f"<body>{_masthead(title, day, unavailable)}{body}</body></html>\n"
    )


# ------------------------------------------------------------------ EPUB


def _xhtml(title: str, body: str, *, css: bool = True) -> str:
    link = '<link rel="stylesheet" type="text/css" href="style.css"/>' if css else ""
    return (
        f"{XML_DECL}<!DOCTYPE html>\n"
        f'<html xmlns="{XHTML_NS}" xmlns:epub="{EPUB_NS}" lang="en" xml:lang="en">'
        f'<head><meta charset="utf-8"/><title>{esc(title)}</title>{link}</head>'
        f"<body>{body}</body></html>\n"
    )


def _chapter(n: int, section: _Section, head: str) -> str:
    body = f'{head}<section id="{_cid(n)}"><h2>{esc(section.name)}</h2>{_body(section)}</section>'
    return _xhtml(section.name, body)


def _nav(entries: list[tuple[str, str]]) -> str:
    return _xhtml("Contents", nav_body(entries), css=False)


def _epub(title: str, day: str, sections: list[_Section], unavailable: bool) -> bytes:
    uid = f"urn:culture-rules:newspaper:{day}"
    chapters = [_cid(n) for n in range(1, len(sections) + 1)]
    entries = [(s.name, _cid(n)) for n, s in enumerate(sections, 1)]
    files = [
        ("META-INF/container.xml", container()),
        (f"{OEBPS}content.opf", opf(uid, f"{title} {day}", day, chapters)),
        (f"{OEBPS}nav.xhtml", _nav(entries)),
        (f"{OEBPS}toc.ncx", ncx(uid, f"{title} {day}", entries)),
        (f"{OEBPS}style.css", _CSS),
    ]
    head = _masthead(title, day, unavailable)
    for n, section in enumerate(sections, 1):
        files.append((f"{OEBPS}{_cid(n)}.xhtml", _chapter(n, section, head if n == 1 else "")))
    return pack(files)


# ------------------------------------------------------------------ the issue


def render_issue(
    payload: Mapping[str, Any], *, html_cap: int = HTML_CAP, epub_cap: int = EPUB_CAP
) -> dict[str, Any]:
    """Render one issue (module doc); raises :class:`RenderError` on bad input or a cap."""
    day = _date(payload.get("date"))
    title = _title(payload.get("title"))
    sections = _sections(payload)
    unavailable = _summaries_unavailable(sections)
    page = _html(title, day, sections, unavailable)
    page_size = len(page.encode(ENCODING))
    if page_size > html_cap:
        raise RenderError(f"html_too_large: {page_size} bytes > {html_cap}")
    book = _epub(title, day, sections, unavailable)
    if len(book) > epub_cap:
        raise RenderError(f"epub_too_large: {len(book)} bytes > {epub_cap}")
    return {
        "subject": f"{title} {day}",
        "html": page,
        "epub_base64": base64.b64encode(book).decode("ascii"),
        "size": len(book),
        "sections_rendered": len(sections),
    }
