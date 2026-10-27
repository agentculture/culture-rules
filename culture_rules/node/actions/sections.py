"""Built-in ``document.sections``: a document split at a configured heading, as sections.

A generic step: it knows no app and no flow. It reads an EPUB, an HTML page or a Markdown
document and splits it at a heading of the configured rank. Each section is its heading's
text, the ``http``/``https`` links it cites and an excerpt of its first paragraphs. Every rule
it applies is config or input; nothing names a document, a field or a destination.

Input (exactly one source; each key may instead sit in ``config``, and the input wins):

* ``content_base64`` - an EPUB's bytes;
* ``html`` - an HTML (or XHTML) page, a string;
* ``markdown`` - a Markdown document, a string;
* ``content_path`` with ``content_root`` - a file under a root: ``content_root`` is an
  absolute directory, ``content_path`` is absolute or relative to it and must lie under it as
  written (``..`` cannot leave it). A symlink anywhere below the root, the file included, is
  refused, and the file is opened ``O_NOFOLLOW``; all of these fail ``path_refused``. The
  format comes from ``format`` (``epub``, ``html`` or ``markdown``) or else the extension
  (``.epub``; ``.html``, ``.htm``, ``.xhtml``; ``.md``, ``.markdown``).

Config:

* ``split_on`` (required) - the heading that starts a section: a tag ``h1``-``h6`` or a level
  1-6 (a Markdown ``#`` heading of that many ``#``; either form works for any source). A
  section runs from its heading to the next heading of the same or a higher rank (a smaller
  number) or the end of the document; text before the first such heading, or after a higher
  heading, belongs to no section. Lower headings are part of the section's text;
* ``paragraphs`` (required) - the excerpt holds the section's first N paragraphs, 0 to 50;
* ``max_chars`` - the excerpt is cut to at most this many characters at the last space or
  line break (cut hard when there is none), 1 to 20000 (required);
* ``require_links`` - drop sections that cite no link (default false);
* ``fields`` - output names for ``heading``, ``links`` and ``excerpt``, e.g.
  ``{"heading": "title"}``; an unnamed field keeps its name. Names are short identifiers and
  must stay distinct.

Text (EPUB and HTML): ``head``, ``title``, ``script`` and ``style`` are dropped, block tags
(``p``, ``div``, ``br``, ``li``, ``tr``, ``h1``-``h6``, ``section`` and other common blocks)
become line breaks, entities are unescaped, runs of spaces collapse, and a blank line
separates paragraphs: a paragraph is the text between two blocks. Links are ``<a href>``
values, scheme ``http``/``https`` with a host, in document order, duplicates dropped. An EPUB
is read as one document: its ``.xhtml``/``.html``/``.htm`` members in name order, never the
navigation document its package marks ``properties="nav"`` (nor the NCX, which is not HTML);
the members' uncompressed bytes may total :data:`MAX_EPUB_TEXT_BYTES` at most.

Markdown (kept simple): ATX headings only (``## Title``, an optional closing ``##``), no
headings inside a fenced code block (fence lines are dropped, code lines kept as text and
never linked). A blank line, a list item, a quote line or a heading starts a paragraph and a
thematic break ends one; other lines join with a space. Links are ``[label](url)``,
``[label](<url>)``, ``<url>`` and bare ``http(s)://`` URLs (trailing punctuation left out),
in order; an image ``![alt](src)`` is its alt text, not a link. The text keeps a link's label,
a bare URL as written, and drops list and quote markers, ``**``, ``__``, ``~~``, ``*`` and
backticks; entities are unescaped; inline HTML is left as written.

Outputs: ``sections`` (in document order, each ``{heading, links, excerpt}`` under the
configured names), ``count`` (sections returned) and ``found`` (sections before
``require_links``). Nothing else enters run state. Final (non-retryable) failures:
``bad_params``, ``path_refused``, ``too_large`` (an input over :data:`MAX_INPUT_BYTES`, or an
EPUB over the text cap) and ``not_epub``. Standard library only; the same input and config
always give the same output.
"""

from __future__ import annotations

import base64
import binascii
import html as htmllib
import io
import os
import posixpath
import re
import stat
import zipfile
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = [
    "DocumentSectionsPort",
    "MAX_EPUB_TEXT_BYTES",
    "MAX_INPUT_BYTES",
    "SECTIONS_BUILTIN",
    "document_sections",
    "split_html",
    "split_markdown",
]

SECTIONS_BUILTIN = "document.sections"
MAX_INPUT_BYTES = 50 * 1024 * 1024  # a file, an EPUB's bytes, or an html/markdown string
MAX_EPUB_TEXT_BYTES = 64 * 1024 * 1024  # an EPUB's pages, uncompressed, in total
_MAX_PARAGRAPHS, _MAX_EXCERPT = 50, 20000
_CANONICAL = ("heading", "links", "excerpt")
_FORMATS = ("epub", "html", "markdown")
_EXTENSIONS = {
    ".epub": "epub",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".md": "markdown",
    ".markdown": "markdown",
}
_PAGES = (".xhtml", ".html", ".htm")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
_HEADINGS = {f"h{n}": n for n in range(1, 7)}
_SKIP = frozenset({"script", "style", "head", "title"})
_BLOCK = frozenset(
    {"p", "div", "br", "li", "tr", "section", "ul", "ol", "dl", "dt", "dd", "table", "td"}
    | {"th", "blockquote", "pre", "hr", "article", "aside", "header", "footer", "figure"}
    | {"figcaption", "nav", "main"}
    | set(_HEADINGS)
)
_SPACES = re.compile(r"[ \t\r\f\v]+")
_LINE_EDGE = re.compile(r" ?\n ?")
_BREAKS = re.compile(r"\n{3,}")
_MD_LINK = re.compile(r"\[([^\]\n]*)\]\((?:<([^>\n]*)>|([^\s()<>]*))\)")
_AUTOLINK = re.compile(r"<(https?://[^\s<>]+)>")
_BARE_URL = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
_URL_TAIL = ".,;:!?*_~"
_MARKS = ("**", "__", "~~", "`", "*")
_BAD_PARAMS, _PATH_REFUSED, _TOO_LARGE = "bad_params", "path_refused", "too_large"


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")


# ------------------------------------------------------------------ sections


@dataclass
class _Section:
    heading: list[str] = field(default_factory=list)
    body: list[str] = field(default_factory=list)  # text with "\n" at block edges
    links: list[str] = field(default_factory=list)

    def link(self, url: str | None) -> None:
        url = (url or "").strip()
        parts = urlsplit(url)
        if parts.scheme in ("http", "https") and parts.netloc and url not in self.links:
            self.links.append(url)


def _collapse(text: str) -> str:
    """Spaces collapsed, no space at a line's edge, at most one blank line in a row."""
    text = _SPACES.sub(" ", text)
    return _BREAKS.sub("\n\n", _LINE_EDGE.sub("\n", text)).strip()


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _excerpt(body: str, paragraphs: int, max_chars: int) -> str:
    """The first ``paragraphs`` of ``body`` (blank-line separated), each on one line, joined
    by a blank line and cut at a word boundary to ``max_chars``."""
    kept = [_one_line(p) for p in _collapse(body).split("\n\n")]
    text = "\n\n".join([p for p in kept if p][:paragraphs])
    if len(text) <= max_chars:
        return text
    head = text[: max_chars + 1]
    at = max(head.rfind(" "), head.rfind("\n"))
    return text[:at].rstrip() if at > 0 else text[:max_chars]


def _shape(found: list[_Section], paragraphs: int, max_chars: int) -> list[dict[str, Any]]:
    return [
        {
            "heading": _one_line("".join(s.heading)),
            "links": list(s.links),
            "excerpt": _excerpt("".join(s.body), paragraphs, max_chars),
        }
        for s in found
    ]


# ------------------------------------------------------------------ HTML and EPUB


class _Splitter:
    """Sections across one or more pages (an EPUB's members are one document)."""

    def __init__(self, level: int) -> None:
        self.level = level
        self.sections: list[_Section] = []
        self.current: _Section | None = None
        self.in_heading = False

    def heading(self, rank: int) -> None:
        if rank <= self.level:
            self.current = None
        if rank == self.level:
            self.current = _Section()
            self.sections.append(self.current)
            self.in_heading = True

    def text(self, data: str) -> None:
        if self.current is not None:
            (self.current.heading if self.in_heading else self.current.body).append(data)

    def end_page(self) -> None:
        self.in_heading = False
        self.text("\n\n")


class _Page(HTMLParser):
    """One page fed to a :class:`_Splitter` (module doc: text and links)."""

    def __init__(self, splitter: _Splitter) -> None:
        super().__init__(convert_charrefs=True)
        self._out = splitter
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
            return
        rank = _HEADINGS.get(tag)
        if rank is not None:
            self._out.heading(rank)
        if tag == "a" and self._out.current is not None:
            self._out.current.link(dict(attrs).get("href"))
        if tag in _BLOCK and not self._out.in_heading:
            self._out.text("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a" and self._out.current is not None:
            self._out.current.link(dict(attrs).get("href"))
        elif tag in _BLOCK and not self._out.in_heading:
            self._out.text("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif self._out.in_heading and _HEADINGS.get(tag) == self._out.level:
            self._out.in_heading = False
            self._out.text("\n")
        elif tag in _BLOCK and not self._out.in_heading:
            self._out.text("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._out.text(data)


def _split_pages(pages: list[str], level: int) -> list[_Section]:
    splitter = _Splitter(level)
    for markup in pages:
        page = _Page(splitter)
        page.feed(markup)
        page.close()
        splitter.end_page()
    return splitter.sections


def split_html(markup: str, *, level: int, paragraphs: int, max_chars: int) -> list[dict]:
    """``{heading, links, excerpt}`` per section of an HTML page (module doc)."""
    return _shape(_split_pages([markup], level), paragraphs, max_chars)


class _Attrs(HTMLParser):
    """The attributes of every ``tag`` in a small XML document (the container, the package)."""

    def __init__(self, tag: str) -> None:
        super().__init__(convert_charrefs=True)
        self._tag = tag
        self.found: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == self._tag:
            self.found.append({k: v or "" for k, v in attrs})


def _attrs(text: str, tag: str) -> list[dict[str, str]]:
    parser = _Attrs(tag)
    parser.feed(text)
    parser.close()
    return parser.found


def _nav_names(book: zipfile.ZipFile, read: Any) -> set[str]:
    """The members the package marks as its navigation document (none without a package)."""
    try:
        container = read(book.getinfo("META-INF/container.xml"))
    except KeyError:
        return set()
    navs: set[str] = set()
    for rootfile in _attrs(container, "rootfile"):
        package = rootfile.get("full-path", "")
        try:
            opf = read(book.getinfo(package))
        except KeyError:
            continue
        base = posixpath.dirname(package)
        for item in _attrs(opf, "item"):
            if "nav" in item.get("properties", "").split():
                navs.add(posixpath.normpath(posixpath.join(base, unquote(item.get("href", "")))))
    return navs


def _epub_pages(data: bytes) -> list[str]:
    """The EPUB's pages in name order, its navigation document left out, under the text cap."""
    budget = [MAX_EPUB_TEXT_BYTES]

    def read(info: zipfile.ZipInfo) -> str:
        if info.file_size > budget[0]:
            raise _Refused(_TOO_LARGE, f"the EPUB's pages exceed {MAX_EPUB_TEXT_BYTES} bytes")
        with book.open(info) as member:
            raw = member.read(budget[0] + 1)
        if len(raw) > budget[0]:
            raise _Refused(_TOO_LARGE, f"the EPUB's pages exceed {MAX_EPUB_TEXT_BYTES} bytes")
        budget[0] -= len(raw)
        return raw.decode("utf-8", errors="replace")

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            navs = _nav_names(book, read)
            members = sorted(
                (i for i in book.infolist() if i.filename.lower().endswith(_PAGES)),
                key=lambda i: i.filename,
            )
            return [read(i) for i in members if posixpath.normpath(i.filename) not in navs]
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, NotImplementedError,
            RuntimeError, zlib.error) as exc:  # fmt: skip
        raise _Refused("not_epub", "the content is not a readable EPUB") from exc


# ------------------------------------------------------------------ Markdown


def _md_heading(line: str) -> tuple[int, str] | None:
    """``(level, text)`` of an ATX heading line, else ``None``."""
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3 or not stripped.startswith("#"):
        return None
    level = len(stripped) - len(stripped.lstrip("#"))
    rest = stripped[level:]
    if level > 6 or (rest and rest[0] not in " \t"):
        return None
    text = rest.strip()
    closing = text.rstrip("#")
    if closing != text and (not closing or closing[-1] in " \t"):
        text = closing.strip()
    return level, text


def _md_marker(line: str) -> tuple[bool, str]:
    """``(starts a block, text)``: a list item or quote line loses its marker."""
    stripped = line.strip()
    if stripped.startswith(">"):
        return True, stripped[1:].strip()
    head, _, rest = stripped.partition(" ")
    if head in ("-", "*", "+") or (
        head[:-1].isdigit() and head[-1:] in (".", ")") and len(head) <= 10
    ):
        return True, rest.strip()
    return False, stripped


def _md_rule(line: str) -> bool:
    compact = line.replace(" ", "").replace("\t", "")
    return len(compact) >= 3 and len(set(compact)) == 1 and compact[0] in "-*_"


def _plain(text: str) -> str:
    for mark in _MARKS:
        text = text.replace(mark, "")
    return htmllib.unescape(text)


def _bare(text: str, out: list[str], links: list[str]) -> None:
    """``text`` with its bare URLs kept as written and linked."""
    at = 0
    for match in _BARE_URL.finditer(text):
        url = match.group(0).rstrip(_URL_TAIL)
        out.append(_plain(text[at : match.start()]))
        out.append(url)
        links.append(url)
        at = match.start() + len(url)
    out.append(_plain(text[at:]))


def _next(pattern: re.Pattern[str], text: str, at: int, last: re.Match[str] | None):
    """``pattern``'s first match at or after ``at``, reusing ``last`` while it still is."""
    if last is not None and last.start() >= at:
        return last
    return pattern.search(text, at)


def _md_token(match: re.Match[str], image: bool, out: list[str], links: list[str]) -> None:
    """A ``[label](url)`` (its label; linked unless an image) or a ``<url>`` (as written)."""
    if match.re is _AUTOLINK:
        out.append(match.group(1))
        links.append(match.group(1))
        return
    out.append(_md_inline(match.group(1))[0])
    if not image:
        links.append(match.group(2) if match.group(2) is not None else match.group(3))


def _md_inline(text: str) -> tuple[str, list[str]]:
    """A Markdown line as a reader sees it, and the links it cites in order (module doc).
    Each pattern is searched again only once the scan passes its last match (linear)."""
    out: list[str] = []
    links: list[str] = []
    at = 0
    link = auto = None
    while at < len(text):
        link, auto = _next(_MD_LINK, text, at, link), _next(_AUTOLINK, text, at, auto)
        found = [m for m in (link, auto) if m is not None]
        if not found:
            break
        match = min(found, key=lambda m: m.start())
        start = match.start()
        image = match is link and start > at and text[start - 1] == "!"
        _bare(text[at : start - 1 if image else start], out, links)
        _md_token(match, image, out, links)
        at = match.end()
    _bare(text[at:], out, links)
    return "".join(out), links


def _md_sections(markdown: str, level: int) -> list[_Section]:
    sections: list[_Section] = []
    current: _Section | None = None
    fenced = False
    for line in markdown.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            if current is not None:
                current.body.append("\n\n")
            continue
        heading = None if fenced else _md_heading(line)
        if heading is not None and heading[0] <= level:
            current = _Section() if heading[0] == level else None
            if current is not None:
                sections.append(current)
                text, links = _md_inline(heading[1])
                current.heading.append(text)
                for url in links:
                    current.link(url)
            continue
        if current is not None:
            _md_line(current, line, heading, fenced)
    return sections


def _md_line(section: _Section, line: str, heading: tuple[int, str] | None, fenced: bool) -> None:
    """One body line into ``section``: text with paragraph breaks, and its links."""
    if fenced:
        section.body.append(f"\n\n{line}\n\n")
        return
    if not line.strip() or _md_rule(line):
        section.body.append("\n\n")
        return
    starts, raw = (True, heading[1]) if heading is not None else _md_marker(line)
    text, links = _md_inline(raw)
    for url in links:
        section.link(url)
    section.body.append(f"\n\n{text}" if starts else f" {text}")
    if heading is not None:
        section.body.append("\n\n")


def split_markdown(markdown: str, *, level: int, paragraphs: int, max_chars: int) -> list[dict]:
    """``{heading, links, excerpt}`` per section of a Markdown document (module doc)."""
    return _shape(_md_sections(markdown, level), paragraphs, max_chars)


# ------------------------------------------------------------------ inputs and config


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    value = input.get(key)
    return config.get(key) if value is None else value


def _level(value: Any) -> int:
    if isinstance(value, str) and value in _HEADINGS:
        return _HEADINGS[value]
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 6:
        return value
    raise _Refused(_BAD_PARAMS, "split_on must be a heading tag h1-h6 or a level 1-6")


def _whole(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _Refused(_BAD_PARAMS, f"{name} must be a whole number from {low} to {high}")
    return value


def _fields(value: Any) -> dict[str, str]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise _Refused(_BAD_PARAMS, f"fields must map {', '.join(_CANONICAL)} to output names")
    names = {key: key for key in _CANONICAL}
    for key, name in value.items():
        if key not in names:
            raise _Refused(_BAD_PARAMS, f"fields names {key!r}; only {', '.join(_CANONICAL)}")
        if not (isinstance(name, str) and _NAME.fullmatch(name)):
            raise _Refused(_BAD_PARAMS, f"field name {name!r} is not a short identifier")
        names[key] = name
    if len(set(names.values())) != len(names):
        raise _Refused(_BAD_PARAMS, "fields must give distinct output names")
    return names


def _relative(path_value: str, root_value: str, root: Path) -> Path:
    """``path_value`` (absolute, or relative to the root) relative to the root, as written or
    resolved; ``..`` is applied as written, so it cannot leave the root."""
    path = Path(os.path.normpath(os.path.join(root_value, path_value)))
    for base in (Path(os.path.normpath(root_value)), root):
        if path != base and path.is_relative_to(base):
            return path.relative_to(base)
    raise _Refused(_PATH_REFUSED, "content_path is not under content_root")


def _read_under(path_value: Any, root_value: Any) -> tuple[bytes, str]:
    """The bytes of the regular file at ``path_value`` under ``root_value`` (module doc), and
    its extension."""
    if not isinstance(root_value, str) or not os.path.isabs(root_value):
        raise _Refused(_PATH_REFUSED, "content_root must be an absolute directory")
    if not isinstance(path_value, str) or not path_value:
        raise _Refused(_BAD_PARAMS, "content_path must be a non-empty string")
    try:
        root = Path(root_value).resolve(strict=True)
        current = root
        for part in _relative(path_value, root_value, root).parts:
            current = current / part
            if current.is_symlink():
                raise _Refused(_PATH_REFUSED, "content_path must not pass through a symlink")
        fd = os.open(current, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise _Refused(_PATH_REFUSED, "content_path is not a regular file")
            data = handle.read(MAX_INPUT_BYTES + 1)
    except (OSError, RuntimeError) as exc:
        raise _Refused(_PATH_REFUSED, "content_path is not a readable file under the root") from exc
    if len(data) > MAX_INPUT_BYTES:
        raise _Refused(_TOO_LARGE, f"content_path's file exceeds {MAX_INPUT_BYTES} bytes")
    return data, current.suffix.lower()


def _epub_bytes(value: Any) -> bytes:
    if not isinstance(value, str) or not value:
        raise _Refused(_BAD_PARAMS, "content_base64 must be a non-empty base64 string")
    if len(value) > (MAX_INPUT_BYTES + 2) // 3 * 4 + 4:
        raise _Refused(_TOO_LARGE, f"content_base64 exceeds {MAX_INPUT_BYTES} bytes")
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _Refused(_BAD_PARAMS, "content_base64 is not valid base64") from exc
    if len(data) > MAX_INPUT_BYTES:
        raise _Refused(_TOO_LARGE, f"content_base64 exceeds {MAX_INPUT_BYTES} bytes")
    return data


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise _Refused(_BAD_PARAMS, f"{name} must be a string")
    if len(value.encode("utf-8")) > MAX_INPUT_BYTES:
        raise _Refused(_TOO_LARGE, f"{name} exceeds {MAX_INPUT_BYTES} bytes")
    return value


def _source(get: Any) -> tuple[str, str | bytes]:
    """``(format, content)``: EPUB bytes, or HTML or Markdown text (module doc)."""
    fmt = get("format")
    if fmt is not None and fmt not in _FORMATS:
        raise _Refused(_BAD_PARAMS, f"format must be one of {', '.join(_FORMATS)}")
    given = [key for key in ("content_base64", "html", "markdown", "content_path") if get(key)]
    if len(given) != 1:
        raise _Refused(
            _BAD_PARAMS, "give exactly one of content_base64, html, markdown or content_path"
        )
    key = given[0]
    if key == "content_base64":
        return "epub", _epub_bytes(get(key))
    if key != "content_path":
        return key, _string(get(key), key)
    if get("content_root") is None:
        raise _Refused(_BAD_PARAMS, "content_path needs content_root")
    data, extension = _read_under(get("content_path"), get("content_root"))
    fmt = fmt or _EXTENSIONS.get(extension)
    if fmt is None:
        raise _Refused(_BAD_PARAMS, "name a format: the file's extension is not a known one")
    return fmt, data if fmt == "epub" else data.decode("utf-8", errors="replace")


def document_sections(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """The step's outputs (module doc), else :class:`_Refused`."""
    get = lambda key: _param(input, config, key)  # noqa: E731
    level = _level(get("split_on"))
    paragraphs = _whole(get("paragraphs"), "paragraphs", 0, _MAX_PARAGRAPHS)
    max_chars = _whole(get("max_chars"), "max_chars", 1, _MAX_EXCERPT)
    require_links = get("require_links")
    if require_links is not None and not isinstance(require_links, bool):
        raise _Refused(_BAD_PARAMS, "require_links must be true or false")
    names = _fields(get("fields"))
    fmt, content = _source(get)
    shape = {"level": level, "paragraphs": paragraphs, "max_chars": max_chars}
    if fmt == "epub":
        found = _shape(_split_pages(_epub_pages(content), level), paragraphs, max_chars)
    elif fmt == "html":
        found = split_html(str(content), **shape)
    else:
        found = split_markdown(str(content), **shape)
    kept = [s for s in found if s["links"] or not require_links]
    out = [{names[key]: s[key] for key in _CANONICAL} for s in kept]
    return {"sections": out, "count": len(out), "found": len(found)}


class DocumentSectionsPort:
    """Built-in ``document.sections`` (module doc). Reads at most the one given file."""

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
        try:
            return InvocationResult.completed(document_sections(input, config))
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
