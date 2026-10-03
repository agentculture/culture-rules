"""A small urllib client for the culture-rules HTTP API.

The CLI (and the MCP server) reach the engine only through this client: it speaks the
contract in ``api/openapi.json`` and never touches the store or the engine. Credentials are a
bearer service token (``Authorization: Bearer ...``); an optional dev identity header is sent
when the server runs with the insecure dev identity enabled. The transport is injectable so
tests can run it against an in-process app.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode

__all__ = ["ApiClient", "ApiError", "ApiUnreachable", "DEFAULT_API_URL", "Transport"]

DEFAULT_API_URL = "http://127.0.0.1:8765"
IDENTITY_HEADER = "X-Culture-Identity"
_TIMEOUT_S = 30

#: ``(method, url, headers, body) -> (status, body bytes)``; ``url`` is the path (+ query).
Transport = Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, bytes]]


class ApiError(Exception):
    """The API answered with an error status; carries its error envelope."""

    def __init__(self, status: int, code: str, message: str, errors: list[dict] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.errors = errors or []


class ApiUnreachable(Exception):
    """The API could not be reached at all (connection refused, DNS, timeout)."""


class ApiClient:
    def __init__(
        self,
        base_url: str = DEFAULT_API_URL,
        *,
        token: str | None = None,
        identity: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.identity = identity
        self._transport = transport or self._urllib

    def _urllib(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        if not self.base_url.startswith(("http://", "https://")):
            raise ApiUnreachable(f"unsupported API url scheme: {self.base_url!r}")
        req = urllib.request.Request(  # noqa: S310  # nosec B310 - scheme checked above
            self.base_url + url, data=body, method=method, headers=dict(headers)
        )
        try:
            with urllib.request.urlopen(
                req, timeout=_TIMEOUT_S
            ) as resp:  # noqa: S310  # nosec B310
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except OSError as exc:  # urllib.error.URLError is an OSError
            raise ApiUnreachable(f"cannot reach {self.base_url}: {exc}") from exc

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: Mapping[str, Any] | None = None,
    ) -> Any:
        """Call the API; return the decoded JSON body or raise :class:`ApiError`."""
        params = {k: v for k, v in (query or {}).items() if v is not None and v != ""}
        url = path + (("?" + urlencode(params)) if params else "")
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.identity:
            headers[IDENTITY_HEADER] = self.identity
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        status, raw = self._transport(method, url, headers, data)
        payload = _decode(raw)
        if status >= 400:
            raise _error(status, payload)
        return payload


def _decode(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw.decode("utf-8", "replace")}


def _error(status: int, payload: Any) -> ApiError:
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        return ApiError(
            status,
            str(err.get("code", "error")),
            str(err.get("message", f"HTTP {status}")),
            list(err.get("errors") or []),
        )
    return ApiError(status, "error", f"HTTP {status}")
