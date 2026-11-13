"""Jira Cloud REST client: refetch an issue by key, read it in full, add a comment (ADF body).

Cited (cite-don't-import) in spirit from the culture-nodes Go ``jirawebhook`` hydration: the
webhook payload is only a hint, so receivers refetch the issue through this client. Standard
library only (:mod:`urllib`), redirects are never followed, and the API token is held in memory
and never logged, repr'd or put in an error message.

Auth: with an ``email`` the client sends ``Basic base64(email:token)``; without one it sends
``Bearer <token>`` (a scoped token through an ``api_base`` gateway). The base URL is
``api_base`` when given, else ``https://<site>``.

The transport is injectable: ``transport(method, url, headers, body, timeout) ->
(status, body_bytes)``; the default is :func:`culture_rules.transport.urllib_transport`.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import urllib.parse
from typing import Any

from culture_rules.extensions.jira.adf import flatten, to_adf
from culture_rules.transport import Transport, urllib_transport

__all__ = ["ISSUE_KEY_RE", "JiraClient", "JiraError"]

log = logging.getLogger(__name__)

ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")
_FIELDS = "summary,status,assignee,project,updated,issuetype,priority,creator"
_TIMEOUT_S = 15
_FULL_FIELDS = ("summary", "project", "description", "reporter")
_CUSTOM_ID_RE = re.compile(r"^customfield_\d+$")
# Standard Jira field ids, used as given; any other name is resolved through /field.
_STANDARD_FIELDS = frozenset(
    {
        "summary", "description", "status", "assignee", "reporter", "creator", "priority",
        "labels", "components", "fixVersions", "versions", "duedate", "environment",
        "issuetype", "project", "created", "updated", "resolution", "resolutiondate",
        "parent", "timetracking", "attachment", "issuelinks", "subtasks", "watches",
        "votes", "security", "statuscategorychangedate", "lastViewed",
    }
)  # fmt: skip
_COMMENT_PAGE = 100
_MAX_COMMENT_PAGES = 50


class JiraError(Exception):
    """A Jira call failed. ``code`` is machine-readable; messages never hold secrets."""

    def __init__(self, code: str, message: str = "", *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.retryable = retryable


class JiraClient:
    """One Jira site: ``get_issue``, ``get_issue_full`` and ``add_comment``."""

    def __init__(
        self,
        *,
        token: str,
        site: str = "",
        email: str = "",
        api_base: str = "",
        transport: Transport | None = None,
    ) -> None:
        self._token = token
        self._email = email
        self._site = site.removeprefix("https://").strip("/")
        self._api_base = api_base.rstrip("/")
        self._transport = transport or urllib_transport

    def __repr__(self) -> str:  # never expose the token
        return f"JiraClient(site={self._site!r}, api_base={self._api_base!r})"

    @property
    def base(self) -> str:
        if self._api_base:
            return self._api_base
        if self._site:
            return f"https://{self._site}"
        raise JiraError("not_configured", "set connection.site or connection.api_base")

    def _auth(self) -> str:
        if self._email:
            raw = f"{self._email}:{self._token}".encode()
            return "Basic " + base64.b64encode(raw).decode("ascii")
        return f"Bearer {self._token}"

    def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        data = self._call(method, path, payload)
        return data if isinstance(data, dict) else {}

    def _call(self, method: str, path: str, payload: dict[str, Any] | None) -> Any:
        url = self.base + path
        if not self._token:
            raise JiraError("not_configured", "no API token")
        headers = {
            "Authorization": self._auth(),
            "Accept": "application/json",
            "User-Agent": "culture-rules",
        }
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        try:
            status, raw = self._transport(method, url, headers, body, _TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - network failure is retryable; text withheld
            raise JiraError("network_error", type(exc).__name__, retryable=True) from None
        if status >= 400:
            log.warning("jira %s failed: http %s", method, status)
            raise JiraError(f"http_{status}", retryable=status >= 500 or status == 429)
        try:
            data = json.loads(raw.decode() or "{}")
        except ValueError:
            raise JiraError("bad_response", retryable=True) from None
        return data

    @staticmethod
    def _check_key(key: str) -> str:
        if not isinstance(key, str) or not ISSUE_KEY_RE.match(key):
            raise JiraError("bad_key", "not a Jira issue key")
        return key

    def get_issue(self, key: str) -> dict[str, Any]:
        """``GET /rest/api/3/issue/{key}`` with a compact field set."""
        self._check_key(key)
        query = urllib.parse.urlencode({"fields": _FIELDS})
        return self._request("GET", f"/rest/api/3/issue/{key}?{query}", None)

    def add_comment(self, key: str, body: str) -> dict[str, Any]:
        """Comment on ``key`` with a plain-text ADF document; returns ``{comment_id}``."""
        self._check_key(key)
        payload = {"body": to_adf(body)}
        data = self._request("POST", f"/rest/api/3/issue/{key}/comment", payload)
        return {"comment_id": data.get("id")}

    def get_issue_full(self, key: str, fields: list[str] | None = None) -> dict[str, Any]:
        """The whole issue: description, every comment and the named fields, as plain data.

        Returns ``{key, project, summary, description, reporter, comments, fields}``; the
        description and comment bodies are flattened ADF, ``reporter`` and each comment's
        ``author`` are accountIds (or None), and ``fields`` is keyed by the names the caller
        passed (an ADF value flattened, an unknown name omitted). Nothing is truncated.
        """
        self._check_key(key)
        wanted = self._resolve_fields(list(fields or []))
        ids = list(_FULL_FIELDS) + [i for i in wanted.values() if i not in _FULL_FIELDS]
        query = urllib.parse.urlencode({"fields": ",".join(ids)})
        issue = self._request("GET", f"/rest/api/3/issue/{key}?{query}", None)
        raw = issue.get("fields")
        raw = raw if isinstance(raw, dict) else {}
        project = raw.get("project")
        return {
            "key": issue.get("key", key),
            "project": project.get("key") if isinstance(project, dict) else None,
            "summary": raw.get("summary") or "",
            "description": flatten(raw.get("description")) if raw.get("description") else "",
            "reporter": _account(raw.get("reporter")),
            "comments": self._comments(key),
            "fields": {name: _field_value(raw[fid]) for name, fid in wanted.items() if fid in raw},
        }

    def _resolve_fields(self, names: list[str]) -> dict[str, str]:
        """Map each caller name to a field id; ids pass through, names go through /field."""
        out: dict[str, str] = {}
        pending = []
        for name in names:
            if not isinstance(name, str) or not name:
                continue
            if _CUSTOM_ID_RE.match(name) or name in _STANDARD_FIELDS:
                out[name] = name
            else:
                pending.append(name)
        if pending:
            listing = self._call("GET", "/rest/api/3/field", None)
            by_name: dict[str, str] = {}
            for entry in listing if isinstance(listing, list) else []:
                if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                    by_name.setdefault(str(entry.get("name")), entry["id"])
                    by_name.setdefault(entry["id"], entry["id"])
            for name in pending:
                if name in by_name:
                    out[name] = by_name[name]
        return out

    def _comments(self, key: str) -> list[dict[str, Any]]:
        """Every comment, in order, following ``startAt``/``total`` pagination (capped)."""
        out: list[dict[str, Any]] = []
        for _ in range(_MAX_COMMENT_PAGES):
            query = urllib.parse.urlencode({"startAt": len(out), "maxResults": _COMMENT_PAGE})
            page = self._request("GET", f"/rest/api/3/issue/{key}/comment?{query}", None)
            chunk = page.get("comments")
            if not isinstance(chunk, list) or not chunk:
                return out
            for item in chunk:
                if isinstance(item, dict):
                    out.append(
                        {
                            "id": item.get("id"),
                            "author": _account(item.get("author")),
                            "created": item.get("created"),
                            "body": flatten(item.get("body")),
                        }
                    )
            total = page.get("total")
            if isinstance(total, int) and len(out) >= total:
                return out
        raise JiraError("too_many_comments", f"more than {_MAX_COMMENT_PAGES} pages")


def _account(user: Any) -> str | None:
    if isinstance(user, dict) and isinstance(user.get("accountId"), str):
        return user["accountId"]
    return None


def _field_value(value: Any) -> Any:
    if isinstance(value, dict) and value.get("type") == "doc":
        return flatten(value)
    return value
