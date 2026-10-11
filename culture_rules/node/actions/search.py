"""``search.web`` action port: search the web through a search App actor (Brave).

The step's bound actor (``context.actor``, else ``params.actor``) is a stored, enabled App
actor with surface ``search`` whose ``params.connection.api_key`` is a ``grant:NAME``
reference. The parameters are checked first (``query`` required; ``count``, ``freshness``,
``country``, ``lang`` optional), so a bad one fails the action before any secret is read or
any call made. The key is then resolved on the executing host at call time and kept only in
memory; the call goes only to Brave (:mod:`culture_rules.apps.brave`).

Outputs: ``{results: [{title, url, snippet, age, source}, ...], query, count}`` with
``count == len(results)``; results are capped in number and per-field length by the client.

A search is a read, so a blind retry is harmless: ``supports_idempotency_key`` is True.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors.secrets import resolve as resolve_secret
from culture_rules.apps.brave import BraveError, BraveSearch, build_query
from culture_rules.apps.github import Transport
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actors import ACTORS_COLLECTION

__all__ = ["SearchWebPort"]

#: The optional search.web params, passed through to the client by name when given.
_OPTIONAL = ("count", "freshness", "country", "lang")


class SearchWebPort:
    """ActorPort for the ``search.web`` action kind."""

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

    def _connection(self, actor_id: str | None) -> Mapping[str, Any] | None:
        if not actor_id:
            return None
        doc = self._store.get(ACTORS_COLLECTION, actor_id)
        if not doc or doc.get("enabled") is False:
            return None
        params = doc.get("params") or {}
        conn = params.get("connection")
        if params.get("surface") != "search" or not isinstance(conn, Mapping):
            return None
        return conn

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        conn = self._connection(context.actor or input.get("actor"))
        if conn is None:
            return InvocationResult.failed("actor_not_found", retryable=False)
        args = {name: input[name] for name in _OPTIONAL if input.get(name) is not None}
        query = input.get("query")
        try:
            build_query(query, **args)
        except BraveError as exc:
            return InvocationResult.failed(exc.code, retryable=False)
        try:
            key = (self._secrets or resolve_secret)(str(conn.get("api_key") or ""))
        except Exception:  # noqa: BLE001 - never echo secret text
            return InvocationResult.failed("secret_unavailable", retryable=False)
        client = BraveSearch(api_key=key, transport=self._transport)
        try:
            results = client.search(query, **args)
        except BraveError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        return InvocationResult.completed(
            {"results": results, "query": query, "count": len(results)}
        )
