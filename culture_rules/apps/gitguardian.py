"""GitGuardian's pull-request check, read as data (d25). Standard-library only; no I/O.

GitGuardian reports the hardcoded secrets it finds in a pull request through a GitHub check
run of its App (slug :data:`APP_SLUG`): conclusion ``failure``, a title such as ``1 secret
uncovered!`` and, in ``output.text``, a markdown table::

    | GitGuardian id | GitGuardian status | Secret | Commit | Filename | |
    | -------------- | ------------------ | ------ | ------ | -------- | ---- |
    | [1234](<incident>) | Triggered | Generic Password | 0ef2… | a.yaml | [View secret](<diff>R8) |

(the incident link is on ``dashboard.gitguardian.com``, the "View secret" link is the GitHub
diff of the commit, ``...#diff-<hash>R<line>``).

The ``Secret`` column is the detector (the secret's *type*), never its value; GitGuardian
puts no value in the table. :func:`parse_findings` reads each row into ``{incident,
incident_url, status, type, commit, file, line}`` and keeps nothing else: the line is the
``R<n>`` anchor of the "View secret" link (``None`` without one), the incident link is kept
only when it is an ``https`` URL on a ``gitguardian.com`` host, every text cell is cleaned
of markdown that could break out of a table cell or a code span (backticks, pipes, angle
brackets, links), and a commit is kept only when it is 7 to 40 hex digits.

The parse is tolerant: columns are found by their header (any order, extra whitespace,
missing columns read as ``None``), several tables are read in turn, separator rows and
anything outside a table are skipped, and a row with none of incident, type, file and commit
is dropped. :func:`check_state` folds the App's check runs of one commit into ``absent`` /
``pending`` / ``failing`` / ``clean``: only a completed run concluded ``failure`` is
``failing``; ``neutral`` (GitGuardian's "Could not complete scanning" on a PR too large to
scan) is ``clean``, never a finding.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "APP_SLUG",
    "DEFAULT_MAX_FINDINGS",
    "MAX_FINDINGS_CAP",
    "check_state",
    "failing_runs",
    "parse_findings",
    "title_count",
]

APP_SLUG = "gitguardian"
"""The GitHub App slug of GitGuardian's check runs and suites."""
DEFAULT_MAX_FINDINGS = 50
MAX_FINDINGS_CAP = 200

ABSENT, PENDING, FAILING, CLEAN = "absent", "pending", "failing", "clean"

INCIDENT, STATUS, TYPE = "incident", "status", "type"
COMMIT, FILE, VIEW = "commit", "file", "view"
_COMPLETED = "completed"

#: Header text (lower-cased, whitespace collapsed) -> the finding field its column feeds.
HEADERS: dict[str, str] = {
    "gitguardian id": INCIDENT,
    "id": INCIDENT,
    "incident": INCIDENT,
    "gitguardian status": STATUS,
    "status": STATUS,
    "secret": TYPE,
    "secret type": TYPE,
    "detector": TYPE,
    "commit": COMMIT,
    "filename": FILE,
    "file": FILE,
    "": VIEW,
}
_TABLE_MARKERS = frozenset((TYPE, FILE))

_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_SEPARATOR = re.compile(r"^:?-+:?$")
_LINK = re.compile(r"\[([^\]]*)\]\(([^()\s]*)\)")
_UNSAFE = re.compile(r"[`|<>\[\]\x00-\x1f\x7f]")
_SPACES = re.compile(r"\s+")
_DIGITS = re.compile(r"\d{1,20}")
_SHA = re.compile(r"[0-9a-fA-F]{7,40}")
_VIEW_LINE = re.compile(r"#diff-[0-9a-fA-F]+R(\d{1,7})\b")
_STATUS_UNSAFE = re.compile(r"[^A-Za-z _-]")
_TITLE_COUNT = re.compile(r"\s*(\d{1,6})\s+secrets?\b", re.IGNORECASE)
TEXT_MAX = 200
"""Characters kept of a type or file cell."""


def _clean(value: Any, limit: int = TEXT_MAX) -> str | None:
    """A cell's plain text: links reduced to their text, markdown that could break out of a
    table cell or code span removed, whitespace collapsed, at most ``limit`` characters."""
    if not isinstance(value, str):
        return None
    text = _LINK.sub(lambda m: m.group(1), value)
    text = _SPACES.sub(" ", _UNSAFE.sub("", text.replace("\\|", ""))).strip()
    return text[:limit] or None


def _incident(cell: str) -> tuple[str | None, str | None]:
    """``(id, url)`` of the incident cell: the id is its digits; the URL only an https link
    on a gitguardian.com host."""
    match = _LINK.search(cell)
    text, url = (match.group(1), match.group(2)) if match else (cell, "")
    digits = _DIGITS.search(text)
    return (digits.group(0) if digits else None), _incident_url(url)


def _incident_url(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    trusted = host == "gitguardian.com" or host.endswith(".gitguardian.com")
    return url if parts.scheme == "https" and trusted else None


def _commit(cell: str) -> str | None:
    text = _clean(cell) or ""
    return text.lower() if _SHA.fullmatch(text) else None


def _line(cell: str) -> int | None:
    match = _VIEW_LINE.search(cell)
    return int(match.group(1)) if match else None


def _status(cell: str) -> str | None:
    text = _SPACES.sub(" ", _STATUS_UNSAFE.sub("", _LINK.sub(lambda m: m.group(1), cell)))
    return text.strip()[:40] or None


def _cells(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [c.strip() for c in _CELL_SPLIT.split(body)]


def _header(cells: list[str]) -> dict[str, int] | None:
    """The column of each finding field when ``cells`` is a findings table's header."""
    columns: dict[str, int] = {}
    for index, cell in enumerate(cells):
        field = HEADERS.get(_SPACES.sub(" ", cell).strip().lower())
        if field is not None and field not in columns:
            columns[field] = index
    return columns if _TABLE_MARKERS & columns.keys() else None


def _finding(cells: list[str], columns: Mapping[str, int]) -> dict[str, Any] | None:
    def cell(field: str) -> str:
        index = columns.get(field)
        return cells[index] if index is not None and index < len(cells) else ""

    incident, url = _incident(cell(INCIDENT))
    found = {
        INCIDENT: incident,
        "incident_url": url,
        STATUS: _status(cell(STATUS)),
        TYPE: _clean(cell(TYPE)),
        COMMIT: _commit(cell(COMMIT)),
        FILE: _clean(cell(FILE)),
        "line": _line(cell(VIEW)),
    }
    keys = (INCIDENT, TYPE, FILE, COMMIT)
    return found if any(found[k] for k in keys) else None


def _rows(text: str) -> Iterable[dict[str, Any]]:
    columns: dict[str, int] | None = None
    for raw in text.splitlines():
        if not raw.strip().startswith("|"):
            columns = None  # anything outside a table ends it
            continue
        cells = _cells(raw)
        if columns is None:
            columns = _header(cells)
            continue
        if all(_SEPARATOR.match(c.replace(" ", "")) for c in cells if c):
            continue
        found = _finding(cells, columns)
        if found is not None:
            yield found


def parse_findings(text: Any, cap: int = DEFAULT_MAX_FINDINGS) -> tuple[list[dict], int]:
    """``(findings, total)``: the first ``cap`` findings of GitGuardian's table(s) in
    ``text`` and how many rows it holds in all (module doc). Never raises."""
    if not isinstance(text, str):
        return [], 0
    findings: list[dict[str, Any]] = []
    total = 0
    for found in _rows(text):
        total += 1
        if len(findings) < cap:
            findings.append(found)
    return findings, total


def title_count(title: Any) -> int | None:
    """The secret count of a title such as ``3 secrets uncovered!``, else ``None``."""
    match = _TITLE_COUNT.match(title) if isinstance(title, str) else None
    return int(match.group(1)) if match else None


def _of_app(runs: Iterable[Any], slug: str) -> list[Mapping[str, Any]]:
    want = slug.casefold()
    return [
        r
        for r in runs
        if isinstance(r, Mapping) and str(r.get("app_slug") or "").casefold() == want
    ]


def failing_runs(runs: Iterable[Any], slug: str = APP_SLUG) -> list[Mapping[str, Any]]:
    """The App's completed check runs concluded ``failure``."""
    return [
        r
        for r in _of_app(runs, slug)
        if r.get(STATUS) == _COMPLETED and r.get("conclusion") == "failure"
    ]


def check_state(runs: Iterable[Any], slug: str = APP_SLUG) -> str:
    """``failing`` (a completed run concluded failure), ``pending`` (one still running),
    ``clean`` (every run completed otherwise, ``neutral`` included) or ``absent`` (no run of
    the App)."""
    mine = _of_app(runs, slug)
    if not mine:
        return ABSENT
    if failing_runs(mine, slug):
        return FAILING
    if any(r.get(STATUS) != _COMPLETED for r in mine):
        return PENDING
    return CLEAN
