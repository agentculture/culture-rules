"""Text from an agent or a bridge, made safe for a public GitHub comment (d26).

The PR fixer's status comment (:mod:`culture_rules.node.fixer_status`) relays text the
engine did not write: the agent's free status notes, its fix summary, a reviewer's findings
inside a hand-back. Posted as the App, such text could ping people, smuggle links or
images, break the comment's layout with HTML or an open code fence, or leak a credential
the agent saw. Two cleaners, both pure and standard-library only:

:func:`clean_note` (strict, for one status note)
    one line, capped at :data:`NOTE_CAP` characters; HTML tags, images, code spans and
    fences removed; a markdown link keeps only its text and a bare URL becomes ``[link]``;
    ``@name`` is neutralised with a zero-width space so it never pings; a note carrying
    anything token-shaped (:func:`looks_secret`) is **dropped whole** (``None``).

:func:`clean_block` (for multi-line text: a summary, a chain's final section)
    keeps lines; the same markup rules, except that bare URLs may be kept (the engine's
    own run link) and token-shaped text is replaced by :data:`REDACTED`; an unbalanced
    code fence is closed; capped.

:func:`status_note` reads the agent's ``STATUS: <note>`` out of one bridge ``progress``
note (the bridge describes a shell tool call by its title, e.g. ``tool_call: Shell: echo
"STATUS: fixing the test"``); the result still goes through :func:`clean_note`.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

__all__ = [
    "BLOCK_CAP",
    "NOTE_CAP",
    "REDACTED",
    "STATUS_MARK",
    "clean_block",
    "clean_note",
    "looks_secret",
    "status_note",
]

NOTE_CAP = 200
"""The longest status note relayed, in characters (an ellipsis marks a cut)."""
BLOCK_CAP = 2000
"""The default cap of :func:`clean_block`."""
REDACTED = "[redacted]"
"""What a token-shaped run of text becomes in a block."""
STATUS_MARK = "STATUS:"
"""The marker an agent writes before a status note (``echo "STATUS: <note>"``)."""
ELLIPSIS = "…"
LINK_WORD = "[link]"

# Known credential formats (as scripts/scan-secrets.py, plus fine-grained PATs and STS keys).
_KNOWN_TOKENS = (
    re.compile(r"\bgh[pousr]_\w{30,}"),
    re.compile(r"\bgithub_pat_\w{40,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[\w-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]{0,20}PRIVATE KEY-----"),
)
_WORD = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_HEX = re.compile(r"[0-9a-fA-F]+")
_MIN_ENTROPY = 3.5  # bits per character: random base64 is near 6, English words near 3

_TAG = re.compile(r"<[^<>]{0,500}>")
_IMAGE = re.compile(r"!\[[^\]\n]{0,300}\]\([^)\n]{0,2000}\)")
_LINK = re.compile(r"\[([^\]\n]{0,300})\]\([^)\n]{0,2000}\)")
_URL = re.compile(r"\b(?:https?|ftp)://[^\s<>()\[\]]+", re.IGNORECASE)
_WWW = re.compile(r"\bwww\.[^\s<>()\[\]]+", re.IGNORECASE)
_MENTION = re.compile(r"(?<![\w.+-])@(?=[A-Za-z0-9])")
_SPACES = re.compile(r"\s+")
_FENCE = re.compile(r"^\s{0,3}(?:`{3,}|~{3,})", re.MULTILINE)


def _entropy(word: str) -> float:
    counts = Counter(word)
    size = len(word)
    return -sum(n / size * math.log2(n / size) for n in counts.values())


def _random_word(word: str) -> bool:
    """A long run of token characters that looks random: letters and digits mixed, high
    entropy, and not a hex digest (a commit SHA is not a secret)."""
    if _HEX.fullmatch(word):
        return False
    has_digit = any(c.isdigit() for c in word)
    has_alpha = any(c.isalpha() for c in word)
    return has_digit and has_alpha and _entropy(word) >= _MIN_ENTROPY


def _secret_spans(text: str) -> list[tuple[int, int]]:
    spans = [m.span() for pattern in _KNOWN_TOKENS for m in pattern.finditer(text)]
    spans += [m.span() for m in _WORD.finditer(text) if _random_word(m.group())]
    return sorted(spans)


def looks_secret(text: Any) -> bool:
    """Whether ``text`` holds anything shaped like a credential (module doc)."""
    return isinstance(text, str) and bool(_secret_spans(text))


def _redact(text: str) -> str:
    out, last = [], 0
    for start, end in _secret_spans(text):
        if start < last:
            continue
        out += [text[last:start], REDACTED]
        last = end
    return "".join(out) + text[last:]


def _cap(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - 1].rstrip() + ELLIPSIS


def _markup(text: str, *, keep_urls: bool) -> str:
    """HTML, images, links, code spans and mentions neutralised (lines kept)."""
    text = _IMAGE.sub("", text)
    text = _TAG.sub("", text)
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = _LINK.sub(r"\1", text)
    if not keep_urls:
        text = _WWW.sub(LINK_WORD, _URL.sub(LINK_WORD, text))
    return _MENTION.sub("@​", text)


def clean_note(text: Any, cap: int = NOTE_CAP) -> str | None:
    """One status note made safe (module doc), or ``None`` when nothing safe is left."""
    if not isinstance(text, str) or looks_secret(text):
        return None
    text = text.replace("&lt;", "<").replace("&gt;", ">")  # idempotent on its own output
    text = _markup(text, keep_urls=False).replace("`", "").replace("~~~", "")
    text = _SPACES.sub(" ", text).strip()
    if not text or looks_secret(text):
        return None
    return _cap(text, cap)


def clean_block(text: Any, cap: int = BLOCK_CAP, *, keep_urls: bool = True) -> str:
    """Multi-line text made safe (module doc): ``""`` for anything that is not text."""
    if not isinstance(text, str):
        return ""
    text = _markup(_redact(text), keep_urls=keep_urls).strip()
    text = _cap(text, cap - 4)
    if len(_FENCE.findall(text)) % 2:
        text += "\n```"
    return text


def _quoted(rest: str, quote: str) -> str:
    end = rest.find(quote)
    return rest if end < 0 else rest[:end]


_TRAILERS = re.compile(r"(?:\s+\[[^\[\]]{0,200}\]|\s+\([^()]{0,300}\))+\s*$")


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
        rest = _TRAILERS.sub("", rest)
    note = rest.strip()
    return note or None
