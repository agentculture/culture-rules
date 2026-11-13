"""``jira.read`` action port: read a whole Jira issue through a Jira App actor.

The step's bound actor is a stored App actor (surface ``jira``), as for ``jira.comment``
(:mod:`culture_rules.extensions.jira.port`). The output is
:meth:`~culture_rules.extensions.jira.client.JiraClient.get_issue_full`'s plain data:
``{key, project, summary, description, reporter, comments, fields}``.

An issue whose project is outside ``connection.projects`` fails before any secret is read
or any network call is made. An absent or empty ``projects`` allows every project, as the
Jira guide (``docs/actors/jira.md``) and the webhook receiver read it.

The port only reads (it never comments), so a retry repeats nothing:
``supports_idempotency_key`` is True.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors.secrets import resolve as resolve_secret
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.extensions.jira.client import JiraClient, JiraError
from culture_rules.extensions.jira.projects import allowed_projects, connection_of, project_allowed
from culture_rules.transport import Transport

__all__ = ["JiraReadPort"]


def _fields(value: Any) -> list[str] | None:
    """The optional ``fields`` param as a list of names, or None when it is malformed."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return list(value)


class JiraReadPort:
    """ActorPort for the ``jira.read`` action kind."""

    supports_idempotency_key = True

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
        if not isinstance(issue, str) or not issue:
            return InvocationResult.failed("bad_input", retryable=False)
        if not project_allowed(issue, allowed_projects(conn)):
            return InvocationResult.failed("project_not_allowed", retryable=False)
        fields = _fields(input.get("fields"))
        if fields is None:
            return InvocationResult.failed("bad_input", retryable=False)
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
            out = client.get_issue_full(issue, fields)
        except JiraError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        return InvocationResult.completed(out)
