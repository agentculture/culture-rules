"""SonarCloud's public Web API, read-only, for the PR fixer (d21 phase 2, E5).

An app client like :mod:`culture_rules.apps.github`, outside the core model and engine
(claim c10: the rules engine itself knows no Sonar). Three reads, all for one pull request
of one project:

* :meth:`SonarCloud.quality_gate` - ``GET /api/qualitygates/project_status``: the PR's gate
  status and its conditions;
* :meth:`SonarCloud.issues` - ``GET /api/issues/search``: the PR's open issues of the given
  types (``BUG``, ``VULNERABILITY``, ``CODE_SMELL``), paged, up to a cap;
* :meth:`SonarCloud.hotspots` - ``GET /api/hotspots/search``: its hotspots still to review,
  paged, up to a cap.

Each list read also answers the total SonarCloud reports, so a capped list says what it left
out.

Public projects need no token. An optional token (a private project) rides only in the
``Authorization`` header, never the URL or a log. The transport is injectable
(``transport(method, url, headers, timeout) -> (status, body)``); the default uses
:mod:`urllib` and does not follow redirects. Any failure raises :class:`SonarError` with a
``code``: ``not_found`` (404: no analysis for that project or PR), ``http_<status>``,
``unreachable``, ``malformed``. Standard-library only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

__all__ = ["DEFAULT_SONAR_BASE", "SonarCloud", "SonarError", "Transport"]

DEFAULT_SONAR_BASE = "https://sonarcloud.io"
_TIMEOUT_S = 15.0
_PAGE = 100

Transport = Callable[[str, str, Mapping[str, str], float], tuple[int, bytes]]


class SonarError(Exception):
    """A SonarCloud read failed; ``code`` says how."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str], timeout: float
) -> tuple[int, bytes]:
    if urllib.parse.urlsplit(url).scheme != "https":
        raise SonarError("unreachable", "refusing a non-https SonarCloud URL")
    req = urllib.request.Request(url, headers=dict(headers), method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:  # nosec B310 - https checked above
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        raise SonarError("unreachable", type(exc).__name__) from exc


class SonarCloud:
    """A read-only SonarCloud Web API client (module doc)."""

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        base_url: str = DEFAULT_SONAR_BASE,
        token: str | None = None,
        timeout: float = _TIMEOUT_S,
    ) -> None:
        self._transport = transport or urllib_transport
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def __repr__(self) -> str:  # never expose the token
        return f"SonarCloud({self._base!r})"

    def _get(self, path: str, query: Mapping[str, Any]) -> Mapping[str, Any]:
        url = f"{self._base}{path}?{urllib.parse.urlencode(query)}"
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            status, raw = self._transport("GET", url, headers, self._timeout)
        except SonarError:
            raise
        except OSError as exc:
            raise SonarError("unreachable", type(exc).__name__) from exc
        if status == 404:
            raise SonarError("not_found", path)
        if status != 200:
            raise SonarError(f"http_{status}", path)
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SonarError("malformed", path) from exc
        if not isinstance(doc, Mapping):
            raise SonarError("malformed", path)
        return doc

    def quality_gate(self, project: str, pull_request: int) -> Mapping[str, Any]:
        """``{"status": ..., "conditions": [...]}`` of the PR's quality gate."""
        doc = self._get(
            "/api/qualitygates/project_status",
            {"projectKey": project, "pullRequest": str(pull_request)},
        )
        status = doc.get("projectStatus")
        if not isinstance(status, Mapping):
            raise SonarError("malformed", "no projectStatus")
        conditions = status.get("conditions") or []
        if not isinstance(conditions, list):
            raise SonarError("malformed", "conditions")
        return {"status": status.get("status"), "conditions": conditions}

    def issues(
        self, project: str, pull_request: int, types: list[str], *, limit: int
    ) -> tuple[list[Mapping[str, Any]], int]:
        """The PR's open issues of ``types`` (at most ``limit``) and how many there are."""
        out: list[Mapping[str, Any]] = []
        total, page = 0, 1
        while len(out) < limit:
            doc = self._get(
                "/api/issues/search",
                {
                    "componentKeys": project,
                    "pullRequest": str(pull_request),
                    "types": ",".join(sorted(types)),
                    "resolved": "false",
                    "ps": str(_PAGE),
                    "p": str(page),
                },
            )
            found = doc.get("issues")
            if not isinstance(found, list):
                raise SonarError("malformed", "issues")
            paging = doc.get("paging") if isinstance(doc.get("paging"), Mapping) else {}
            if page == 1:
                total = _total(paging, len(found))
            out.extend(i for i in found if isinstance(i, Mapping))
            if len(found) < _PAGE:
                break
            page += 1
        return out[:limit], max(total, len(out))

    def hotspots(
        self, project: str, pull_request: int, *, limit: int
    ) -> tuple[list[Mapping[str, Any]], int]:
        """The PR's security hotspots still to review (at most ``limit``, paged) and how
        many there are. ``limit`` 0 reads one row for the total only."""
        out: list[Mapping[str, Any]] = []
        total, page = 0, 1
        while True:
            size = _PAGE if limit > 0 else 1
            doc = self._get(
                "/api/hotspots/search",
                {
                    "projectKey": project,
                    "pullRequest": str(pull_request),
                    "status": "TO_REVIEW",
                    "ps": str(size),
                    "p": str(page),
                },
            )
            found = doc.get("hotspots")
            if not isinstance(found, list):
                raise SonarError("malformed", "hotspots")
            paging = doc.get("paging") if isinstance(doc.get("paging"), Mapping) else {}
            if page == 1:
                total = _total(paging, len(found))
            out.extend(h for h in found if isinstance(h, Mapping))
            if limit <= 0 or len(out) >= limit or len(found) < size:
                break
            page += 1
        return out[: max(limit, 0)], max(total, len(out[: max(limit, 0)]))


def _total(paging: Mapping[str, Any], fallback: int) -> int:
    value = paging.get("total")
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback
