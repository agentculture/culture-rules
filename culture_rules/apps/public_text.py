"""Text from an agent or a bridge, made inert for a public GitHub comment (d26).

The PR fixer's status comment (:mod:`culture_rules.node.fixer_status`) relays text the
engine did not write: the agent's free status notes, its fix summary, a reviewer's findings
inside a hand-back. Posted as the App, such text could ping people, smuggle links or
images, forge headings or sections, break the comment with HTML or a fence, or leak a
credential. So untrusted text is never "cleaned" Markdown: it is **normalized, checked,
then escaped** into plain characters (pure, standard-library only):

1. :func:`normalize` - Unicode NFKC; control and format characters (zero-width, bidi)
   removed; every URL (``scheme://...``, ``//host...``, ``www.``) dropped; whitespace
   collapsed (one line for a note).
2. :func:`withheld` - the text is refused when any of its views - raw, entity-decoded,
   normalized, and the *stripped view* of each (tags, backslashes, backticks, emphasis and
   pipes removed, so ``ghp_<b></b>...`` reassembles) - looks like a credential
   (:func:`looks_secret`: known token formats, private key blocks, long random strings),
   or when a compacted view holds a **known secret** of this
   process (:func:`known_secret_in`: the value, any 12-character piece of it, or a piece
   of its hex, base64 or urlsafe-base64 encoding). Callers pass the known values
   (:func:`culture_rules.actors.secrets.known_values`).
3. :func:`escape` - every Markdown metacharacter is backslash-escaped, ``<``, ``>`` and
   ``&`` become entities (so ``<!--`` can never appear), ``@`` is followed by a zero-width
   space (no mention pings), and leading spaces are dropped (no indented code). The result
   renders as the same words, inert: no link, image, heading, list, table, fence or HTML.

:func:`clean_note` normalizes and checks one status note (``None`` when refused);
:func:`inert_block` does all three for multi-line text (``[withheld]`` when refused).
:func:`status_note` reads ``STATUS: <note>`` out of a bridge progress note. Engine facts
and links never pass through here: the caller renders them from validated values.

Residual risk, plainly: these checks stop accidents and the obvious leaks. An agent
determined to exfiltrate through an encoding of its own choosing cannot be fully stopped by
any filter; the known-secret check covers only the values the checking process holds. What
the fixer's agent holds is its bridge token, scoped to its own bridge.
"""

from __future__ import annotations

import base64
import html
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from typing import Any

__all__ = [
    "NOTE_CAP",
    "STATUS_MARK",
    "WINDOW",
    "WITHHELD",
    "clean_note",
    "escape",
    "inert_block",
    "known_secret_in",
    "looks_secret",
    "normalize",
    "status_note",
    "withheld",
]

NOTE_CAP = 200
"""The longest status note relayed, in characters (an ellipsis marks a cut)."""
WITHHELD = "[withheld]"
"""What a refused untrusted section becomes."""
STATUS_MARK = "STATUS:"
"""The marker an agent writes before a status note (``echo "STATUS: <note>"``)."""
ELLIPSIS = "…"
WINDOW = 12
"""A known secret is matched by any piece of this many characters (of it or an encoding)."""
_MIN_KNOWN = 8  # shorter values are too common to match on

# Known credential formats (as scripts/scan-secrets.py, plus fine-grained PATs and STS keys).
_KNOWN_TOKENS = (
    re.compile(r"gh[pousr]_\w{30,}"),
    re.compile(r"github_pat_\w{40,}"),
    re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[\w-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"-{3,} ?BEGIN [A-Z ]{0,20}PRIVATE KEY"),
)
_WORD = re.compile(r"[A-Za-z0-9+=_-]{32,}")  # "/" splits: a path is not one word
_HEX = re.compile(r"(?:[A-Za-z]{1,10}[-_])?[0-9a-fA-F]+")  # a digest, or an id like run-<hex>
_MIN_ENTROPY = 3.5  # bits per character: random base64 is near 6, English words near 3

_URL = re.compile(r"(?:\b[a-z][a-z0-9+.-]{0,20}:)?//\S*|\bwww\.\S*", re.IGNORECASE)
_SPACES = re.compile(r"[ \f\v]+")
_BLANKS = re.compile(r"\n{3,}")
_TAG = re.compile(r"<[^<>]{0,500}>")
_STRIP = re.compile(r"[\\`*~|]")
_COMPACT = re.compile(r"[^a-z0-9+/_-]")
_METACHARS = re.compile(r"([\\`*_{}\[\]()#+\-.!|~=:\"'$^])")


# --------------------------------------------------------------------------- normalize


def _visible(ch: str) -> bool:
    """Keep printable characters and line breaks; drop control and format ones."""
    return ch == "\n" or unicodedata.category(ch)[0] != "C"


def normalize(text: Any, *, one_line: bool = False) -> str:
    """NFKC, control/format characters and every URL removed, whitespace collapsed."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\t", " ")
    text = "".join(ch for ch in text if _visible(ch))
    text = _URL.sub("", text)
    if one_line:
        text = text.replace("\n", " ")
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


# --------------------------------------------------------------------------- secrets


def _entropy(word: str) -> float:
    counts = Counter(word)
    size = len(word)
    return -sum(n / size * math.log2(n / size) for n in counts.values())


def _random_word(word: str) -> bool:
    """A long run of token characters that looks random: letters and digits mixed, high
    entropy, and not a hex digest or a prefixed hex id (a SHA or a run id is no secret)."""
    if _HEX.fullmatch(word):
        return False
    has_digit = any(c.isdigit() for c in word)
    has_alpha = any(c.isalpha() for c in word)
    return has_digit and has_alpha and _entropy(word) >= _MIN_ENTROPY


def _stripped(text: str) -> str:
    """The view in which markup between a token's characters no longer splits it."""
    return _STRIP.sub("", _TAG.sub("", text))


def _heuristic(view: str) -> bool:
    if any(p.search(view) for p in _KNOWN_TOKENS):
        return True
    return any(_random_word(m.group()) for m in _WORD.finditer(view))


def _views(text: str) -> tuple[str, ...]:
    """Every view a check runs on: raw, entity-decoded (an escaped body), normalized, and
    the stripped view of each (markup removed, so it cannot split a token; the unstripped
    views keep what sits inside angle brackets)."""
    decoded = html.unescape(text)
    plain = (text, decoded, normalize(text), normalize(decoded))
    return (*plain, *(_stripped(v) for v in plain))


def looks_secret(text: Any) -> bool:
    """Whether any view of ``text`` (:func:`_views`) holds anything shaped like a
    credential."""
    if not isinstance(text, str):
        return False
    return any(_heuristic(view) for view in _views(text))


def _compact(text: str) -> str:
    return _COMPACT.sub("", text.lower())


def _encodings(value: str) -> list[str]:
    """The value and its hex, base64 and urlsafe-base64 forms (each from three byte
    offsets, the unstable last characters dropped), compacted."""
    raw = value.encode("utf-8")
    out = [value, raw.hex()]
    for i in range(3):
        for enc in (base64.b64encode, base64.urlsafe_b64encode):
            text = enc(raw[i:]).decode("ascii").rstrip("=")
            out.append(text[:-3] if len(text) > WINDOW + 3 else text)
    return [_compact(e) for e in out]


_PIECES: dict[tuple[frozenset[str], frozenset[str]], tuple[frozenset[str], frozenset[str]]] = {}
_PUBLIC_COMPACT: frozenset[str] = frozenset()  # declare_public()


def _pieces(values: frozenset[str]) -> tuple[frozenset[str], frozenset[str]]:
    """``(windows, whole)``: every 12-character piece of each known value's forms, and the
    shorter forms matched whole. Cached per set of values (never logged)."""
    public = _PUBLIC_COMPACT
    cached = _PIECES.get((values, public))
    if cached is not None:
        return cached
    windows: set[str] = set()
    whole: set[str] = set()
    for value in values:
        for form in _encodings(value):
            if len(form) >= WINDOW:
                windows.update(form[i : i + WINDOW] for i in range(len(form) - WINDOW + 1))
            elif len(form) >= _MIN_KNOWN:
                whole.add(form)
    windows = {w for w in windows if not any(w in p for p in public)}
    whole = {w for w in whole if not any(w in p for p in public)}
    if len(_PIECES) > 8:
        _PIECES.clear()
    _PIECES[(values, public)] = (frozenset(windows), frozenset(whole))
    return _PIECES[(values, public)]


def declare_public(*texts: str) -> None:
    """Declare text the engine itself publishes (its link base, its fixed wording). A piece
    of a known secret that also occurs in it is public already and stops counting as a hit;
    the value's other pieces, and the value whole, still do. Declarations only grow."""
    global _PUBLIC_COMPACT  # noqa: PLW0603 - a process-wide, append-only set
    added = {_compact(unicodedata.normalize("NFKC", t)) for t in texts if isinstance(t, str)}
    _PUBLIC_COMPACT = _PUBLIC_COMPACT | frozenset(c for c in added if c)


def known_secret_in(text: Any, known: Iterable[str]) -> bool:
    """Whether ``text``, compacted (lower-cased, everything but token characters dropped,
    so spaces, markup and note boundaries cannot split a value), holds a known secret: a
    12-character piece of a value or of its hex/base64 forms, or a short value whole."""
    if not isinstance(text, str):
        return False
    values = frozenset(v for v in known if isinstance(v, str) and len(v) >= _MIN_KNOWN)
    if not values:
        return False
    windows, whole = _pieces(values)
    views = {_compact(unicodedata.normalize("NFKC", v)) for v in _views(text)}
    return any(_holds(compact, windows, whole) for compact in views)


def _holds(compact: str, windows: frozenset[str], whole: frozenset[str]) -> bool:
    if any(w in compact for w in whole):
        return True
    return any(compact[i : i + WINDOW] in windows for i in range(len(compact) - WINDOW + 1))


def withheld(text: Any, known: Iterable[str] = ()) -> bool:
    """Whether untrusted ``text`` must not be relayed (module doc, step 2)."""
    return looks_secret(text) or known_secret_in(text, known)


# --------------------------------------------------------------------------- escape


def escape(text: str) -> str:
    """Untrusted text as inert Markdown (module doc, step 3)."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _METACHARS.sub(r"\\\1", text)
    text = text.replace("@", "@​")
    return "\n".join(line.lstrip() for line in text.split("\n"))


def _cap(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - 1].rstrip() + ELLIPSIS


def clean_note(text: Any, cap: int = NOTE_CAP, known: Iterable[str] = ()) -> str | None:
    """One status note normalized to one line and capped, or ``None`` when empty or
    refused (the raw text and the note are both checked). Stored like this; it is
    :func:`escape` d when rendered."""
    if not isinstance(text, str) or withheld(text, known):
        return None
    note = _cap(normalize(text, one_line=True), cap)
    return note if note and not withheld(note, known) else None


def inert_block(text: Any, cap: int, known: Iterable[str] = ()) -> str:
    """Multi-line untrusted text normalized, capped, checked and escaped; ``[withheld]``
    when refused, ``""`` when empty."""
    block = _cap(normalize(text), cap)
    if not block:
        return ""
    if withheld(text, known) or withheld(block, known):
        return WITHHELD
    return escape(block)


# --------------------------------------------------------------------------- STATUS: notes


def _quoted(rest: str, quote: str) -> str:
    end = rest.find(quote)
    return rest if end < 0 else rest[:end]


_TRAILERS = {"]": ("[", 200), ")": ("(", 300)}  # closer: its opener, the longest inside


def _strip_trailers(text: str) -> str:
    """``text`` less its trailing run of whitespace-led ``[...]`` / ``(...)`` groups (each with
    no bracket of its own kind inside), scanned from the end in linear time; unchanged when
    it has none."""
    end = _space_before(text, len(text))
    cut = None
    while end and text[end - 1] in _TRAILERS:
        opener, longest = _TRAILERS[text[end - 1]]
        start = text.rfind(opener, max(0, end - 2 - longest), end - 1)
        if start < 1 or text.find(text[end - 1], start + 1, end - 1) >= 0:
            break
        if not text[start - 1].isspace():
            break
        end = _space_before(text, start)
        cut = end
    return text if cut is None else text[:cut]


def _space_before(text: str, end: int) -> int:
    """Where the whitespace run that ends at ``end`` starts."""
    while end and text[end - 1].isspace():
        end -= 1
    return end


def status_note(progress: Any) -> str | None:
    """The text after ``STATUS:`` in one bridge progress note (module doc), or ``None``.

    A quoted note (``echo "STATUS: x"``) ends at its closing quote; an unquoted one runs
    to the end, less the tool title's trailing ``[in dir]`` / ``[timeout: ...]`` /
    ``(description)`` parts."""
    if not isinstance(progress, str):
        return None
    at = progress.find(STATUS_MARK)
    if at < 0:
        return None
    rest = progress[at + len(STATUS_MARK) :]
    before = progress[at - 1] if at > 0 else ""
    if before in ("'", '"'):
        rest = _quoted(rest, before)
    else:
        rest = _strip_trailers(rest)
    note = rest.strip()
    return note or None
