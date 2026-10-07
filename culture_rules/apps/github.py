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
  :meth:`GitHubApp.resolve_review_thread` (GraphQL ``resolveReviewThread``), both as the App.
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
import urllib.error
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
"""The PR facts every fixer-trigger event carries under the same names (d14)."""


def _get(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def pr_facts(pr: Any) -> dict[str, Any]:
    """The :data:`PR_FACT_FIELDS` of one pull-request document (a webhook's ``pull_request``
    object or a REST ``GET /repos/{repo}/pulls/{n}`` result; same shape). Missing parts are
    ``None`` (``draft`` is ``False``); a non-mapping yields ``{}``."""
    if not isinstance(pr, dict):
        return {}
    head, base = pr.get("head"), pr.get("base")
    return {
        "head_sha": _get(head, "sha"),
        "head_branch": _get(head, "ref"),
        "head_repo": _get(head, "repo", "full_name") or _get(head, "full_name"),
        "base_repo": _get(base, "repo", "full_name") or _get(base, "full_name"),
        "base_branch": _get(base, "ref"),
        "base_sha": _get(base, "sha"),
        "draft": bool(pr.get("draft")),
        "pr_author": _get(pr, "user", "login"),
    }


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
        (retryable) without touching the network. Held in a ContextVar, so concurrent
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
        except Exception as exc:  # noqa: BLE001 - network failure is retryable; text withheld
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
            for suite in suites:
                if isinstance(suite, dict):
                    app = suite.get("app") if isinstance(suite.get("app"), dict) else {}
                    out.append(
                        {
                            "app_slug": app.get("slug"),
                            "status": suite.get("status"),
                            "conclusion": suite.get("conclusion"),
                        }
                    )
            if len(suites) < 100:
                return out
        raise GitHubError("too_many_pages", "check suites")

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
        raise GitHubError("too_many_pages", "review threads")

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

    def resolve_review_thread(self, thread_id: str) -> bool:
        """Resolve the review thread ``thread_id`` (GraphQL ``resolveReviewThread``)."""
        data = self.graphql(_RESOLVE_MUTATION, {"threadId": thread_id})
        thread = ((data.get("resolveReviewThread") or {}).get("thread")) or {}
        return bool(thread.get("isResolved"))


_MAX_PAGES = 50
_PAGE = "pageInfo{hasNextPage endCursor}"
_THREADS_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!,$after:String)"
    "{repository(owner:$owner,name:$name){pullRequest(number:$number)"
    "{reviewThreads(first:100,after:$after){" + _PAGE + " nodes{id "
    "comments(first:100){" + _PAGE + " nodes{databaseId}}}}}}}"
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
