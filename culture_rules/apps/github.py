"""GitHub App client: App JWT, installation tokens, allow-listed comments, PR reads and merges.

Cited (cite-don't-import) in spirit from the culture-nodes Go github adapter: a repo
allowlist is enforced *before* any network call, and secrets are held only in memory.

* The App JWT is RS256 over ``{iat: now-60, exp: now+540, iss: app_id}``, signed with the
  ``cryptography`` package (the ``github`` extra, imported lazily inside the signing
  function so the core stays dependency-free). Without the extra, :class:`GitHubError`
  with code ``extra_missing`` is raised.
* The installation token is exchanged via
  ``POST {api_base}/app/installations/{id}/access_tokens`` and cached per app instance
  until five minutes before its ``expires_at``.
* Exactly one merge call, :meth:`GitHubApp.merge_pull` (a squash at a given head sha), used
  only by the ``github.merge`` port, which is fenced to a run started by a
  ``discord.button.clicked`` event carrying the clicked button's one-time code: merging stays
  a human gate. :meth:`GitHubApp.delete_ref` removes the merged PR's same-repo head branch.
* Neither the key, the JWT nor the token is ever logged or put in an error message.

The HTTP transport is injectable: ``transport(method, url, headers, body, timeout) ->
(status, body_bytes)``; the default uses :mod:`urllib` and does not follow redirects.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

__all__ = [
    "DEFAULT_API_BASE",
    "PR_FACT_FIELDS",
    "GitHubApp",
    "GitHubError",
    "complete_pr_facts",
    "pr_facts",
    "urllib_transport",
]

log = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
_REFRESH_MARGIN = timedelta(minutes=5)
_TIMEOUT_S = 15
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_MAX_PAGES = 50
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+$")

#: The current call's ``(deadline, clock)`` (see :meth:`GitHubApp.deadline`).
_DEADLINE: ContextVar[tuple[datetime, Callable[[], datetime]] | None] = ContextVar(
    "github_deadline", default=None
)
Transport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, bytes]]


PR_FACT_FIELDS = (
    "head_sha",
    "head_branch",
    "head_repo",
    "base_repo",
    "base_branch",
    "base_sha",
    "draft",
    "pr_author",
)
"""The PR facts every PR-scoped trigger event carries under the same names (d14). Each event also
carries ``state`` (``open`` / ``closed``, d21) when the source reports a valid one; it is not
part of the all-or-nothing set, so a missing state omits only itself and a rule requiring
``state == "open"`` fails closed."""


_FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def _get(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_repo(value: Any) -> bool:
    return isinstance(value, str) and bool(_REPO_RE.match(value))


def _is_full_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(_FULL_SHA_RE.match(value))


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


_FACT_VALID: dict[str, Callable[[Any], bool]] = {
    "head_sha": _is_full_sha,
    "head_branch": _is_text,
    "head_repo": _is_repo,
    "base_repo": _is_repo,
    "base_branch": _is_text,
    "base_sha": _is_full_sha,
    "draft": _is_bool,
    "pr_author": _is_text,
    "state": lambda v: v in ("open", "closed"),
}


def pr_facts(pr: Any) -> dict[str, Any]:
    """The valid :data:`PR_FACT_FIELDS` of one pull-request document (a webhook's
    ``pull_request`` object or a REST ``GET /repos/{repo}/pulls/{n}`` result; same shape).

    Each fact is checked: repos are ``owner/name`` strings, SHAs 40 hex digits, branches and
    the author non-empty strings, ``draft`` a real bool. A missing or malformed fact is
    *omitted*, never ``None`` and never defaulted, so a condition comparing it is false (two
    missing repos cannot read as ``head_repo == base_repo``; a deleted fork's null
    ``head.repo`` drops only ``head_repo``). A non-mapping yields ``{}``."""
    if not isinstance(pr, dict):
        return {}
    head, base = pr.get("head"), pr.get("base")
    raw = {
        "head_sha": _get(head, "sha"),
        "head_branch": _get(head, "ref"),
        "head_repo": _get(head, "repo", "full_name") or _get(head, "full_name"),
        "base_repo": _get(base, "repo", "full_name") or _get(base, "full_name"),
        "base_branch": _get(base, "ref"),
        "base_sha": _get(base, "sha"),
        "draft": pr.get("draft"),
        "pr_author": _get(pr, "user", "login"),
        "state": pr.get("state"),
    }
    return {key: value for key, value in raw.items() if _FACT_VALID[key](value)}


def complete_pr_facts(pr: Any) -> dict[str, Any] | None:
    """:func:`pr_facts` when *every* fact is present and valid, else ``None``: the all-or-
    nothing form a lookup-based enrichment uses, so it is never half-applied."""
    facts = pr_facts(pr)
    return facts if all(k in facts for k in PR_FACT_FIELDS) else None


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
    deadline_at = time.monotonic() + timeout  # the whole call: connect, send, full read
    req = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:  # nosec B310 - scheme checked above
            return resp.status, _read_within(resp, deadline_at)
    except urllib.error.HTTPError as exc:
        return exc.code, _read_within(exc, deadline_at)


MAX_RESPONSE_BYTES = 8 << 20
"""The largest response body read (d26: a listing of a long PR's comments is far less)."""
_CHUNK = 64 << 10


def _read_within(stream: Any, deadline_at: float, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read ``stream`` to its end in chunks, within the monotonic ``deadline_at`` (a
    response trickling in slower than the socket timeout raises ``TimeoutError``, which the
    App reports as ``deadline_exceeded`` under :meth:`GitHubApp.deadline`) and at most
    ``max_bytes`` (``ValueError``)."""
    chunks: list[bytes] = []
    size = 0
    while True:
        if time.monotonic() >= deadline_at:
            raise TimeoutError("the response did not arrive within the call's deadline")
        chunk = stream.read1(_CHUNK) if hasattr(stream, "read1") else stream.read(_CHUNK)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > max_bytes:
            raise ValueError("response too large")
        chunks.append(chunk)


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

    @staticmethod
    def is_repo_name(repo: object) -> bool:
        """Whether ``repo`` has the ``owner/name`` shape (no ``..`` or extra slashes)."""
        return isinstance(repo, str) and bool(_REPO_RE.match(repo))

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

    @contextmanager
    def deadline(
        self, deadline: datetime, clock: Callable[[], datetime] | None = None
    ) -> Iterator[None]:
        """Bound every HTTP call in the block by the time left until ``deadline``.

        A call that would start at or after the deadline raises ``deadline_exceeded``
        (retryable) without touching the network, and so does a call the deadline cuts off in
        flight (its transport timeout). Held in a ContextVar, so concurrent
        callers do not see each other's deadline."""
        reset = _DEADLINE.set((deadline, clock or self._clock))
        try:
            yield
        finally:
            _DEADLINE.reset(reset)

    @staticmethod
    def _timeout() -> float:
        bound = _DEADLINE.get()
        if bound is None:
            return _TIMEOUT_S
        left = (bound[0] - bound[1]()).total_seconds()
        if left <= 0:
            raise GitHubError("deadline_exceeded", retryable=True)
        return min(float(_TIMEOUT_S), left)

    @staticmethod
    def _cut_by_deadline(exc: BaseException, timeout: float) -> bool:
        """Whether a transport failure is the active :meth:`deadline` cutting a call off: a
        timeout (``TimeoutError``, bare or as urllib's ``URLError.reason``) of a call whose
        timeout was the deadline's remainder, or any failure once the deadline has passed.
        Callers then see ``deadline_exceeded`` (retryable), as for a call never started."""
        bound = _DEADLINE.get()
        if bound is None:
            return False
        if (bound[0] - bound[1]()).total_seconds() <= 0:
            return True
        reason = getattr(exc, "reason", None)
        timed_out = isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError)
        return timed_out and timeout < _TIMEOUT_S

    def _request(
        self, method: str, path: str, bearer: str, payload: dict[str, Any] | None
    ) -> tuple[int, dict[str, Any]]:
        timeout = self._timeout()
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
            status, raw = self._transport(method, self._api_base + path, headers, body, timeout)
        except GitHubError:
            raise
        except Exception as exc:  # noqa: BLE001 - network failure is retryable; text withheld
            if self._cut_by_deadline(exc, timeout):
                raise GitHubError("deadline_exceeded", retryable=True) from None
            raise GitHubError("network_error", type(exc).__name__, retryable=True) from None
        if status >= 400:
            raise GitHubError(f"http_{status}", retryable=status >= 500 or status == 429)
        try:
            data = json.loads(raw.decode() or "{}")
        except ValueError:
            raise GitHubError("bad_response", retryable=True) from None
        if isinstance(data, list) and method == "GET":
            return status, {"items": data}  # a list endpoint
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

    def _call(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        """One call with the installation token; a revoked (401) cached token is swapped once."""
        try:
            return self._request(method, path, self.installation_token(), payload)[1]
        except GitHubError as exc:
            if exc.code != "http_401":
                raise
            # the cached token was revoked server-side: drop it and exchange once more
            self._token = self._token_expiry = None
            return self._request(method, path, self.installation_token(), payload)[1]

    def _require_allowed(self, repo: str, what: str) -> None:
        if not self.is_allowed(repo):
            log.warning("github %s refused: repo not allow-listed", what)
            raise GitHubError("repo_not_allowed", "repo is not on the actor's allowlist")

    def post_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        """Comment on issue/PR ``number`` of ``repo``; returns ``{comment_id, url}``."""
        self._require_allowed(repo, "comment")
        data = self._call("POST", f"/repos/{repo}/issues/{int(number)}/comments", {"body": body})
        return {"comment_id": data.get("id"), "url": data.get("html_url")}

    def get_pull(self, repo: str, number: int) -> dict[str, Any]:
        """The pull request ``number`` of ``repo`` (REST ``GET /repos/{repo}/pulls/{n}``)."""
        self._require_allowed(repo, "pull read")
        return self._call("GET", f"/repos/{repo}/pulls/{int(number)}", None)

    def merge_pull(self, repo: str, number: int, sha: str) -> dict[str, Any]:
        """Squash-merge PR ``number`` of ``repo`` at head ``sha`` (REST ``PUT
        /repos/{repo}/pulls/{n}/merge`` with ``{sha, merge_method: squash}``): GitHub refuses
        a head that moved (``http_409``) or a PR it cannot merge (``http_405``). Returns
        ``{merged, sha}`` (``sha`` is the merge commit). Only ``github.merge`` calls it."""
        self._require_allowed(repo, "merge")
        if not _is_full_sha(sha):
            raise GitHubError("bad_input", "sha must be a full commit sha")
        payload = {"sha": sha, "merge_method": "squash"}
        data = self._call("PUT", f"/repos/{repo}/pulls/{int(number)}/merge", payload)
        merged, commit = data.get("merged"), data.get("sha")
        return {"merged": merged is True, "sha": commit if isinstance(commit, str) else None}

    def delete_ref(self, repo: str, branch: str) -> None:
        """Delete branch ``branch`` of ``repo`` (REST ``DELETE
        /repos/{repo}/git/refs/heads/{branch}``); a branch already gone answers ``http_422``
        or ``http_404``."""
        self._require_allowed(repo, "ref delete")
        if not isinstance(branch, str) or not _BRANCH_RE.fullmatch(branch) or ".." in branch:
            raise GitHubError("bad_input", "branch")
        quoted = urllib.parse.quote(branch, safe="/")
        self._call("DELETE", f"/repos/{repo}/git/refs/heads/{quoted}", None)

    def branch_tip(self, repo: str, branch: str) -> str:
        """The commit branch ``branch`` of ``repo`` points at now (REST ``GET
        /repos/{repo}/branches/{branch}``; read-only, Contents: read). d37: a PR's
        ``base.sha`` is the base as of the PR's last push, not the branch's tip."""
        self._require_allowed(repo, "branch read")
        if not isinstance(branch, str) or not branch:
            raise GitHubError("bad_input", "branch")
        quoted = urllib.parse.quote(branch, safe="")
        sha = _get(self._call("GET", f"/repos/{repo}/branches/{quoted}", None), "commit", "sha")
        if not _is_full_sha(sha):
            raise GitHubError("bad_response", "branch tip", retryable=True)
        return sha

    def compare_status(self, repo: str, base: str, head: str) -> str:
        """GitHub's ``status`` of ``head`` against ``base`` (REST ``GET
        /repos/{repo}/compare/{base}...{head}``): ``ahead`` or ``identical`` when ``base``
        is an ancestor of (or is) ``head``; ``behind`` or ``diverged`` otherwise (d37)."""
        self._require_allowed(repo, "compare")
        if not all(isinstance(r, str) and r for r in (base, head)):
            raise GitHubError("bad_input", "compare refs")
        refs = "...".join(urllib.parse.quote(r, safe="") for r in (base, head))
        status = self._call("GET", f"/repos/{repo}/compare/{refs}?per_page=1", None).get("status")
        if status not in ("ahead", "identical", "behind", "diverged"):
            raise GitHubError("bad_response", "compare status", retryable=True)
        return status

    def list_check_suites(self, repo: str, sha: str) -> list[dict[str, Any]]:
        """Every check suite of commit ``sha`` (REST, paginated); read-only (Checks: read).

        Each item is ``{app_slug, status, conclusion}`` - the three fields a settle decision
        needs - so nothing else of GitHub's payload is carried around."""
        self._require_allowed(repo, "check suites")
        if not isinstance(sha, str) or not _SHA_RE.match(sha):
            raise GitHubError("bad_input", "sha")
        out: list[dict[str, Any]] = []
        for page in range(1, _MAX_PAGES + 1):
            data = self._call(
                "GET", f"/repos/{repo}/commits/{sha}/check-suites?per_page=100&page={page}", None
            )
            suites = data.get("check_suites")
            suites = suites if isinstance(suites, list) else []
            out.extend(_suite_fact(suite) for suite in suites if isinstance(suite, dict))
            if len(suites) < 100:
                return out
        raise GitHubError("too_many_pages", "check suites")


def _suite_fact(suite: dict[str, Any]) -> dict[str, Any]:
    """The three fields of one check suite a settle decision needs."""
    app = suite.get("app") if isinstance(suite.get("app"), dict) else {}
    return {
        "app_slug": app.get("slug"),
        "status": suite.get("status"),
        "conclusion": suite.get("conclusion"),
    }
