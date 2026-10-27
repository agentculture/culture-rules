"""culture-library's text, chunk, match and manifest rules, copied as test fixtures (#97).

culture-library is not a dependency of culture-rules and is never imported. These are copies,
cited per function, taken from culture-library main at bb981c0 (0.17.1), so a contract test
can replay what the library does with a manifest the ``document.sections`` step produces:

* text extraction - ``culture_library/core/ingest.py``: ``_TextParser``, ``_collapse``,
  ``_html_to_text`` and ``_epub_text`` (xhtml/html members in name order, joined by blank
  lines; the ingest limits are left out);
* chunking - ``culture_library/core/search.py``: ``chunk_text``, ``CHUNK_MAX_CHARS``;
* matching - ``culture_library/eval/generate.py``: ``_norm``, ``MATCH_CHARS``,
  ``_article_urls``, ``_article_excerpt`` and the probe rule of ``_match_articles``;
* manifest validation - ``culture_library/server/routes_sources.py``: ``validate_manifest``,
  ``_entry_problem``, ``_bad_url``, ``MAX_ENTRIES``, ``MAX_EXCERPT``.

If culture-library changes one of these, copy it again here (the spec's open park).
"""

from __future__ import annotations

import io
import re
import zipfile
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

CHUNK_MAX_CHARS = 1000  # core/search.py
MATCH_CHARS = 120  # eval/generate.py
MAX_ENTRIES = 500  # server/routes_sources.py
MAX_EXCERPT = 2000  # server/routes_sources.py
_WS = re.compile(r"\s+")


class _TextParser(HTMLParser):  # core/ingest.py _TextParser
    _SKIP = {"script", "style", "head", "title"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def _collapse(text: str) -> str:  # core/ingest.py _collapse
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_to_text(markup: str) -> str:  # core/ingest.py _html_to_text
    parser = _TextParser()
    parser.feed(markup)
    parser.close()
    return _collapse("".join(parser.parts))


def epub_text(data: bytes) -> str:  # core/ingest.py _epub_text, without the ingest limits
    chunks: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted(
            (i for i in zf.infolist() if i.filename.lower().endswith((".xhtml", ".html", ".htm"))),
            key=lambda i: i.filename,
        )
        for info in names:
            raw = zf.read(info)
            chunks.append(html_to_text(raw.decode("utf-8", errors="replace")))
    return "\n\n".join(c for c in chunks if c)


def chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:  # core/search.py
    """Split on blank lines, packing paragraphs up to ``max_chars``; hard-split long runs."""
    chunks: list[str] = []
    cur = ""

    def flush() -> None:
        nonlocal cur
        if cur.strip():
            chunks.append(cur.strip())
        cur = ""

    for para in re.split(r"\n\s*\n", text):  # NOSONAR - a verbatim copy of the library's rule
        para = para.strip()
        while len(para) > max_chars:
            cut = para.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            flush()
            chunks.append(para[:cut].strip())
            para = para[cut:].strip()
        if len(cur) + len(para) + 2 > max_chars:
            flush()
        cur = f"{cur}\n\n{para}" if cur else para
    flush()
    return [c for c in chunks if c]


def norm(text: str) -> str:  # eval/generate.py _norm
    return _WS.sub(" ", text or "").strip().lower()


def article_urls(article: dict) -> list[str]:  # eval/generate.py _article_urls
    urls = article.get("source_urls") or article.get("urls") or article.get("url") or []
    if isinstance(urls, str):
        urls = [urls]
    return [u for u in urls if isinstance(u, str) and u]


def article_excerpt(article: dict) -> str:  # eval/generate.py _article_excerpt
    return str(article.get("excerpt") or article.get("text") or article.get("title") or "")


def matches(article: dict, chunks: list[str]) -> bool:
    """eval/generate.py ``_match_articles``, for one article: its probe (the first
    ``MATCH_CHARS`` normalised characters of the excerpt) inside one chunk, and URLs."""
    probe = norm(article_excerpt(article))[:MATCH_CHARS]
    hits = [c for c in chunks if probe and probe in norm(c)]
    return bool(hits) and bool(article_urls(article))


def _bad_url(url: Any) -> bool:  # server/routes_sources.py _bad_url
    if not isinstance(url, str):
        return True
    parts = urlsplit(url)
    return parts.scheme not in ("http", "https") or not parts.netloc


def validate_manifest(body: Any) -> str | None:  # server/routes_sources.py validate_manifest
    """A description of the first problem with ``body``, or ``None`` when it is valid."""
    if not isinstance(body, list):
        return "the body must be a JSON list of source entries"
    if len(body) > MAX_ENTRIES:
        return f"at most {MAX_ENTRIES} entries are allowed"
    for i, entry in enumerate(body):
        problem = _entry_problem(entry)
        if problem:
            return f"entry {i}{problem}"
    return None


def _entry_problem(entry: Any) -> str | None:  # server/routes_sources.py _entry_problem
    """The problem with one manifest entry (as a suffix after "entry N"), or None."""
    if not isinstance(entry, dict):
        return " must be an object"
    title = entry.get("article_title")
    if not isinstance(title, str) or not title.strip():
        return ": article_title must be a non-empty string"
    urls = entry.get("source_urls")
    if not isinstance(urls, list) or not urls:
        return ": source_urls must be a non-empty list"
    if any(_bad_url(u) for u in urls):
        return ": source_urls must be http(s) URL strings"
    excerpt = entry.get("excerpt")
    if not isinstance(excerpt, str):
        return ": excerpt must be a string"
    if len(excerpt) > MAX_EXCERPT:
        return f": excerpt is longer than {MAX_EXCERPT} characters"
    return None
