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
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors.secrets import resolve as resolve_secret
from culture_rules.apps.github import DEFAULT_API_BASE, GitHubApp, GitHubError, Transport
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actors import ACTORS_COLLECTION

__all__ = ["GitHubCommentPort"]

log = logging.getLogger(__name__)


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

    def _connection(self, actor_id: str | None) -> Mapping[str, Any] | None:
        if not actor_id:
            return None
        doc = self._store.get(ACTORS_COLLECTION, actor_id)
        if not doc or doc.get("enabled") is False:
            return None
        params = doc.get("params") or {}
        conn = params.get("connection")
        if params.get("surface") != "github" or not isinstance(conn, Mapping):
            return None
        return conn

    def _app(self, actor_id: str, conn: Mapping[str, Any], allowed: set[str]) -> GitHubApp | None:
        """The per-actor App (so its installation token cache survives across invocations)."""
        fingerprint = (
            conn.get("app_id"),
            conn.get("installation_id"),
            conn.get("private_key"),
            tuple(sorted(allowed)),
        )
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
            return InvocationResult.failed("actor_not_found", retryable=False)
        repo = input.get("repo")
        allowed = {str(r).lower() for r in conn.get("repos") or ()}
        if not isinstance(repo, str) or repo.lower() not in allowed:
            return InvocationResult.failed("repo_not_allowed", retryable=False)
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
