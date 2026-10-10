"""``github.comment`` action port: comment on an issue/PR through a GitHub App actor.

The step's bound actor (``context.actor``) is a stored App actor (surface ``github``) whose
``params.connection`` carries ``app_id``, ``installation_id``, ``private_key: grant:NAME``,
``webhook_secret`` and ``repos`` (the allowlist). The private key is resolved from ``grant``
on the executing host at call time and kept only in memory. A repo outside the allowlist
fails the action before any secret is read or any network call is made.

The GitHub REST API cannot deduplicate comments, so ``supports_idempotency_key`` is False.

``once_key`` (optional, d25): a non-empty string that makes the comment durable once per
(repo, PR, key), across runs, rules, budget resets and nodes. Before posting, the port
claims ``sha256(repo#number#key)`` by insert in :data:`ONCE_COLLECTION` (after it has an
installation token, so a token failure takes no claim); a claim already there completes
with the earlier comment and ``skipped: posted_before`` (or ``claimed_before`` when that
post's outcome is unknown), posting nothing. Only a post GitHub refused with a client error
(4xx but 408: no comment exists) releases the claim, so a later firing may post. Any other
failure (5xx, 408, a network error, the deadline, an unreadable answer) may follow a
created comment, so the claim stays (state ``unknown``, with the error): at most once, never
twice. A node that dies between the claim and the post leaves the claim too.

``status`` (optional bool, d26): ``true`` makes the body the **final section of the run's
chain's status comment** (:mod:`culture_rules.node.fixer_status`). The action stores it as
the chain's pending final (:meth:`~culture_rules.node.status_board.StatusBoard.finish`)
before any credential is resolved and completes at once: it never calls GitHub and never
fails the run; the status stage of the node on the App actor's machine - the comment's
single writer (:meth:`GitHubCommentPort.status_tick`) - delivers it. Outside a chain whose
rules opt in (this action, or the rule's other one, with ``status: true``) on the same
repository and PR, or when the App actor has no machine (no single writer, so no live
status), it posts a plain comment: the text made inert, then the run link. It cannot be
combined with ``once_key``.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime
from typing import Any

from culture_rules.actors.secrets import resolve as resolve_secret
from culture_rules.apps.github import DEFAULT_API_BASE, GitHubApp, GitHubError, Transport
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.store.port import DuplicateKeyError

__all__ = [
    "ONCE_COLLECTION",
    "RESOLVE_WORKERS",
    "GitHubCommentPort",
    "GitHubPrHeadPort",
    "on_base_branch",
]

log = logging.getLogger(__name__)

ONCE_COLLECTION = "github_comment_once"
"""Claims of ``github.comment`` posts made with a ``once_key`` (one document per key)."""
CLAIMED, POSTED, UNKNOWN = "claimed", "posted", "unknown"
BAD_INPUT, ACTOR_NOT_FOUND, SECRET_UNAVAILABLE = (
    "bad_input",
    "actor_not_found",
    "secret_unavailable",
)
"""Failure codes of the port (shared with the ports built on it)."""
_REFUSED = re.compile(r"http_4(?!08)\d\d")


def _refused(code: str | None) -> bool:
    """Whether GitHub answered the post with a client error (4xx but 408): no comment was
    created. A 5xx, 408, network error, deadline or unreadable answer may follow a created
    comment, so it is not a refusal."""
    return isinstance(code, str) and _REFUSED.fullmatch(code) is not None


def _options_ok(once: Any, status: Any) -> bool:
    """``once_key`` is absent or a non-empty string, ``status`` a bool, and not both."""
    if once is not None and (not isinstance(once, str) or not once):
        return False
    return isinstance(status, bool) and not (status and once is not None)


RESOLVE_WORKERS = 2
"""The most deadline-bounded App resolves (:meth:`GitHubCommentPort._app_within`) running at
once per port; it caps the threads a stuck ``grant get`` can hold."""


def repo_refusal(conn: Mapping[str, Any], repo: Any) -> tuple[set[str], str | None]:
    """The App connection's allowed repos (lower-cased), and why ``repo`` cannot be served
    through it: ``repo_not_allowed`` (not a repo name, or off the allowlist) or
    ``actor_misconfigured`` (no App or installation id); None when it can."""
    allowed = {str(r).lower() for r in conn.get("repos") or ()}
    if not GitHubApp.is_repo_name(repo) or repo.lower() not in allowed:
        return allowed, "repo_not_allowed"
    if not conn.get("app_id") or not conn.get("installation_id"):
        return allowed, "actor_misconfigured"
    return allowed, None


class GitHubCommentPort:
    """ActorPort for the ``github.comment`` action kind."""

    supports_idempotency_key = False

    def __init__(
        self,
        store: Any,
        *,
        transport: Transport | None = None,
        secrets: Callable[[str], str] | None = None,
        api_base: str = DEFAULT_API_BASE,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._transport = transport
        self._secrets = secrets
        self._api_base = api_base
        self._apps: dict[str, tuple[tuple[Any, ...], GitHubApp]] = {}
        self._resolve_slots = threading.BoundedSemaphore(RESOLVE_WORKERS)
        self._clock = clock
        self._board: Any = None

    def _connection(self, actor_id: str | None) -> Mapping[str, Any] | None:
        if not actor_id:
            return None
        return self._connection_of(self._store.get(ACTORS_COLLECTION, actor_id))

    @staticmethod
    def _connection_of(doc: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        """The GitHub connection of an actor document already read."""
        if not doc or doc.get("enabled") is False:
            return None
        params = doc.get("params") or {}
        conn = params.get("connection")
        if params.get("surface") != "github" or not isinstance(conn, Mapping):
            return None
        return conn

    @staticmethod
    def _fingerprint(conn: Mapping[str, Any], allowed: set[str]) -> tuple[Any, ...]:
        return (
            conn.get("app_id"),
            conn.get("installation_id"),
            conn.get("private_key"),
            tuple(sorted(allowed)),
        )

    def _app_within(
        self, actor_id: str, conn: Mapping[str, Any], allowed: set[str], deadline: datetime
    ) -> GitHubApp | None:
        """:meth:`_app`, bounded by ``deadline``: a cached App answers at once; a cold one is
        resolved (``grant get``) on one of at most :data:`RESOLVE_WORKERS` daemon threads.
        Past the deadline the caller gets a retryable ``deadline_exceeded`` while the worker
        finishes and caches the App, so the next call is warm; with every worker busy,
        ``lookup_busy`` at once. Raises :class:`GitHubError` for those two only."""
        cached = self._apps.get(actor_id)
        if cached is not None and cached[0] == self._fingerprint(conn, allowed):
            return cached[1]
        left = (deadline - datetime.now(UTC)).total_seconds()
        if left <= 0:
            raise GitHubError("deadline_exceeded", retryable=True)
        if not self._resolve_slots.acquire(blocking=False):
            raise GitHubError("lookup_busy", "every resolve worker is busy", retryable=True)
        result: Future[GitHubApp | None] = Future()

        def work() -> None:
            try:
                result.set_result(self._app(actor_id, conn, allowed))
            except Exception as exc:  # handed to the waiting caller
                result.set_exception(exc)
            except BaseException as exc:  # e.g. SystemExit: hand it over, then end the thread
                result.set_exception(exc)
                raise
            finally:
                self._resolve_slots.release()

        try:
            threading.Thread(target=work, name="github-app-resolve", daemon=True).start()
        except BaseException:
            self._resolve_slots.release()
            raise
        try:
            return result.result(timeout=left)
        except FutureTimeout:
            raise GitHubError("deadline_exceeded", retryable=True) from None

    def _app(self, actor_id: str, conn: Mapping[str, Any], allowed: set[str]) -> GitHubApp | None:
        """The per-actor App (so its installation token cache survives across invocations)."""
        fingerprint = self._fingerprint(conn, allowed)
        cached = self._apps.get(actor_id)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        try:
            key = (self._secrets or resolve_secret)(str(conn.get("private_key") or ""))
        except Exception:  # noqa: BLE001 - never echo secret text
            return None
        app = GitHubApp(
            app_id=conn.get("app_id", ""),
            installation_id=conn.get("installation_id", ""),
            private_key=key,
            repos=allowed,
            api_base=self._api_base,
            transport=self._transport,
        )
        self._apps[actor_id] = (fingerprint, app)
        return app

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        actor_id = context.actor or input.get("actor")
        conn = self._connection(actor_id)
        if conn is None:
            self._apps.pop(str(actor_id), None)  # deleted/disabled: free its key material
            return InvocationResult.failed(ACTOR_NOT_FOUND, retryable=False)
        repo = input.get("repo")
        allowed = {str(r).lower() for r in conn.get("repos") or ()}
        if not GitHubApp.is_repo_name(repo) or repo.lower() not in allowed:
            return InvocationResult.failed("repo_not_allowed", retryable=False)
        if not conn.get("app_id") or not conn.get("installation_id"):
            return InvocationResult.failed("actor_misconfigured", retryable=False)
        try:
            number, body = int(input["number"]), str(input["body"])
        except (KeyError, TypeError, ValueError):
            return InvocationResult.failed(BAD_INPUT, retryable=False)
        once, status = input.get("once_key"), input.get("status", False)
        if not _options_ok(once, status):
            return InvocationResult.failed(BAD_INPUT, retryable=False)
        if status:  # d26: stored first, no credential and no network (module doc)
            return self._post_status(str(actor_id), conn, allowed, (repo, number, body), context)
        app = self._app(str(actor_id), conn, allowed)
        if app is None:
            return InvocationResult.failed(SECRET_UNAVAILABLE, retryable=False)
        if once is None:
            return self._post(app, repo, number, body)
        return self._post_once(app, repo, number, body, once)

    # ------------------------------------------------------------------ d26: status comment

    @property
    def status_board(self) -> Any:
        """The :class:`~culture_rules.node.status_board.StatusBoard` of this port (lazily)."""
        if self._board is None:
            from culture_rules.node.status_board import StatusBoard  # noqa: PLC0415

            self._board = StatusBoard(self._store, clock=self._clock)
        return self._board

    def _status_app(self, actor_id: str, repo: str, deadline: datetime) -> GitHubApp:
        """The App ``actor_id`` serving ``repo``, resolved within ``deadline``
        (:meth:`_app_within`: a cold ``grant get`` never holds the status stage past its
        budget); raises :class:`GitHubError` otherwise."""
        conn = self._connection(actor_id)
        if conn is None:
            raise GitHubError(ACTOR_NOT_FOUND)
        allowed, refusal = repo_refusal(conn, repo)
        if refusal:
            raise GitHubError(refusal)
        now = self._clock() if self._clock is not None else datetime.now(UTC)
        real = datetime.now(UTC) + (deadline - now)  # the board's clock may be injected
        app = self._app_within(actor_id, conn, allowed, real)
        if app is None:
            raise GitHubError(SECRET_UNAVAILABLE)
        return app

    def status_tick(self, host: str) -> int:
        """The node's status stage (:meth:`StatusBoard.tick`): the single writer of the
        status comments of the App actors placed on ``host``; returns how many writes GitHub
        acknowledged."""
        return self.status_board.tick(self._status_app, host)

    def _post_status(
        self,
        actor_id: str,
        conn: Mapping[str, Any],
        allowed: set[str],
        comment: tuple[str, int, str],
        context: InvocationContext,
    ) -> InvocationResult:
        """``status: true`` (module doc): the chain's pending final, stored before any
        credential is resolved; outside a status chain, a plain inert comment with the run
        link (only that path resolves the App)."""
        from culture_rules.actors.secrets import known_values  # noqa: PLC0415
        from culture_rules.node.fixer_status import plain_final  # noqa: PLC0415

        repo, number, body = comment
        run = self._store.get("runs", context.run_id) if context.run_id else None
        out = self.status_board.finish(run, body, where=(repo, number))
        if out is not None:
            return InvocationResult.completed(out)
        app = self._app(actor_id, conn, allowed)
        if app is None:
            return InvocationResult.failed(SECRET_UNAVAILABLE, retryable=False)
        return self._post(app, repo, number, plain_final(body, context.run_id, known_values()))

    @staticmethod
    def _post(app: GitHubApp, repo: str, number: int, body: str) -> InvocationResult:
        try:
            out = app.post_comment(repo, number, body)
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        return InvocationResult.completed(out)

    def _post_once(
        self, app: GitHubApp, repo: str, number: int, body: str, once: str
    ) -> InvocationResult:
        """:meth:`_post` at most once per (repo, PR, ``once``) (module doc)."""
        try:
            app.installation_token()  # before the claim: a token failure sends no comment
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        key = f"{repo.lower()}#{number}#{once}"
        claim_id = hashlib.sha256(key.encode()).hexdigest()
        claim = {"id": claim_id, "repo": repo, "number": number, "once_key": once}
        try:
            self._store.insert(ONCE_COLLECTION, {**claim, "state": CLAIMED})
        except DuplicateKeyError:
            prior = self._store.get(ONCE_COLLECTION, claim_id) or {}
            done = {k: prior.get(k) for k in ("comment_id", "url")}
            seen = "posted_before" if prior.get("state") == POSTED else "claimed_before"
            return InvocationResult.completed({**done, "skipped": seen})
        result = self._post(app, repo, number, body)
        if result.outcome == "completed":
            changes = {"state": POSTED, **result.output}
            self._store.update_if(ONCE_COLLECTION, claim_id, {"state": CLAIMED}, changes)
        elif _refused(result.error):
            self._store.delete(ONCE_COLLECTION, claim_id)  # GitHub created nothing
        else:  # the comment may exist: keep the claim (at most once)
            changes = {"state": UNKNOWN, "error": result.error}
            self._store.update_if(ONCE_COLLECTION, claim_id, {"state": CLAIMED}, changes)
        return result


class GitHubPrHeadPort(GitHubCommentPort):
    """Read-only port behind a wait step's ``head_unchanged`` guard: a PR's current head SHA.

    Input ``{repo, number}`` (the repo must be in the actor's allowlist); completes with
    ``{"head_sha": ..., "base_sha": ..., "state": ..., "merged": ...}`` (the gate checks its
    ``base_sha`` with it; the guard ends a wake on a PR that is no longer open, #31 - a
    merged PR keeps its head sha). It reads, so retrying is harmless.

    d37: ``base_sha`` above is GitHub's ``base.sha``, the base *as of the PR's last push*
    (the fork point of a PR not pushed since its base moved, or a commit the head does not
    hold), never a promise about the branch now. Two optional inputs read more:

    * ``with_base_tip: true`` adds ``base_tip_sha``, the base branch's live tip (``None``
      when it could not be read: the caller falls back, never fails on it);
    * ``with_checks: true`` (d38) adds ``check_suites``, the head commit's check suites as
      ``[{app_slug, status, conclusion}]`` (``None`` when they could not be read: the
      caller falls back, never fails on it);
    * ``base_sha: <sha>`` adds ``base_on_branch``: ``True`` when that commit is the PR's
      ``base.sha`` or lies between it and the base branch's tip (it descends from
      ``base.sha`` and the tip descends from it), ``False`` when it does not, ``None``
      when GitHub could not say. A commit older than the PR's recorded base is not
      accepted: the base picks the gate policy, and an older one could weaken it.

    The executor calls it synchronously inside its tick, so the whole lookup honours the
    invocation ``deadline``: a cold private-key resolve runs on a capped worker
    (:meth:`~GitHubCommentPort._app_within`, cached once it finishes) and every HTTP call
    is bounded by the time left. Past the deadline it fails ``deadline_exceeded`` (or
    ``lookup_busy``), retryable - the wait step re-arms and asks again.
    """

    supports_idempotency_key = True

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        actor_id = context.actor or input.get("actor")
        conn = self._connection(actor_id)
        if conn is None:
            self._apps.pop(str(actor_id), None)
            return InvocationResult.failed(ACTOR_NOT_FOUND, retryable=False)
        repo = input.get("repo")
        allowed, refusal = repo_refusal(conn, repo)
        if refusal:
            return InvocationResult.failed(refusal, retryable=False)
        number, check = _head_request(input)
        if number is None:
            return InvocationResult.failed(BAD_INPUT, retryable=False)
        try:
            app = self._app_within(str(actor_id), conn, allowed, deadline)
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        if app is None:
            return InvocationResult.failed(SECRET_UNAVAILABLE, retryable=False)
        try:
            with app.deadline(deadline):
                pull = app.get_pull(repo, number)
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        out = _head_facts(pull)
        if out is None:
            return InvocationResult.failed("bad_response", retryable=True)
        ref = (pull.get("base") or {}).get("ref")
        if input.get("with_base_tip") is True:
            out["base_tip_sha"] = _base_tip(app, repo, ref, deadline)
        if check is not None:
            out["base_on_branch"] = on_base_branch(app, repo, out["base_sha"], ref, check, deadline)
        if input.get("with_checks") is True:
            out["check_suites"] = _head_suites(app, repo, out["head_sha"], deadline)
        return InvocationResult.completed(out)


def _head_request(input: Mapping[str, Any]) -> tuple[int | None, Any]:
    """The PR number and the optional ``base_sha`` to check; the number is ``None`` when
    either is malformed (``bad_input``)."""
    try:
        number = int(input["number"])
    except (KeyError, TypeError, ValueError):
        return None, None
    check = input.get("base_sha")
    if check is not None and not _FULL_SHA.fullmatch(str(check)):
        return None, None
    return number, check


def _head_facts(pull: Mapping[str, Any]) -> dict[str, Any] | None:
    """The PR's head, recorded base, state and merged flag from ``pull``; ``None`` when it
    names no head commit."""
    sha = (pull.get("head") or {}).get("sha")
    base = (pull.get("base") or {}).get("sha")
    if not isinstance(sha, str) or not sha:
        return None
    state, merged = pull.get("state"), pull.get("merged")
    return {
        "head_sha": sha,
        "base_sha": base if isinstance(base, str) else None,
        "state": state if isinstance(state, str) else None,
        "merged": merged if isinstance(merged, bool) else None,
    }


_FULL_SHA = re.compile(r"[0-9a-f]{40}")


def _base_tip(app: Any, repo: str, ref: Any, deadline: datetime) -> str | None:
    """d37: the base branch's live tip, or ``None`` when it could not be read."""
    if not isinstance(ref, str) or not ref:
        return None
    try:
        with app.deadline(deadline):
            return app.branch_tip(repo, ref)
    except GitHubError as exc:
        log.info("github.pr_head: base tip of %s not read (%s)", repo, exc.code)
        return None


def _head_suites(app: Any, repo: str, sha: str, deadline: datetime) -> list[dict[str, Any]] | None:
    """d38: the check suites of the PR's head commit, or ``None`` when they could not be read."""
    try:
        with app.deadline(deadline):
            return app.list_check_suites(repo, sha)
    except GitHubError as exc:
        log.info("github.pr_head: check suites of %s not read (%s)", repo, exc.code)
        return None


def on_base_branch(
    app: Any,
    repo: str,
    pull_base: str | None,
    ref: Any,
    sha: str,
    deadline: datetime | None = None,
) -> bool | None:
    """d37: whether ``sha`` lies between the PR's recorded base and its base branch's tip
    (both inclusive); ``None`` when GitHub could not answer. ``deadline`` bounds the two
    compare calls (omit it inside a caller's own :meth:`GitHubApp.deadline`)."""
    if sha == pull_base:
        return True
    if not isinstance(pull_base, str) or not isinstance(ref, str) or not ref:
        return None
    try:
        if deadline is None:
            return _compare_on_branch(app, repo, pull_base, ref, sha)
        with app.deadline(deadline):
            return _compare_on_branch(app, repo, pull_base, ref, sha)
    except GitHubError as exc:
        log.info("github: base of %s not compared (%s)", repo, exc.code)
        return None


def _compare_on_branch(app: Any, repo: str, pull_base: str, ref: str, sha: str) -> bool:
    if app.compare_status(repo, pull_base, sha) not in ("ahead", "identical"):
        return False
    return app.compare_status(repo, sha, ref) in ("ahead", "identical")
