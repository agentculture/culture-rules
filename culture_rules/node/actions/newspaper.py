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

Output ``sections``: the normalised list, every key present. A bad value fails the step
non-retryably, ``invalid_sections: <field path>: <reason>`` (e.g. ``sections[2].queries``), so
the run stops before any search (c12, h11).

``newspaper.filter`` (:class:`NewspaperFilterPort`)
===================================================

Input ``section`` (one section, normalised or raw: it is validated the same way, at path
``section``) and ``results`` (search.web's list). Output ``results``: :func:`filter_results` -
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

One issue per local date (c15, h8). Input ``slot`` (the schedule event's ISO-8601 UTC slot,
``trigger.data.slot``), ``tz`` (IANA zone; the input or ``config.tz``) and ``phase`` (the input
or ``config.phase``): the local date is the slot's date in ``tz`` (zoneinfo, so DST is
right). One document per date in :data:`ISSUES_COLLECTION` (id = the date), written only by
compare-and-set on ``rev``:

* ``claim`` (before delivery). Output ``{deliver, date, reason}``: ``already_delivered``
  (state ``delivered``: deliver false), ``claimed`` (no document, or this run's own claim:
  deliver true), ``claimed_by_another_run`` (another run's claim younger than
  ``config.claim_timeout_s``, default :data:`DEFAULT_CLAIM_TIMEOUT_S`: deliver false) or
  ``taken_over`` (another run's claim older than that - its run died between the claim and
  the record: this run claims it and delivers). The takeover is deliberately simple: a run
  that is merely slow past the timeout could deliver twice, so the timeout is far longer than
  a run takes.
* ``record`` (after delivery): sets state ``delivered`` with ``message_id`` and ``item_id``
  (inputs), ``delivered_by`` and ``delivered_at``, whoever claimed. Output
  ``{recorded, date}``: false when another run already recorded the date; a re-invoked
  record of the same run answers true again.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from culture_rules.engine.actorport import InvocationContext, InvocationResult
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
]

SECTIONS_BUILTIN = "newspaper.sections"
FILTER_BUILTIN = "newspaper.filter"
RENDER_BUILTIN = "newspaper.render"
ISSUE_BUILTIN = "newspaper.issue"
ISSUES_COLLECTION = "newspaper_issues"
VARIABLE = "newspaper_sections"
CLAIM = "claim"
RECORD = "record"
CLAIMED = "claimed"
DELIVERED = "delivered"
DEFAULT_CLAIM_TIMEOUT_S = 3600
DEFAULT_COUNT = 5
MAX_SECTIONS = 20
FRESHNESS = ("pd", "pw", "pm", "py")
_NAME_MAX = 80
_QUERIES_MAX = 5
_QUERY_LEN = 400
_TERMS_MAX = 20
_TERM_LEN = 100
_COUNT_MAX = 20
_ID_LEN = 200
_CAS_TRIES = 3
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST_RE = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*$")

Clock = Callable[[], datetime]


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
        if key not in _VALIDATORS:
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


# ------------------------------------------------------------------ local date


def local_date(slot: Any, tz: Any) -> str:
    """The slot's date (``YYYY-MM-DD``) in the IANA zone ``tz``; ValueError names the field."""
    if not isinstance(slot, str):
        raise ValueError("slot: required, an ISO-8601 time")
    try:
        when = datetime.fromisoformat(slot)
    except ValueError:
        raise ValueError("slot: not an ISO-8601 time") from None
    if when.tzinfo is None:
        raise ValueError("slot: must carry a UTC offset")
    if not isinstance(tz, str) or not tz:
        raise ValueError("tz: required, an IANA zone such as Asia/Jerusalem")
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("tz: unknown IANA zone") from None
    return when.astimezone(zone).date().isoformat()


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
            sections = parse_sections(input.get("sections"))
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
            section = validate_section(input.get("section"), "section")
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


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat(timespec="seconds")


def _age_s(stamp: Any, now: datetime) -> float | None:
    try:
        return (now - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return None


def _delivery_id(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value)[:_ID_LEN]


def _claim_timeout(config: Mapping[str, Any]) -> int:
    value = config.get("claim_timeout_s", DEFAULT_CLAIM_TIMEOUT_S)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("claim_timeout_s: must be a positive integer (seconds)")
    return value


def _decide(doc: Mapping[str, Any] | None, run_id: str, now: datetime, timeout: int) -> str | None:
    """A claim's answer from the current document, or None to (re)claim it."""
    if doc is None:
        return None
    if doc.get("state") == DELIVERED:
        return "already_delivered"
    if doc.get("claimed_by") == run_id:
        return CLAIMED
    age = _age_s(doc.get("claimed_at"), now)
    if age is not None and age < timeout:
        return "claimed_by_another_run"
    return None


class NewspaperIssuePort:
    """``newspaper.issue`` (module doc): one issue per local date."""

    supports_idempotency_key = True  # claim and record are idempotent per run

    def __init__(self, store: Any, *, clock: Clock | None = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime | None,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config or {}
        phase = input.get("phase") or config.get("phase")
        try:
            if phase not in (CLAIM, RECORD):
                raise ValueError(f"phase: must be {CLAIM} or {RECORD}")
            day = local_date(input.get("slot"), input.get("tz") or config.get("tz"))
            timeout = _claim_timeout(config)
        except ValueError as exc:
            return _bad_input(f"bad_input: {exc}")
        if phase == CLAIM:
            return self._claim(day, context.run_id, timeout)
        return self._record(day, context.run_id, input)

    def _cas(self, day: str, doc: Mapping[str, Any] | None, changes: dict[str, Any]) -> bool:
        rev = (doc or {}).get("rev")
        changes = {**changes, "date": day, "rev": (rev or 0) + 1}
        result = self._store.update_if(
            ISSUES_COLLECTION, day, {"rev": rev}, changes, upsert=doc is None
        )
        return bool(result.won)

    def _claim(self, day: str, run_id: str, timeout: int) -> InvocationResult:
        for _ in range(_CAS_TRIES):
            doc = self._store.get(ISSUES_COLLECTION, day)
            now = self._clock()
            reason = _decide(doc, run_id, now, timeout)
            if reason is not None:
                return _claimed(day, reason)
            changes = {"state": CLAIMED, "claimed_by": run_id, "claimed_at": _iso(now)}
            if self._cas(day, doc, changes):
                return _claimed(day, "taken_over" if doc else CLAIMED)
        return _claimed(day, "claimed_by_another_run")

    def _record(self, day: str, run_id: str, input: Mapping[str, Any]) -> InvocationResult:
        changes = {
            "state": DELIVERED,
            "delivered_by": run_id,
            "message_id": _delivery_id(input.get("message_id")),
            "item_id": _delivery_id(input.get("item_id")),
        }
        for _ in range(_CAS_TRIES):
            doc = self._store.get(ISSUES_COLLECTION, day)
            if doc is not None and doc.get("state") == DELIVERED:
                mine = doc.get("delivered_by") == run_id
                return InvocationResult.completed({"recorded": mine, "date": day})
            stamped = {**changes, "delivered_at": _iso(self._clock())}
            if self._cas(day, doc, stamped):
                return InvocationResult.completed({"recorded": True, "date": day})
        return InvocationResult.failed(f"issue_contended: {ISSUES_COLLECTION}/{day}")


def _claimed(day: str, reason: str) -> InvocationResult:
    deliver = reason in (CLAIMED, "taken_over")
    return InvocationResult.completed({"deliver": deliver, "date": day, "reason": reason})
