"""``github.comment`` action port: comment on an issue/PR through a GitHub App actor.

The step's bound actor (``context.actor``) is a stored App actor (surface ``github``) whose
``params.connection`` carries ``app_id``, ``installation_id``, ``private_key: grant:NAME``,
``webhook_secret`` and ``repos`` (the allowlist). The private key is resolved from ``grant``
on the executing host at call time and kept only in memory. A repo outside the allowlist
fails the action before any secret is read or any network call is made.

The GitHub REST API cannot deduplicate comments, so ``supports_idempotency_key`` is False.
"""

from __future__ import annotations

import logging
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

__all__ = ["RESOLVE_WORKERS", "GitHubCommentPort", "GitHubPrHeadPort"]

log = logging.getLogger(__name__)

RESOLVE_WORKERS = 2
"""The most deadline-bounded App resolves (:meth:`GitHubCommentPort._app_within`) running at
once per port; it caps the threads a stuck ``grant get`` can hold."""


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
    ) -> None:
        self._store = store
        self._transport = transport
        self._secrets = secrets
        self._api_base = api_base
        self._apps: dict[str, tuple[tuple[Any, ...], GitHubApp]] = {}
        self._resolve_slots = threading.BoundedSemaphore(RESOLVE_WORKERS)

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
            return InvocationResult.failed("actor_not_found", retryable=False)
        repo = input.get("repo")
        allowed = {str(r).lower() for r in conn.get("repos") or ()}
        if not GitHubApp.is_repo_name(repo) or repo.lower() not in allowed:
            return InvocationResult.failed("repo_not_allowed", retryable=False)
        if not conn.get("app_id") or not conn.get("installation_id"):
            return InvocationResult.failed("actor_misconfigured", retryable=False)
        try:
            number, body = int(input["number"]), str(input["body"])
        except (KeyError, TypeError, ValueError):
            return InvocationResult.failed("bad_input", retryable=False)
        app = self._app(str(actor_id), conn, allowed)
        if app is None:
            return InvocationResult.failed("secret_unavailable", retryable=False)
        try:
            out = app.post_comment(repo, number, body)
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        return InvocationResult.completed(out)


class GitHubPrHeadPort(GitHubCommentPort):
    """Read-only port behind a wait step's ``head_unchanged`` guard: a PR's current head SHA.

    Input ``{repo, number}`` (the repo must be in the actor's allowlist); completes with
    ``{"head_sha": ..., "base_sha": ...}`` (the gate checks its ``base_sha`` with it). It
    reads, so retrying is harmless.

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
            return InvocationResult.failed("actor_not_found", retryable=False)
        repo = input.get("repo")
        allowed = {str(r).lower() for r in conn.get("repos") or ()}
        if not GitHubApp.is_repo_name(repo) or repo.lower() not in allowed:
            return InvocationResult.failed("repo_not_allowed", retryable=False)
        if not conn.get("app_id") or not conn.get("installation_id"):
            return InvocationResult.failed("actor_misconfigured", retryable=False)
        try:
            number = int(input["number"])
        except (KeyError, TypeError, ValueError):
            return InvocationResult.failed("bad_input", retryable=False)
        try:
            app = self._app_within(str(actor_id), conn, allowed, deadline)
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        if app is None:
            return InvocationResult.failed("secret_unavailable", retryable=False)
        try:
            with app.deadline(deadline):
                pull = app.get_pull(repo, number)
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        sha = (pull.get("head") or {}).get("sha")
        base = (pull.get("base") or {}).get("sha")
        if not isinstance(sha, str) or not sha:
            return InvocationResult.failed("bad_response", retryable=True)
        return InvocationResult.completed(
            {"head_sha": sha, "base_sha": base if isinstance(base, str) else None}
        )
