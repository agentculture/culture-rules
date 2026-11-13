"""``jira.comment`` action port: comment on a Jira issue through a Jira App actor.

The step's bound actor (``context.actor``) is a stored App actor (surface ``jira``) whose
``params.connection`` carries ``site``, ``email``, ``token: grant:NAME`` and ``projects``
(the allowlist). The token is resolved from ``grant`` on the executing host at call time and
kept only in memory. A malformed issue key fails ``bad_input`` and one whose project is outside
the allowlist fails ``project_not_allowed``, both before any secret is read or any network call
is made.

Jira's comment API cannot deduplicate, so ``supports_idempotency_key`` is False.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors.secrets import resolve as resolve_secret
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.extensions.jira.client import JiraClient, JiraError
from culture_rules.extensions.jira.projects import (
    allowed_projects,
    connection_of,
    project_allowed,
    project_of,
)
from culture_rules.transport import Transport

__all__ = ["JiraCommentPort"]

log = logging.getLogger(__name__)


class JiraCommentPort:
    """ActorPort for the ``jira.comment`` action kind."""

    supports_idempotency_key = False

    def __init__(
        self,
        store: Any,
        *,
        transport: Transport | None = None,
        secrets: Callable[[str], str] | None = None,
    ) -> None:
        self._store = store
        self._transport = transport
        self._secrets = secrets

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        actor_id = context.actor or input.get("actor")
        conn = connection_of(self._store, actor_id)
        if conn is None:
            return InvocationResult.failed("actor_not_found", retryable=False)
        issue = input.get("issue")
        if not isinstance(issue, str) or not project_of(issue):
            return InvocationResult.failed("bad_input", retryable=False)
        if not project_allowed(issue, allowed_projects(conn)):
            return InvocationResult.failed("project_not_allowed", retryable=False)
        if "body" not in input:
            return InvocationResult.failed("bad_input", retryable=False)
        body = str(input["body"])
        try:
            token = (self._secrets or resolve_secret)(str(conn.get("token") or ""))
        except Exception:  # noqa: BLE001 - never echo secret text
            return InvocationResult.failed("secret_unavailable", retryable=False)
        client = JiraClient(
            token=token,
            site=str(conn.get("site") or ""),
            email=str(conn.get("email") or ""),
            api_base=str(conn.get("api_base") or ""),
            transport=self._transport,
        )
        try:
            out = client.add_comment(issue, body)
        except JiraError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        return InvocationResult.completed({"comment_id": out.get("comment_id"), "issue": issue})
