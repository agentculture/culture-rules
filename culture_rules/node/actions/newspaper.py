"""Built-ins of the daily newspaper (#53): sections, filter, render and the per-date guard.

Four actor-less ``code`` steps (``config.builtin``), served through
:class:`~culture_rules.node.runner.BuiltinCodePort`. Standard-library only.

``newspaper.sections`` (:class:`NewspaperSectionsPort`)
=======================================================

Input ``sections``: the raw JSON string of the variable ``newspaper_sections`` (an already
decoded list is accepted too). It must be a list of 1..:data:`MAX_SECTIONS` objects with the
keys (any other key is refused):

* ``name`` - required, a non-empty string of at most 80 characters;
* ``queries`` - required, 1..5 non-empty strings (at most 400 characters each);
* ``must`` / ``must_not`` - optional lists of terms (default empty): a result is kept only
  when its title or snippet contains every ``must`` term and no ``must_not`` term
  (case-insensitive);
* ``freshness`` - optional, one of ``pd``, ``pw``, ``pm``, ``py`` (Brave's day, week, month,
  year; default null: any age);
* ``count`` - optional integer 1..20 (default :data:`DEFAULT_COUNT`): results kept per section;
* ``site_include`` / ``site_exclude`` - optional lists of hostnames (lower-cased; a hostname
  also matches its subdomains).

Output ``sections``: the normalised list, every key present, plus ``query`` - the section's
queries joined with Brave's ``OR`` operator (:data:`QUERY_JOIN`), the one query its
``search.web`` step sends; a joined query over :data:`MAX_QUERY_CHARS` (Brave's limit) is
refused at ``sections[i].queries``. A bad value fails the step
non-retryably, ``invalid_sections: <field path>: <reason>`` (e.g. ``sections[2].queries``), so
the run stops before any search (c12, h11).

``newspaper.filter`` (:class:`NewspaperFilterPort`)
===================================================

Input ``section`` (one section, normalised or raw: it is validated the same way, at path
``section``; inside the per-section ``for_each`` loop it arrives as the implicit ``item``
port, read when ``section`` is absent; the derived ``query`` key is ignored) and ``results``
(search.web's list). Output ``results``: :func:`filter_results` -
only ``http``/``https`` URLs, deduplicated by URL (first wins), the must/must-not terms and
the site lists applied, capped at the section's ``count``. The workflow runs it in the
per-section loop, between ``search.web`` and ``llm.complete``, so the model sees only what
the issue may show.

``newspaper.render`` (:class:`NewspaperRenderPort`)
===================================================

See :mod:`culture_rules.node.actions.newspaper_render`: the HTML email body and the EPUB,
with the link check. Fails non-retryably on bad input or an exceeded size cap.

``newspaper.issue`` (:class:`NewspaperIssuePort`)
=================================================

See :mod:`culture_rules.node.actions.newspaper_issue`: one issue per local date, with the
lease-fenced ``claim``, ``before_email``, ``before_push`` and ``record`` phases.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actions.newspaper_issue import (
    CLAIM,
    DEFAULT_CLAIM_TIMEOUT_S,
    ISSUES_COLLECTION,
    RECORD,
    NewspaperIssuePort,
    local_date,
)
from culture_rules.node.actions.newspaper_render import (
    EPUB_CAP,
    HTML_CAP,
    RenderError,
    http_host,
    render_issue,
)

__all__ = [
    "CLAIM",
    "DEFAULT_CLAIM_TIMEOUT_S",
    "DEFAULT_COUNT",
    "FILTER_BUILTIN",
    "ISSUES_COLLECTION",
    "ISSUE_BUILTIN",
    "MAX_SECTIONS",
    "RECORD",
    "RENDER_BUILTIN",
    "SECTIONS_BUILTIN",
    "NewspaperFilterPort",
    "NewspaperIssuePort",
    "NewspaperRenderPort",
    "NewspaperSectionsPort",
    "SectionsError",
    "filter_results",
    "local_date",
    "parse_sections",
    "validate_section",
    "with_query",
]

SECTIONS_BUILTIN = "newspaper.sections"
FILTER_BUILTIN = "newspaper.filter"
RENDER_BUILTIN = "newspaper.render"
ISSUE_BUILTIN = "newspaper.issue"
VARIABLE = "newspaper_sections"
DEFAULT_COUNT = 5
MAX_SECTIONS = 20
MAX_QUERY_CHARS = 400  # Brave's own limit (culture_rules.apps.brave)
QUERY_JOIN = " OR "
_DERIVED = ("query",)
FRESHNESS = ("pd", "pw", "pm", "py")
_NAME_MAX = 80
_QUERIES_MAX = 5
_QUERY_LEN = 400
_TERMS_MAX = 20
_TERM_LEN = 100
_COUNT_MAX = 20
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST_RE = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*$")


class SectionsError(ValueError):
    """``newspaper_sections`` (or one section) is invalid; the message starts with the path."""


def _bad_input(message: str) -> InvocationResult:
    return InvocationResult.failed(message, retryable=False)


# ------------------------------------------------------------------ section validation


def _string(value: Any, path: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SectionsError(f"{path}: must be a non-empty string")
    text = value.strip()
    if len(text) > limit:
        raise SectionsError(f"{path}: at most {limit} characters")
    return text


def _strings(value: Any, path: str, most: int, limit: int, *, least: int = 0) -> list[str]:
    if not isinstance(value, list) or not least <= len(value) <= most:
        raise SectionsError(f"{path}: must be a list of {least} to {most} strings")
    return [_string(item, f"{path}[{n}]", limit) for n, item in enumerate(value)]


def _name(value: Any, path: str) -> str:
    return _string(value, path, _NAME_MAX)


def _queries(value: Any, path: str) -> list[str]:
    return _strings(value, path, _QUERIES_MAX, _QUERY_LEN, least=1)


def _terms(value: Any, path: str) -> list[str]:
    return _strings(value, path, _TERMS_MAX, _TERM_LEN)


def _freshness(value: Any, path: str) -> str:
    if value not in FRESHNESS:
        raise SectionsError(f"{path}: must be one of {', '.join(FRESHNESS)}")
    return value


def _count(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= _COUNT_MAX:
        raise SectionsError(f"{path}: must be an integer from 1 to {_COUNT_MAX}")
    return value


def _hosts(value: Any, path: str) -> list[str]:
    hosts = [item.lower() for item in _terms(value, path)]
    for n, host in enumerate(hosts):
        if not _HOST_RE.match(host):
            raise SectionsError(f"{path}[{n}]: must be a hostname such as example.com")
    return hosts


_VALIDATORS: dict[str, Callable[[Any, str], Any]] = {
    "name": _name,
    "queries": _queries,
    "must": _terms,
    "must_not": _terms,
    "freshness": _freshness,
    "count": _count,
    "site_include": _hosts,
    "site_exclude": _hosts,
}
_REQUIRED = ("name", "queries")
_DEFAULTS: dict[str, Callable[[], Any]] = {
    "must": list,
    "must_not": list,
    "freshness": lambda: None,
    "count": lambda: DEFAULT_COUNT,
    "site_include": list,
    "site_exclude": list,
}


def validate_section(raw: Any, path: str) -> dict[str, Any]:
    """One section, normalised (module doc); raises :class:`SectionsError` naming the field."""
    if not isinstance(raw, Mapping):
        raise SectionsError(f"{path}: must be an object")
    for key in raw:
        if key not in _VALIDATORS and key not in _DERIVED:
            raise SectionsError(f"{path}.{str(key)[:40]}: unknown key")
    section: dict[str, Any] = {}
    for key, check in _VALIDATORS.items():
        value = raw.get(key)
        if value is not None:
            section[key] = check(value, f"{path}.{key}")
        elif key in _REQUIRED:
            raise SectionsError(f"{path}.{key}: required")
        else:
            section[key] = _DEFAULTS[key]()
    return section


def parse_sections(raw: Any) -> list[dict[str, Any]]:
    """Parse and validate ``newspaper_sections`` (module doc)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise SectionsError(f"{VARIABLE}: not valid JSON ({exc})") from None
    elif not isinstance(raw, list):
        raise SectionsError(f"{VARIABLE}: missing, or not a JSON string")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_SECTIONS:
        raise SectionsError(f"sections: must be a list of 1 to {MAX_SECTIONS} sections")
    return [validate_section(item, f"sections[{n}]") for n, item in enumerate(raw)]


def with_query(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each section plus its search ``query`` (module doc); SectionsError over the limit."""
    out = []
    for n, section in enumerate(sections):
        query = QUERY_JOIN.join(section["queries"])
        if len(query) > MAX_QUERY_CHARS:
            raise SectionsError(
                f"sections[{n}].queries: joined with OR, at most {MAX_QUERY_CHARS} characters"
            )
        out.append({**section, "query": query})
    return out


# ------------------------------------------------------------------ result filter


def _on_site(host: str, site: str) -> bool:
    return host == site or host.endswith("." + site)


def _keeps(item: Mapping[str, Any], host: str, section: Mapping[str, Any]) -> bool:
    text = " ".join(str(item.get(key) or "") for key in ("title", "snippet")).lower()
    if not all(term.lower() in text for term in section["must"]):
        return False
    if any(term.lower() in text for term in section["must_not"]):
        return False
    include = section["site_include"]
    if include and not any(_on_site(host, site) for site in include):
        return False
    return not any(_on_site(host, site) for site in section["site_exclude"])


def filter_results(results: Any, section: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A normalised section's results: http(s) only, deduplicated, filtered, capped (module doc)."""
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in results if isinstance(results, list) else ():
        url = item.get("url") if isinstance(item, Mapping) else None
        host = http_host(url)
        if host is None or url in seen:
            continue
        seen.add(url)
        if _keeps(item, host, section):
            kept.append(dict(item))
        if len(kept) >= section["count"]:
            break
    return kept


# ------------------------------------------------------------------ ports


class NewspaperSectionsPort:
    """``newspaper.sections`` (module doc)."""

    supports_idempotency_key = True  # pure

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime | None,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        del context
        try:
            sections = with_query(parse_sections(input.get("sections")))
        except SectionsError as exc:
            return _bad_input(f"invalid_sections: {exc}")
        return InvocationResult.completed({"sections": sections})


class NewspaperFilterPort:
    """``newspaper.filter`` (module doc)."""

    supports_idempotency_key = True  # pure

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime | None,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        del context
        try:
            raw = input.get("section", input.get("item"))
            section = validate_section(raw, "section")
        except SectionsError as exc:
            return _bad_input(f"invalid_sections: {exc}")
        results = input.get("results")
        if results is not None and not isinstance(results, list):
            return _bad_input("bad_input: results: must be a list")
        return InvocationResult.completed({"results": filter_results(results, section)})


class NewspaperRenderPort:
    """``newspaper.render`` (:mod:`~culture_rules.node.actions.newspaper_render`)."""

    supports_idempotency_key = True  # pure

    def __init__(self, *, html_cap: int = HTML_CAP, epub_cap: int = EPUB_CAP) -> None:
        self._html_cap, self._epub_cap = html_cap, epub_cap

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime | None,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        del context
        try:
            issue = render_issue(input, html_cap=self._html_cap, epub_cap=self._epub_cap)
        except RenderError as exc:
            return _bad_input(str(exc))
        return InvocationResult.completed(issue)
