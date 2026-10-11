"""Brave Search web client: one read, ``GET /res/v1/web/search``, normalised and capped.

An app client like :mod:`culture_rules.apps.jira`, outside the core model and engine. It talks
only to :data:`BRAVE_HOST`: the URL is a constant, there is no base-URL override, and every
request is checked against the host before it is sent. Standard library only; the transport is
the GitHub client's (redirects never followed, the body read up to a byte cap) and injectable:
``transport(method, url, headers, body, timeout) -> (status, body_bytes)``.

The API key rides only in the ``X-Subscription-Token`` header; it is never logged, repr'd or
put in an error message.

Parameters are checked before any call: ``query`` (non-empty, at most
:data:`MAX_QUERY_CHARS`), ``count`` (an int >= 1, clamped to :data:`MAX_COUNT`), ``freshness``
(one of :data:`FRESHNESS`), ``country`` (two letters, or ``ALL``) and ``lang`` (``en``,
``pt-br``, ``zh-hans``...). A bad one raises a non-retryable :class:`BraveError`.

Each result of ``web.results[]`` becomes ``{title, url, snippet, age, source}``: tags are
stripped from title and description and entities unescaped; ``source`` is ``profile.name``,
else ``meta_url.hostname``, else the URL's host; every field is cut to a fixed length; a result
whose URL is not http(s) or is longer than :data:`MAX_URL_CHARS` is dropped. At most ``count``
results come back, so a large response cannot overflow a run document.

HTTP failures: 429 and 5xx are retryable, every other 4xx (401, 403, 422...) is not; a network
failure or an unreadable body is retryable.
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse
from collections.abc import Mapping
from typing import Any

from culture_rules.apps.github import Transport, urllib_transport

__all__ = [
    "BRAVE_HOST",
    "BRAVE_SEARCH_URL",
    "DEFAULT_COUNT",
    "FRESHNESS",
    "MAX_COUNT",
    "BraveError",
    "BraveSearch",
]

log = logging.getLogger(__name__)

BRAVE_HOST = "api.search.brave.com"
BRAVE_SEARCH_URL = f"https://{BRAVE_HOST}/res/v1/web/search"

DEFAULT_COUNT = 10
MAX_COUNT = 20  # Brave's own maximum per request
MAX_QUERY_CHARS = 400  # Brave's own maximum
FRESHNESS = frozenset({"pd", "pw", "pm", "py"})

MAX_TITLE_CHARS = 300
MAX_SNIPPET_CHARS = 1000
MAX_URL_CHARS = 2048
MAX_AGE_CHARS = 64
MAX_SOURCE_CHARS = 200

_TIMEOUT_S = 15
_COUNTRY_RE = re.compile(r"^(?:[A-Za-z]{2}|ALL)$")
_LANG_RE = re.compile(r"^[A-Za-z]{2}(?:-[A-Za-z]{2,4})?$")
_TAG_RE = re.compile(r"<[^>]*>")
_SPACE_RE = re.compile(r"\s+")


class BraveError(Exception):
    """A Brave search failed. ``code`` is machine-readable; messages never hold the key."""

    def __init__(self, code: str, message: str = "", *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.retryable = retryable


def _text(value: Any, cap: int) -> str:
    """Plain text from an HTML-ish snippet: tags stripped, entities unescaped, capped."""
    if not isinstance(value, str):
        return ""
    plain = html.unescape(_TAG_RE.sub("", value))
    return _SPACE_RE.sub(" ", plain).strip()[:cap]


def _http_url(value: Any) -> str | None:
    """``value`` when it is a well-formed ``http``/``https`` URL with a host, else None.

    Result URLs are untrusted: ``urlsplit`` raises ``ValueError`` on some of them (an
    unclosed IPv6 bracket such as ``https://[bad``), and ``.hostname``/``.port`` raise on a
    bad port. Any such error drops that one result, so a single malformed URL never aborts
    the whole search (Codex r1 #6).
    """
    if not isinstance(value, str) or len(value) > MAX_URL_CHARS:
        return None
    try:
        parts = urllib.parse.urlsplit(value)
        host, _port = parts.hostname, parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    return value


def _source(item: Mapping[str, Any], url: str) -> str:
    for key, field in (("profile", "name"), ("meta_url", "hostname")):
        holder = item.get(key)
        name = holder.get(field) if isinstance(holder, Mapping) else None
        if isinstance(name, str) and name.strip():
            return _text(name, MAX_SOURCE_CHARS)
    return (urllib.parse.urlsplit(url).hostname or "")[:MAX_SOURCE_CHARS]


def normalise(item: Any) -> dict[str, Any] | None:
    """One ``web.results[]`` item as ``{title, url, snippet, age, source}``, or None to drop."""
    if not isinstance(item, Mapping):
        return None
    url = _http_url(item.get("url"))
    if url is None:
        return None
    age = item.get("age")
    return {
        "title": _text(item.get("title"), MAX_TITLE_CHARS),
        "url": url,
        "snippet": _text(item.get("description"), MAX_SNIPPET_CHARS),
        "age": age[:MAX_AGE_CHARS] if isinstance(age, str) else None,
        "source": _source(item, url),
    }


def _check_query(query: Any) -> str:
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise BraveError("bad_query", f"a non-empty string of at most {MAX_QUERY_CHARS} chars")
    return query


def _check_count(count: Any) -> int:
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise BraveError("bad_count", "an integer of at least 1")
    return min(count, MAX_COUNT)


def _check_optional(value: Any, code: str, ok: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not ok(value):
        raise BraveError(code)
    return value


def build_query(
    query: Any,
    count: Any = DEFAULT_COUNT,
    freshness: Any = None,
    country: Any = None,
    lang: Any = None,
) -> dict[str, str]:
    """The checked Brave query string parameters (raises :class:`BraveError` on a bad one)."""
    params = {"q": _check_query(query), "count": str(_check_count(count))}
    optional = (
        ("freshness", _check_optional(freshness, "bad_freshness", FRESHNESS.__contains__)),
        ("country", _check_optional(country, "bad_country", _COUNTRY_RE.match)),
        ("search_lang", _check_optional(lang, "bad_lang", _LANG_RE.match)),
    )
    params.update({name: value for name, value in optional if value is not None})
    return params


class BraveSearch:
    """Brave's web search with one API key (module doc)."""

    def __init__(self, *, api_key: str, transport: Transport | None = None) -> None:
        self._key = api_key
        self._transport = transport or urllib_transport

    def __repr__(self) -> str:  # never expose the key
        return f"BraveSearch(host={BRAVE_HOST!r})"

    def _get(self, params: Mapping[str, str]) -> Mapping[str, Any]:
        if not self._key:
            raise BraveError("not_configured", "no API key")
        url = f"{BRAVE_SEARCH_URL}?{urllib.parse.urlencode(params)}"
        if urllib.parse.urlsplit(url).netloc != BRAVE_HOST:  # defence in depth: constant URL
            raise BraveError("bad_host")
        headers = {
            "X-Subscription-Token": self._key,
            "Accept": "application/json",
            "User-Agent": "culture-rules",
        }
        try:
            status, raw = self._transport("GET", url, headers, None, _TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - network failure is retryable; text withheld
            raise BraveError("network_error", type(exc).__name__, retryable=True) from None
        if status >= 400:
            log.warning("brave search failed: http %s", status)
            raise BraveError(f"http_{status}", retryable=status >= 500 or status == 429)
        try:
            data = json.loads(raw.decode())
        except ValueError:  # UnicodeDecodeError derives from ValueError
            raise BraveError("bad_response", retryable=True) from None
        if not isinstance(data, Mapping):
            raise BraveError("bad_response", retryable=True)
        return data

    def search(
        self,
        query: Any,
        *,
        count: Any = DEFAULT_COUNT,
        freshness: Any = None,
        country: Any = None,
        lang: Any = None,
    ) -> list[dict[str, Any]]:
        """Up to ``count`` (at most :data:`MAX_COUNT`) normalised results for ``query``."""
        params = build_query(query, count, freshness, country, lang)
        data = self._get(params)
        web = data.get("web")
        items = web.get("results") if isinstance(web, Mapping) else None
        if not isinstance(items, list):
            return []
        limit = int(params["count"])
        results = [r for r in (normalise(item) for item in items) if r is not None]
        return results[:limit]
