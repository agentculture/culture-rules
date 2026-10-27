"""``transcript.render``: one schema-1 transcript and its summary as a deterministic EPUB.

The transcript file is the one :mod:`~culture_rules.node.actions.transcript_file` writes
(``audio.transcribe``). The port reads it, but only from under a transcripts root, and builds an
EPUB 3 (with an EPUB 2 NCX) for the library and its readers. Standard-library only.

Input
=====

``result_path`` (absolute path of the transcript JSON), ``root`` (the transcripts root; the
path must resolve under it, symlinks followed, else ``path_refused`` before anything is read;
a bound actor's ``params.transcripts_root`` or ``output_root`` is a second boundary and the
path must also resolve under it), ``recorded_at`` (ISO-8601 with a zone; the book is dated
with the Asia/Jerusalem local date and time; missing or invalid is ``invalid_request``),
``language`` (``en`` is left-to-right; ``he``, any other code, or none at all is Hebrew,
right-to-left; when empty the transcript's own ``language`` is used) and ``summary``
(llm.complete's ``{text}``, a string, or null when that step was skipped).

The summary text must be a JSON object ``{"summary", "word"}`` (a ``json`` fence or an empty
``<think>`` block is tolerated): ``summary`` is one or two non-blank lines and ``word`` one
short Latin word. Anything else, or no summary, gives the title ``<date> <HHMM> recording``
and the line :data:`SUMMARY_UNAVAILABLE`.

``format`` (``epub``, the default, or ``md``): ``md`` returns the transcript as UTF-8
Markdown in ``content_base64`` with the output ``format: "md"``: the dated title as H1, the
summary, a ``Key facts`` list (the answer's optional ``facts``; omitted when there are none),
then ``Transcript`` with one paragraph per non-empty segment; a right-to-left document is
wrapped in a ``<div dir="rtl">`` block. The answer may also be a mapping that already is
``{summary, word, facts}``; ``action_items`` is ignored here. The Markdown is kept like the
book, as ``book.<sha16>.md`` (+ ``.md.json``), so a re-run sends the same bytes. The
Markdown HTML-escapes ``&``, ``<`` and ``>`` in the word, summary, facts and segments; that
changed its bytes, but nothing had cached Markdown yet, so no migration is needed.

Output
======

Scalars and base64 only: ``content_base64``, ``title`` (``<date> <HHMM> <word>``: the local time
keeps two recordings of one day apart), ``date``, ``word``, ``summary_used``, ``segment_count``
(the non-empty segments rendered). Never the summary or the transcript text. The EPUB is
deterministic: fixed zip times, order and permissions, and an identifier derived from the
transcript's ``audio_sha256``.

One book per transcript file (d4)
=================================

The summary comes from a model that may answer differently on a re-run, which would change the
bytes and give the reader a second book. So the first render keeps the book beside its
transcript, ``book.<first 16 hex of the transcript file's sha256>.epub`` plus a ``.json`` of its
title, date, word, ``summary_used``, ``segment_count`` and both sha256s, each written
atomically. A later run for the same transcript file returns those stored bytes and fields
unchanged, whatever its summary says, so the library deduplicates the upload; a changed transcript
file has a new sha256 and gets a new book. A cache that does not check out is rendered again,
and a folder that cannot be written to still gets its book (only rendered again next time).
Only a book whose summary was used is kept: one rendered without a summary (the summary step
failed or was skipped) is not stored, and a stored one is ignored, so a passing model outage
does not leave "Summary unavailable" on that recording for good.

Caps: the transcript file is read only up to :data:`TRANSCRIPT_CAP` (JSON, with timestamps;
the same cap as ``llm.complete``'s excerpt, so a file one step reads the other reads too),
and the book must fit :data:`EPUB_CAP` (deflated text, typically a fraction of the
transcript's size); a book over it fails ``epub_too_large``.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actions._epub import (
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
from culture_rules.node.actions.transcript_file import (
    TRANSCRIPT_FILE_CAP,
    ExcerptRefused,
    require_absolute,
    resolve_existing,
    write_bytes_atomic,
)

__all__ = [
    "DEFAULT_WORD",
    "EPUB_CAP",
    "SUMMARY_UNAVAILABLE",
    "TRANSCRIPT_RENDER",
    "RenderError",
    "TranscriptRenderPort",
    "parse_answer",
    "parse_summary",
    "render_transcript",
]

TRANSCRIPT_RENDER = "transcript.render"
SUMMARY_UNAVAILABLE = "Summary unavailable."
DEFAULT_WORD = "recording"
EPUB_CAP = 8 << 20
TRANSCRIPT_CAP = TRANSCRIPT_FILE_CAP
INVALID = "invalid_request"
PATH_REFUSED = "path_refused"
TIMEZONE = "Asia/Jerusalem"

_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z-]{1,23}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"^```[A-Za-z]*\s*\n?(.*?)\n?```$", re.DOTALL)
_SUMMARY_MAX_CHARS = 2000
_FACT_MAX_CHARS = 300
_FACTS_MAX = 12

_CSS = (
    "body{font-family:Georgia,serif;line-height:1.5;margin:0 auto;max-width:42em;"
    "padding:0 1em}\n"
    "h1,h2{font-family:Helvetica,Arial,sans-serif}\n"
    ".date{color:#555;font-size:0.9em}\np{margin:0.5em 0}\n"
)


class RenderError(ValueError):
    """The book cannot be rendered; the message starts with its failure code."""


def _fail(code: str, detail: str) -> RenderError:
    return RenderError(f"{code}: {detail}")


# ------------------------------------------------------------------ inputs


def _local_moment(recorded_at: Any) -> datetime:
    if not isinstance(recorded_at, str) or not recorded_at.strip():
        raise _fail(INVALID, "recorded_at is required")
    try:
        moment = datetime.fromisoformat(recorded_at.strip())
    except ValueError as exc:
        raise _fail(INVALID, "recorded_at must be an ISO-8601 time") from exc
    if moment.tzinfo is None:
        raise _fail(INVALID, "recorded_at needs a time zone (for example a trailing Z)")
    return moment.astimezone(ZoneInfo(TIMEZONE))


def _answer_data(value: Any) -> Mapping[str, Any] | None:
    """The answer object: a mapping that already is ``{summary, word, ...}`` (or holds it as
    ``answer``), or the JSON in llm.complete's ``text`` (or a plain string)."""
    if isinstance(value, Mapping):
        if isinstance(value.get("answer"), Mapping):
            return value["answer"]
        if "text" not in value and "summary" in value:
            return value
        value = value.get("text")
    if not isinstance(value, str):
        return None
    text = _THINK_RE.sub("", value).strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _facts(value: Any) -> list[str]:
    """The optional ``facts``: short non-blank strings; anything else is dropped."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        fact = " ".join(INVALID_XML.sub("", item).split())
        if fact and len(fact) <= _FACT_MAX_CHARS:
            out.append(fact)
    return out[:_FACTS_MAX]


def parse_answer(value: Any) -> tuple[list[str], str, list[str]] | None:
    """``(summary lines, word, facts)`` from a recording answer, or None when it does not
    qualify. ``action_items`` is ignored here."""
    data = _answer_data(value)
    if data is None:
        return None
    summary, word = data.get("summary"), data.get("word")
    if isinstance(summary, list) and all(isinstance(ln, str) for ln in summary):
        summary = "\n".join(summary)  # the recording answer keeps its summary as lines
    if not isinstance(summary, str) or not isinstance(word, str):
        return None
    word = word.strip()
    if not _WORD_RE.match(word):
        return None
    lines = [ln.strip() for ln in INVALID_XML.sub("", summary).splitlines() if ln.strip()]
    if not 1 <= len(lines) <= 2 or sum(len(ln) for ln in lines) > _SUMMARY_MAX_CHARS:
        return None
    return lines, word.lower(), _facts(data.get("facts"))


def parse_summary(value: Any) -> tuple[list[str], str] | None:
    """``(summary lines, word)`` from a summary reply, or None when it does not qualify."""
    parsed = parse_answer(value)
    return (parsed[0], parsed[1]) if parsed else None


def _rtl(language: Any, doc: Mapping[str, Any]) -> bool:
    chosen = language if isinstance(language, str) and language.strip() else doc.get("language")
    if isinstance(chosen, str) and chosen.strip():
        return chosen.strip().lower().split("-")[0].split("_")[0] != "en"
    return True  # unlabelled: the zipformer (Hebrew) lane


def _paragraphs(doc: Mapping[str, Any]) -> list[str]:
    segments = doc.get("segments")
    if not isinstance(segments, list):
        raise _fail(INVALID, "transcript has no segments list")
    out: list[str] = []
    for seg in segments:
        text = seg.get("text") if isinstance(seg, Mapping) else None
        if isinstance(text, str) and INVALID_XML.sub("", text).strip():
            out.append(INVALID_XML.sub("", text).strip())
    return out


# ------------------------------------------------------------------ the EPUB


def _xhtml(title: str, body: str, lang: str, direction: str, *, css: bool = True) -> str:
    link = '<link rel="stylesheet" type="text/css" href="style.css"/>' if css else ""
    return (
        f'{XML_DECL}<html xmlns="{XHTML_NS}" xmlns:epub="{EPUB_NS}" '
        f'lang="{lang}" xml:lang="{lang}" dir="{direction}"><head><meta charset="utf-8"/>'
        f"<title>{esc(title)}</title>{link}</head>"
        f'<body dir="{direction}">{body}</body></html>\n'
    )


def _epub(
    uid: str,
    title: str,
    day: str,
    summary_lines: list[str],
    paragraphs: list[str],
    lang: str,
    direction: str,
) -> bytes:
    labels = [title, "Transcript"]
    head = f'<h1>{esc(title)}</h1><p class="date">{esc(day)}</p>' + "".join(
        f"<p>{esc(line)}</p>" for line in summary_lines
    )
    transcript = "<h2>Transcript</h2>" + "".join(f"<p>{esc(p)}</p>" for p in paragraphs)
    entries = list(zip(labels, ("c1", "c2"), strict=True))
    nav = nav_body(entries)
    files = [
        ("META-INF/container.xml", container()),
        (f"{OEBPS}content.opf", opf(uid, title, day, ("c1", "c2"), lang=lang, direction=direction)),
        (f"{OEBPS}nav.xhtml", _xhtml("Contents", nav, lang, direction, css=False)),
        (f"{OEBPS}toc.ncx", ncx(uid, title, entries)),
        (f"{OEBPS}style.css", _CSS),
        (f"{OEBPS}c1.xhtml", _xhtml(title, f'<section id="c1">{head}</section>', lang, direction)),
        (
            f"{OEBPS}c2.xhtml",
            _xhtml("Transcript", f'<section id="c2">{transcript}</section>', lang, direction),
        ),
    ]
    return pack(files)


def _mdesc(text: str) -> str:
    """Escape ``&``, ``<`` and ``>`` (in that order) in model- or transcript-derived text, so
    it cannot inject HTML; Markdown syntax characters are left alone."""
    clean = INVALID_XML.sub("", text)
    return clean.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _markdown(
    title: str, lines: list[str], facts: list[str], paragraphs: list[str], rtl: bool
) -> str:
    """The transcript as Markdown: H1 title, summary, Key facts, Transcript. A right-to-left
    document is wrapped in a ``dir="rtl"`` block (blank lines around it keep the Markdown
    inside rendering)."""
    parts = [f"# {_mdesc(title)}", "\n\n".join(_mdesc(ln) for ln in lines)]
    if facts:
        parts.append("## Key facts\n\n" + "\n".join(f"- {_mdesc(fact)}" for fact in facts))
    body = "\n\n".join(parts)
    body += "\n\n## Transcript\n\n" + "\n\n".join(_mdesc(p) for p in paragraphs)
    if rtl:
        body = f'<div dir="rtl">\n\n{body}\n\n</div>'
    return body + "\n"


def render_transcript(
    doc: Mapping[str, Any],
    *,
    recorded_at: Any,
    language: Any = None,
    summary: Any = None,
    format: str | None = None,  # noqa: A002
) -> dict[str, Any]:
    """Render the schema-1 transcript ``doc`` (an EPUB, or Markdown for ``format="md"``);
    raises :class:`RenderError` (``invalid_request``)."""
    if format in (None, ""):
        format = "epub"  # noqa: A001
    if format not in ("epub", "md"):
        raise _fail(INVALID, "format must be epub or md")
    if not isinstance(doc, Mapping) or doc.get("schema") != 1:
        raise _fail(INVALID, "transcript is not a schema-1 document")
    sha = doc.get("audio_sha256")
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        raise _fail(INVALID, "transcript audio_sha256 must be 64 lowercase hex characters")
    local = _local_moment(recorded_at)
    day = local.date().isoformat()
    paragraphs = _paragraphs(doc)
    parsed = parse_answer(summary)
    lines, word, facts = parsed if parsed else ([SUMMARY_UNAVAILABLE], DEFAULT_WORD, [])
    rtl = _rtl(language, doc)
    lang, direction = ("he", "rtl") if rtl else ("en", "ltr")
    title = f"{day} {local:%H%M} {word}"
    if format == "md":
        data = _markdown(title, lines, facts, paragraphs, rtl).encode("utf-8")
    else:
        uid = f"urn:culture-rules:transcript:{sha}"
        data = _epub(uid, title, day, lines, paragraphs, lang, direction)
    if len(data) > EPUB_CAP:
        raise _fail("epub_too_large", f"the book is over {EPUB_CAP} bytes")
    out = {
        "content_base64": base64.b64encode(data).decode("ascii"),
        "title": title,
        "date": day,
        "word": word,
        "summary_used": parsed is not None,
        "segment_count": len(paragraphs),
    }
    if format == "md":
        out["format"] = "md"
    return out


# ------------------------------------------------------------------ the port


def _resolve_under(path_value: Any, roots: list[Path]) -> Path:
    require_absolute(path_value, "result_path")
    if not roots:
        raise _fail(PATH_REFUSED, "no transcripts root is set")
    path = resolve_existing(path_value, "result_path does not exist")
    if not all(path.is_relative_to(root) for root in roots):
        raise _fail(PATH_REFUSED, "result_path is outside the transcripts root")
    if not path.is_file():
        raise _fail(PATH_REFUSED, "result_path is not a regular file")
    return path


def _root(value: Any, label: str) -> Path:
    require_absolute(value, label)
    return resolve_existing(value, f"{label} does not exist")


def _read_bytes(path: Path) -> bytes:
    try:
        if path.stat().st_size > TRANSCRIPT_CAP:
            raise _fail(INVALID, "transcript file is too large")
        return path.read_bytes()
    except OSError as exc:
        raise _fail(INVALID, "transcript file is not readable JSON") from exc


def _parse(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise _fail(INVALID, "transcript file is not readable JSON") from exc


# ------------------------------------------------------------------ the book cache (d4)

_CACHED_FIELDS = ("title", "date", "word", "summary_used", "segment_count")


def _book_paths(path: Path, raw: bytes, fmt: str = "epub") -> tuple[Path, Path, str]:
    digest = hashlib.sha256(raw).hexdigest()
    stem = f"book.{digest[:16]}"
    if fmt == "md":  # book.<sha16>.md with its record beside it; the EPUB names are unchanged
        return path.parent / f"{stem}.md", path.parent / f"{stem}.md.json", digest
    return path.parent / f"{stem}.epub", path.parent / f"{stem}.json", digest


def _cached_book(path: Path, raw: bytes, fmt: str = "epub") -> dict[str, Any] | None:
    """The book stored for exactly this transcript file, or None when there is none or it
    does not check out (a damaged cache is rendered again)."""
    epub_path, meta_path, digest = _book_paths(path, raw, fmt)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        book = epub_path.read_bytes()
    except (OSError, ValueError, RecursionError):
        return None
    if (
        not isinstance(meta, Mapping)
        or meta.get("summary_used") is not True  # a summaryless book is rendered again
        or meta.get("transcript_sha256") != digest
        or meta.get("book_sha256") != hashlib.sha256(book).hexdigest()
        or any(name not in meta for name in _CACHED_FIELDS)
    ):
        return None
    out = {name: meta[name] for name in _CACHED_FIELDS}
    out["content_base64"] = base64.b64encode(book).decode("ascii")
    if fmt == "md":
        out["format"] = "md"
    return out


def _store_book(path: Path, raw: bytes, out: Mapping[str, Any], fmt: str = "epub") -> None:
    """Keep the rendered book beside its transcript (best effort: a folder that cannot be
    written to still gets its book, it is only rendered again next time). A book without
    its summary is not kept, so a later run with a working summary replaces it."""
    if out.get("summary_used") is not True:
        return
    epub_path, meta_path, digest = _book_paths(path, raw, fmt)
    book = base64.b64decode(out["content_base64"])
    meta = {name: out[name] for name in _CACHED_FIELDS}
    meta.update(transcript_sha256=digest, book_sha256=hashlib.sha256(book).hexdigest())
    with contextlib.suppress(OSError):
        write_bytes_atomic(epub_path, book)  # the book first: the record is only valid with it
        write_bytes_atomic(meta_path, json.dumps(meta, sort_keys=True).encode("utf-8"))


class TranscriptRenderPort:
    """ActorPort for ``transcript.render`` (see the module docstring).

    ``store`` is only needed when a step binds an actor; the actor's ``transcripts_root`` (or
    ``output_root``) is then a second boundary beside the step's ``root``.
    """

    supports_idempotency_key = True  # reads one file, writes nothing

    def __init__(self, store: Any = None) -> None:
        self._store = store

    def _actor_root(self, input: Mapping[str, Any], context: InvocationContext) -> Path | None:
        actor_id = input.get("actor") or getattr(context, "actor", None)
        if not isinstance(actor_id, str) or not actor_id or self._store is None:
            return None
        from culture_rules.node.actions.machine import load_bound_actor  # noqa: PLC0415

        actor, _doc, failure = load_bound_actor(self._store, input, context)
        if failure is not None or actor is None:
            raise _fail("actor_not_found", repr(actor_id))
        for name in ("transcripts_root", "output_root"):
            value = actor.params.get(name)
            if value not in (None, ""):
                return _root(value, f"actor {name}")
        return None

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime | None,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        try:
            roots: list[Path] = []
            step_root = input.get("root")
            if step_root not in (None, ""):
                roots.append(_root(step_root, "root"))
            actor_root = self._actor_root(input, context)
            if actor_root is not None:
                roots.append(actor_root)
            path = _resolve_under(input.get("result_path"), roots)  # before anything is read
            fmt = input.get("format") or "epub"
            if fmt not in ("epub", "md"):
                raise _fail(INVALID, "format must be epub or md")
            raw = _read_bytes(path)
            cached = _cached_book(path, raw, fmt)
            if cached is not None:
                return InvocationResult.completed(cached)
            out = render_transcript(
                _parse(raw),
                recorded_at=input.get("recorded_at"),
                language=input.get("language"),
                summary=input.get("summary"),
                format=fmt,
            )
        except (RenderError, ExcerptRefused) as exc:  # both read "<code>: <detail>"
            return InvocationResult.failed(str(exc), retryable=False)
        _store_book(path, raw, out, fmt)
        return InvocationResult.completed(out)
