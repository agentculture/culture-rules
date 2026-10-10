"""GitHub App client: App JWT, installation tokens, allow-listed comments, PR reads and replies.

Cited (cite-don't-import) in spirit from the culture-nodes Go github adapter: a repo
allowlist is enforced *before* any network call, and secrets are held only in memory.

* The App JWT is RS256 over ``{iat: now-60, exp: now+540, iss: app_id}``, signed with the
  ``cryptography`` package (the ``github`` extra, imported lazily inside the signing
  function so the core stays dependency-free). Without the extra, :class:`GitHubError`
  with code ``extra_missing`` is raised.
* The installation token is exchanged via
  ``POST {api_base}/app/installations/{id}/access_tokens`` and cached per app instance
  until five minutes before its ``expires_at``.
* :meth:`GitHubApp.push_token` mints a fresh, *uncached* token per push, scoped to exactly
  one repository with ``{contents: write}`` only; the caller holds it for one push alone.
* Review threads: :meth:`GitHubApp.reply_review_comment` (REST) and
  :meth:`GitHubApp.resolve_review_thread` (GraphQL ``resolveReviewThread``), both as the App;
  :meth:`GitHubApp.list_review_threads` reads the unresolved ones (GraphQL, bounded pages).
* There is deliberately no merge call: merging stays a human gate.
* Neither the key, the JWT nor the token is ever logged or put in an error message.

The HTTP transport is injectable: ``transport(method, url, headers, body, timeout) ->
(status, body_bytes)``; the default uses :mod:`urllib` and does not follow redirects.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
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
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+$")

#: The current call's ``(deadline, clock)`` (see :meth:`GitHubApp.deadline`).
_DEADLINE: ContextVar[tuple[datetime, Callable[[], datetime]] | None] = ContextVar(
    "github_deadline", default=None
)
_GUARD: ContextVar[Callable[[], None] | None] = ContextVar("github_request_guard", default=None)
"""Called before every HTTP request in a :meth:`GitHubApp.request_guard` block (d26)."""
_WATCH: ContextVar[bool] = ContextVar("github_watchdog", default=False)
"""Whether requests run under the watchdog (:meth:`GitHubApp.watchdog`; the status stage
only, d26)."""

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
"""The PR facts every fixer-trigger event carries under the same names (d14). Each event also
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


WATCHDOG_WORKERS = 8
"""The most transport calls running at once per process under the (opt-in) watchdog; an
abandoned call (past its deadline) keeps its worker until its socket timeout ends it."""
_WATCH_SLOTS = threading.BoundedSemaphore(WATCHDOG_WORKERS)


def _watched(call: Callable[[], tuple[int, bytes]], timeout: float) -> tuple[int, bytes]:
    """Run one transport call on a daemon worker and wait at most ``timeout`` (d26): the
    hard bound of the whole call - connect, headers, chunks and trailers, which http.client
    reads line by line under the socket timeout alone. Past it the caller gets
    ``deadline_exceeded`` (retryable) and the worker is abandoned; the transport's socket
    timeout (the same remaining budget) ends it soon after. With every worker busy, the
    call fails ``transport_busy`` (retryable) without starting."""
    if not _WATCH_SLOTS.acquire(blocking=False):
        raise GitHubError("transport_busy", "every transport worker is busy", retryable=True)
    result: Future[tuple[int, bytes]] = Future()

    def work() -> None:
        try:
            result.set_result(call())
        except Exception as exc:  # noqa: BLE001 - handed to the waiting caller
            result.set_exception(exc)
        except BaseException as exc:  # handed over too, then left to end this thread
            result.set_exception(exc)
            raise
        finally:
            _WATCH_SLOTS.release()

    try:
        threading.Thread(target=work, name="github-call", daemon=True).start()
    except BaseException:
        _WATCH_SLOTS.release()
        raise
    try:
        return result.result(timeout=timeout)
    except FutureTimeout:
        raise GitHubError("deadline_exceeded", retryable=True) from None


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

    @contextmanager
    def request_guard(self, guard: Callable[[], None]) -> Iterator[None]:
        """Call ``guard()`` before every HTTP request in the block, token exchanges
        included (d26: the status board counts each request against its budget; the guard
        raises :class:`GitHubError` to stop before the request is sent). Held in a
        ContextVar, like :meth:`deadline`."""
        reset = _GUARD.set(guard)
        try:
            yield
        finally:
            _GUARD.reset(reset)

    @contextmanager
    def watchdog(self) -> Iterator[None]:
        """Run every request of the block under the watchdog (:func:`_watched`): a hard
        bound of the whole call, connect to trailers. Opt-in (d26: the status stage only);
        every other caller runs its requests inline, as before."""
        reset = _WATCH.set(True)
        try:
            yield
        finally:
            _WATCH.reset(reset)

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
        guard = _GUARD.get()
        if guard is not None:
            guard()
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
            status, raw = self._send(method, self._api_base + path, headers, body, timeout)
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
            return status, {"items": data}  # a list endpoint (list_issue_comments)
        return status, data if isinstance(data, dict) else {}

    def _send(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        """One transport call: inline, or under the watchdog inside :meth:`watchdog`."""
        if not _WATCH.get():
            return self._transport(method, url, headers, body, timeout)
        return _watched(lambda: self._transport(method, url, headers, body, timeout), timeout)

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

    def update_issue_comment(self, repo: str, comment_id: int, body: str) -> dict[str, Any]:
        """Edit comment ``comment_id`` on ``repo`` (REST ``PATCH
        /repos/{repo}/issues/comments/{id}``, d26: the PR fixer's status comment, which the
        App posted); returns ``{comment_id, url}``. A deleted comment answers ``http_404``."""
        self._require_allowed(repo, "comment edit")
        if not isinstance(comment_id, int) or isinstance(comment_id, bool) or comment_id < 1:
            raise GitHubError("bad_input", "comment_id must be a positive integer")
        data = self._call("PATCH", f"/repos/{repo}/issues/comments/{comment_id}", {"body": body})
        return {"comment_id": data.get("id"), "url": data.get("html_url")}

    @property
    def app_id(self) -> str:
        """The App's id (comments it posted carry it as ``performed_via_github_app.id``)."""
        return str(self._app_id)

    def list_issue_comments(
        self, repo: str, number: int, *, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        """The comments on issue/PR ``number`` (REST ``GET /repos/{repo}/issues/{n}/comments``,
        100 a page, at most ``max_pages``), oldest first, as ``{comment_id, url, body,
        app_id}`` (``app_id`` is the posting App's id, ``None`` for a person; d26)."""
        self._require_allowed(repo, "comment listing")
        out: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            path = f"/repos/{repo}/issues/{int(number)}/comments?per_page=100&page={page}"
            items = self._call("GET", path, None).get("items") or []
            out += [_comment_fact(c) for c in items if isinstance(c, dict)]
            if len(items) < 100:
                break
        return out

    def push_token(self, repo: str) -> str:
        """A fresh installation token for one push: ``repositories=[repo]``, contents:write only.

        Never cached and never shared with :meth:`installation_token`; the caller drops it
        after the push. GitHub takes repository *names* (the installation fixes the owner).
        """
        self._require_allowed(repo, "push token")
        owner, name = repo.split("/", 1)
        payload = {"repositories": [name], "permissions": {"contents": "write"}}
        path = f"/app/installations/{self._installation_id}/access_tokens"
        _, data = self._request("POST", path, self.make_jwt(), payload)
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise GitHubError("bad_response", "token exchange", retryable=True)
        granted = data.get("repositories")
        if isinstance(granted, list) and any(
            isinstance(r, dict) and str(r.get("full_name", "")).lower() != repo.lower()
            for r in granted
        ):
            raise GitHubError("token_scope_mismatch", "token is not scoped to the one repo")
        log.debug("github app %s: single-repo push token minted (owner %s)", self._app_id, owner)
        return token

    def get_pull(self, repo: str, number: int) -> dict[str, Any]:
        """The pull request ``number`` of ``repo`` (REST ``GET /repos/{repo}/pulls/{n}``)."""
        self._require_allowed(repo, "pull read")
        return self._call("GET", f"/repos/{repo}/pulls/{int(number)}", None)

    def list_open_pulls(self, repo: str, *, max_pages: int = 3) -> list[dict[str, Any]]:
        """The open pull requests of ``repo`` (REST ``GET /repos/{repo}/pulls?state=open``,
        100 a page, at most ``max_pages``); read-only (Pull requests: read). The listing
        carries no ``mergeable``: read each PR with :meth:`get_pull` for that (d31)."""
        self._require_allowed(repo, "pull listing")
        out: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            path = f"/repos/{repo}/pulls?state=open&per_page=100&page={page}"
            items = self._call("GET", path, None).get("items") or []
            out += [p for p in items if isinstance(p, dict)]
            if len(items) < 100:
                break
        return out

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

    def list_check_runs(self, repo: str, sha: str) -> list[dict[str, Any]]:
        """The latest check run of each check of commit ``sha`` (REST, paginated); read-only
        (Checks: read).

        Each item is ``{name, app_slug, status, conclusion, title, text, html_url}`` (the
        run's ``output.title`` and ``output.text``, d25: GitGuardian's findings table), so
        nothing else of GitHub's payload is carried around."""
        self._require_allowed(repo, "check runs")
        if not isinstance(sha, str) or not _SHA_RE.match(sha):
            raise GitHubError("bad_input", "sha")
        out: list[dict[str, Any]] = []
        for page in range(1, _MAX_PAGES + 1):
            data = self._call(
                "GET",
                f"/repos/{repo}/commits/{sha}/check-runs?filter=latest&per_page=100&page={page}",
                None,
            )
            runs = data.get("check_runs")
            runs = runs if isinstance(runs, list) else []
            out.extend(_run_fact(run) for run in runs if isinstance(run, dict))
            if len(runs) < 100:
                return out
        raise GitHubError("too_many_pages", "check runs")

    def reply_review_comment(
        self, repo: str, number: int, comment_id: int, body: str
    ) -> dict[str, Any]:
        """Reply in the review thread of ``comment_id``; returns ``{comment_id, url, node_id}``."""
        self._require_allowed(repo, "review reply")
        path = f"/repos/{repo}/pulls/{int(number)}/comments/{int(comment_id)}/replies"
        data = self._call("POST", path, {"body": body})
        return {"comment_id": data.get("id"), "url": data.get("html_url")}

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """One GraphQL call as the App; GraphQL-level ``errors`` raise ``graphql_error``."""
        data = self._call("POST", "/graphql", {"query": query, "variables": variables})
        if data.get("errors"):
            raise GitHubError("graphql_error", retryable=False)
        out = data.get("data")
        return out if isinstance(out, dict) else {}

    def _thread_has_comment(self, thread_id: str, page: Any, comment_id: int) -> bool:
        """Whether review thread ``thread_id`` holds ``comment_id``, paging its comments."""
        for _ in range(_MAX_PAGES):
            page = page if isinstance(page, dict) else {}
            if any((c or {}).get("databaseId") == comment_id for c in page.get("nodes") or ()):
                return True
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return False
            data = self.graphql(
                _THREAD_COMMENTS_QUERY, {"id": thread_id, "after": info.get("endCursor")}
            )
            page = (data.get("node") or {}).get("comments")
        raise GitHubError("too_many_pages", "review thread comments")

    def find_review_thread(self, repo: str, number: int, comment_id: int) -> str | None:
        """The GraphQL id of the PR review thread holding REST comment ``comment_id``.

        Pages through every review thread of the PR (and each thread's comments)."""
        self._require_allowed(repo, "thread lookup")
        owner, name = repo.split("/", 1)
        after: str | None = None
        for _ in range(_MAX_PAGES):
            variables = {"owner": owner, "name": name, "number": int(number), "after": after}
            data = self.graphql(_THREADS_QUERY, variables)
            pull = ((data.get("repository") or {}).get("pullRequest")) or {}
            threads = pull.get("reviewThreads") or {}
            for thread in threads.get("nodes") or ():
                tid = (thread or {}).get("id")
                if isinstance(tid, str) and self._thread_has_comment(
                    tid, thread.get("comments"), int(comment_id)
                ):
                    return tid
            info = threads.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return None
            after = info.get("endCursor")
        raise GitHubError("too_many_pages", _THREADS_WHAT)

    def review_thread_matches(
        self, repo: str, number: int, thread_id: str, comment_id: int
    ) -> bool:
        """Whether ``thread_id`` is a review thread of PR ``number`` in ``repo`` holding
        ``comment_id``. The installation token can reach any thread it is installed on, so a
        caller-supplied id is never trusted unchecked."""
        self._require_allowed(repo, "thread check")
        node = self.graphql(_THREAD_NODE_QUERY, {"id": thread_id}).get("node") or {}
        pull = node.get("pullRequest") or {}
        where = (pull.get("repository") or {}).get("nameWithOwner")
        if not isinstance(where, str) or where.lower() != repo.lower():
            return False
        if pull.get("number") != int(number):
            return False
        return self._thread_has_comment(thread_id, node.get("comments"), int(comment_id))

    def list_review_threads(
        self, repo: str, number: int, *, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        """The PR's **unresolved** review threads, each described by its opening comment.

        One GraphQL ``reviewThreads`` page (100 threads) per call, at most ``max_pages``
        (default :data:`THREAD_PAGES`) pages, else ``too_many_pages``. Each item is
        ``{thread_id, comment_id, path, line, author, body}``: ``thread_id`` the GraphQL node
        id (what ``resolveReviewThread`` takes), ``comment_id`` the opening comment's REST id
        (what a reply targets), ``author`` its login with GraphQL's bare bot login given the
        REST ``[bot]`` suffix (so it compares with webhook ``author`` values), ``body``
        clipped to :data:`THREAD_BODY_MAX` characters. A thread whose opening comment cannot
        be read is left out."""
        self._require_allowed(repo, _THREADS_WHAT)
        owner, name = repo.split("/", 1)
        out: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(max_pages or THREAD_PAGES):
            variables = {"owner": owner, "name": name, "number": int(number), "after": after}
            data = self.graphql(_OPEN_THREADS_QUERY, variables)
            pull = ((data.get("repository") or {}).get("pullRequest")) or {}
            threads = pull.get("reviewThreads") or {}
            for node in threads.get("nodes") or ():
                item = _open_thread(node)
                if item is not None:
                    out.append(item)
            info = threads.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return out
            after = info.get("endCursor")
        raise GitHubError("too_many_pages", _THREADS_WHAT)

    def resolve_review_thread(self, thread_id: str) -> bool:
        """Resolve the review thread ``thread_id`` (GraphQL ``resolveReviewThread``)."""
        data = self.graphql(_RESOLVE_MUTATION, {"threadId": thread_id})
        thread = ((data.get("resolveReviewThread") or {}).get("thread")) or {}
        return bool(thread.get("isResolved"))


def _comment_fact(comment: dict[str, Any]) -> dict[str, Any]:
    via = comment.get("performed_via_github_app")
    app = via.get("id") if isinstance(via, dict) else None
    body = comment.get("body")
    return {
        "comment_id": comment.get("id"),
        "url": comment.get("html_url"),
        "body": body if isinstance(body, str) else "",
        "app_id": str(app) if isinstance(app, int) and not isinstance(app, bool) else None,
    }


def _suite_fact(suite: dict[str, Any]) -> dict[str, Any]:
    """The three fields of one check suite a settle decision needs."""
    app = suite.get("app") if isinstance(suite.get("app"), dict) else {}
    return {
        "app_slug": app.get("slug"),
        "status": suite.get("status"),
        "conclusion": suite.get("conclusion"),
    }


def _run_fact(run: dict[str, Any]) -> dict[str, Any]:
    """The fields of one check run a findings report needs (:meth:`GitHubApp.list_check_runs`)."""
    app = run.get("app") if isinstance(run.get("app"), dict) else {}
    output = run.get("output") if isinstance(run.get("output"), dict) else {}
    return {
        "name": run.get("name"),
        "app_slug": app.get("slug"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "title": output.get("title"),
        "text": output.get("text"),
        "html_url": run.get("html_url"),
    }


_MAX_PAGES = 50
_THREADS_WHAT = "review threads"
THREAD_PAGES = 10
"""Page cap of :meth:`GitHubApp.list_review_threads` (100 threads a page)."""
THREAD_BODY_MAX = 4000
"""Characters of a thread's opening comment kept by :meth:`GitHubApp.list_review_threads`."""


def _open_thread(node: Any) -> dict[str, Any] | None:
    """One unresolved thread of a ``reviewThreads`` page, or None (resolved / unreadable)."""
    if not isinstance(node, dict) or node.get("isResolved") is not False:
        return None
    tid = node.get("id")
    first = ((node.get("comments") or {}).get("nodes") or [None])[0]
    if not isinstance(tid, str) or not tid or not isinstance(first, dict):
        return None
    opener = _thread_opener(first)
    if opener is None:
        return None
    cid, login = opener
    body = first.get("body")
    line = node.get("line")
    return {
        "thread_id": tid,
        "comment_id": cid,
        "path": node.get("path") if isinstance(node.get("path"), str) else None,
        "line": line if isinstance(line, int) and not isinstance(line, bool) else None,
        "author": login,
        "body": body[:THREAD_BODY_MAX] if isinstance(body, str) else "",
    }


def _thread_opener(first: Mapping[str, Any]) -> tuple[int, str] | None:
    """The opening comment's database id and author login (a GitHub App bot's login ends
    ``[bot]``), or None when either is unreadable."""
    cid, author = first.get("databaseId"), first.get("author") or {}
    login = author.get("login") if isinstance(author, dict) else None
    if not isinstance(cid, int) or isinstance(cid, bool) or not isinstance(login, str):
        return None
    if author.get("__typename") == "Bot" and not login.endswith("[bot]"):
        login += "[bot]"
    return cid, login


_PAGE = "pageInfo{hasNextPage endCursor}"
_THREADS_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!,$after:String)"
    "{repository(owner:$owner,name:$name){pullRequest(number:$number)"
    "{reviewThreads(first:100,after:$after){" + _PAGE + " nodes{id "
    "comments(first:100){" + _PAGE + " nodes{databaseId}}}}}}}"
)
_OPEN_THREADS_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!,$after:String)"
    "{repository(owner:$owner,name:$name){pullRequest(number:$number)"
    "{reviewThreads(first:100,after:$after){" + _PAGE + " nodes{id isResolved path line "
    "comments(first:1){nodes{databaseId body author{__typename login}}}}}}}}"
)
_THREAD_NODE_QUERY = (
    "query($id:ID!){node(id:$id){... on PullRequestReviewThread{id "
    "pullRequest{number repository{nameWithOwner}} "
    "comments(first:100){" + _PAGE + " nodes{databaseId}}}}}"
)
_THREAD_COMMENTS_QUERY = (
    "query($id:ID!,$after:String){node(id:$id){... on PullRequestReviewThread"
    "{comments(first:100,after:$after){" + _PAGE + " nodes{databaseId}}}}}"
)
_RESOLVE_MUTATION = (
    "mutation($threadId:ID!){resolveReviewThread(input:{threadId:$threadId})"
    "{thread{id isResolved}}}"
)
