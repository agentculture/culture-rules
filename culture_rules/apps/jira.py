"""Jira Cloud REST client: refetch an issue by key, add a comment (ADF body).

Cited (cite-don't-import) in spirit from the culture-nodes Go ``jirawebhook`` hydration: the
webhook payload is only a hint, so receivers refetch the issue through this client. Standard
library only (:mod:`urllib`), redirects are never followed, and the API token is held in memory
and never logged, repr'd or put in an error message.

Auth: with an ``email`` the client sends ``Basic base64(email:token)``; without one it sends
``Bearer <token>`` (a scoped token through an ``api_base`` gateway). The base URL is
``api_base`` when given, else ``https://<site>``.

The transport is injectable: ``transport(method, url, headers, body, timeout) ->
(status, body_bytes)``; the default is the GitHub client's urllib transport.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import urllib.parse
from typing import Any

from culture_rules.apps.github import Transport, urllib_transport

__all__ = ["ISSUE_KEY_RE", "JiraClient", "JiraError"]

log = logging.getLogger(__name__)

ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")
_FIELDS = "summary,status,assignee,project,updated,issuetype,priority,creator"
_TIMEOUT_S = 15


class JiraError(Exception):
    """A Jira call failed. ``code`` is machine-readable; messages never hold secrets."""

    def __init__(self, code: str, message: str = "", *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.retryable = retryable


class JiraClient:
    """One Jira site: ``get_issue`` and ``add_comment``."""

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
        return data if isinstance(data, dict) else {}

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
        adf = {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": body}]}],
        }
        data = self._request("POST", f"/rest/api/3/issue/{key}/comment", {"body": adf})
        return {"comment_id": data.get("id")}
