"""The host of a web link, only when it is the host a browser would open (standard library).

``urlsplit`` and browsers disagree on some authorities: a backslash (browsers read it as a
slash, so ``http://evil.com\\@good.com/x`` opens evil.com while ``urlsplit`` reports
good.com), userinfo (``user@host``), percent-escapes and non-ASCII letters in the authority,
and a non-numeric port. :func:`browser_host` answers ``None`` for every such URL, so a domain
allowlist checked against its answer is checked against the host a reader is sent to.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import SplitResult, urlsplit

__all__ = ["browser_host"]

_SCHEMES = ("http", "https")
#: Control characters, lone surrogates and non-characters (whitespace is ``_SPACE``'s).
_CONTROL = re.compile(r"[\x00-\x1f\x7f\ud800-\udfff\ufffe\uffff]")
_SPACE = re.compile(r"\s")
#: A host a browser opens as written: ASCII letters, digits, ``-``, ``_``, ``.`` only.
_PLAIN_HOST = re.compile(r"[a-z0-9_.-]+", re.ASCII)
#: The characters of an IPv6 address as ``urlsplit`` reports it (brackets removed); one
#: character class, so the match is linear (it must also hold a colon, checked apart).
_IPV6_CHARS = re.compile(r"[0-9a-f:.]+", re.ASCII)
#: Authority characters on which ``urlsplit`` and a browser may disagree about the host.
_AMBIGUOUS_AUTHORITY = ("@", "%")


def _numeric_port(parts: SplitResult) -> bool:
    """No port, or a port that is a number (``.port`` raises on anything else)."""
    try:
        return parts.port is None or parts.port >= 0
    except ValueError:
        return False


def browser_host(url: Any) -> str | None:
    """The lower-cased host of an ``http``/``https`` URL, else None: only when ``urlsplit``'s
    host is the host a browser would open (module doc)."""
    if not isinstance(url, str) or not url or "\\" in url:
        return None
    if _CONTROL.search(url) or _SPACE.search(url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in _SCHEMES or not host or not _numeric_port(parts):
        return None
    if not parts.netloc.isascii():
        return None
    if any(mark in parts.netloc for mark in _AMBIGUOUS_AUTHORITY):
        return None
    if _PLAIN_HOST.fullmatch(host) or (":" in host and _IPV6_CHARS.fullmatch(host)):
        return host
    return None
