"""GitHub App client: App JWT, cached installation token, allow-listed issue comments.

Cited (cite-don't-import) in spirit from the culture-nodes Go github adapter: a repo
allowlist is enforced *before* any network call, and secrets are held only in memory.

* The App JWT is RS256 over ``{iat: now-60, exp: now+540, iss: app_id}``, signed with the
  ``cryptography`` package (the ``github`` extra, imported lazily inside the signing
  function so the core stays dependency-free). Without the extra, :class:`GitHubError`
  with code ``extra_missing`` is raised.
* The installation token is exchanged via
  ``POST {api_base}/app/installations/{id}/access_tokens`` and cached per app instance
  until five minutes before its ``expires_at``.
* Neither the key, the JWT nor the token is ever logged or put in an error message.

The HTTP transport is injectable: ``transport(method, url, headers, body, timeout) ->
(status, body_bytes)``; the default uses :mod:`urllib` and does not follow redirects.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

__all__ = ["DEFAULT_API_BASE", "GitHubApp", "GitHubError", "urllib_transport"]

log = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
_REFRESH_MARGIN = timedelta(minutes=5)
_TIMEOUT_S = 15
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+$")

Transport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, bytes]]


class GitHubError(Exception):
    """A GitHub call failed. ``code`` is machine-readable; messages never hold secrets."""

    def __init__(self, code: str, message: str = "", *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.retryable = retryable


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> tuple[int, bytes]:
    """Default transport: one urllib request; HTTP error statuses are returned, not raised."""
    if not url.startswith(("https://", "http://")):
        raise ValueError("unsupported url scheme")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:  # nosec B310 - scheme checked above
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


class GitHubApp:
    """One GitHub App installation: mints tokens and posts comments on allow-listed repos."""

    def __init__(
        self,
        *,
        app_id: str | int,
        installation_id: str | int,
        private_key: str,
        repos: Iterable[str] = (),
        api_base: str = DEFAULT_API_BASE,
        transport: Transport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._app_id = str(app_id)
        self._installation_id = str(installation_id)
        self._private_key = private_key
        self._repos = frozenset(str(r).lower() for r in repos)
        self._api_base = api_base.rstrip("/")
        self._transport = transport or urllib_transport
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token: str | None = None
        self._token_expiry: datetime | None = None

    def __repr__(self) -> str:  # never expose the key or token
        return f"GitHubApp(app_id={self._app_id!r}, installation_id={self._installation_id!r})"

    def is_allowed(self, repo: str) -> bool:
        return isinstance(repo, str) and bool(_REPO_RE.match(repo)) and repo.lower() in self._repos

    def make_jwt(self) -> str:
        """Sign the short-lived App JWT (RS256). Needs the ``github`` extra."""
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError as exc:
            raise GitHubError("extra_missing", "install culture-rules[github]") from exc
        now = int(self._clock().timestamp())
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {"iat": now - 60, "exp": now + 540, "iss": self._app_id}
        signing = ".".join(
            _b64url(json.dumps(part, separators=(",", ":")).encode()) for part in (header, claims)
        )
        try:
            key = serialization.load_pem_private_key(self._private_key.encode(), password=None)
            signature = key.sign(signing.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        except Exception as exc:  # noqa: BLE001 - never echo key material
            raise GitHubError("bad_private_key", type(exc).__name__) from None
        return f"{signing}.{_b64url(signature)}"

    def _request(
        self, method: str, path: str, bearer: str, payload: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {bearer}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "culture-rules",
        }
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        try:
            status, raw = self._transport(method, self._api_base + path, headers, body, _TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - network failure: retryable, text withheld
            raise GitHubError("network_error", type(exc).__name__, retryable=True) from None
        if status >= 400:
            raise GitHubError(f"http_{status}", retryable=status >= 500 or status == 429)
        try:
            data = json.loads(raw.decode() or "{}")
        except ValueError:
            raise GitHubError("bad_response", retryable=True) from None
        return status, data if isinstance(data, dict) else {}

    def installation_token(self) -> str:
        """The cached installation token, refreshed within 5 minutes of its expiry."""
        now = self._clock()
        if self._token and self._token_expiry and now < self._token_expiry - _REFRESH_MARGIN:
            return self._token
        path = f"/app/installations/{self._installation_id}/access_tokens"
        _, data = self._request("POST", path, self.make_jwt(), None)
        token, expires = data.get("token"), data.get("expires_at")
        if not isinstance(token, str) or not isinstance(expires, str):
            raise GitHubError("bad_response", "token exchange", retryable=True)
        self._token, self._token_expiry = token, _parse_time(expires)
        log.debug("github app %s: installation token refreshed", self._app_id)
        return token

    def post_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        """Comment on issue/PR ``number`` of ``repo``; returns ``{comment_id, url}``."""
        if not self.is_allowed(repo):
            log.warning("github comment refused: repo not allow-listed")
            raise GitHubError("repo_not_allowed", "repo is not on the actor's allowlist")
        number = int(number)
        token = self.installation_token()
        _, data = self._request(
            "POST", f"/repos/{repo}/issues/{number}/comments", token, {"body": body}
        )
        return {"comment_id": data.get("id"), "url": data.get("html_url")}
