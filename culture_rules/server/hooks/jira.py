"""Jira webhook receiver: verify, extract issue keys, refetch each issue, write typed events.

Cited (cite-don't-import) from culture-nodes ``internal/api/jirawebhook.go``. The core is
framework-agnostic: :func:`handle` returns ``(status, body)``; :func:`router` wraps it in a
FastAPI ``POST /hooks/jira`` (the ``server`` extra, imported lazily).

Auth is checked **before** the body is parsed, against every ``app`` actor whose
``params.surface`` is ``jira`` (disabled ones included: the sink reports ``disabled``):

- an ``X-Hub-Signature`` header means HMAC-SHA256 of the raw body, hex, with the actor's
  ``connection.webhook_secret`` (``sha256=`` prefix optional);
- otherwise ``?token=`` compared with ``connection.webhook_token``, both reduced to SHA-256
  digests and compared with :func:`hmac.compare_digest`.

The actor whose secret or token verifies is the receiver; none verifying is a 401 and nothing
is written. Secrets are ``grant:NAME`` references resolved per request; the payload, secrets
and tokens are never logged.

Issue keys are every ``key`` / ``issueKey`` / ``issue_key`` string in the payload that matches
``^[A-Z][A-Z0-9_]*-\\d+$`` (none: 400; keys outside ``connection.projects``, when set, are
dropped). Each key is refetched through the Jira client; any refetch failure answers 502 with
``retryable`` (Jira retries) and nothing is written. ``webhookEvent`` maps to the event type:
``jira:issue_created`` -> ``jira.issue.created``, ``jira:issue_updated`` ->
``jira.issue.updated``, ``comment_created`` -> ``jira.comment.created``; anything else is
``200 {"ignored": true}``.

``delivery_id`` is the ``X-Atlassian-Webhook-Identifier`` header when present (suffixed with
``:<key>`` when the payload names several issues), else ``sha256(webhookEvent|key|timestamp)``,
so a redelivery of the same event writes exactly one event. ``author`` is the comment author's
(or the payload user's) ``accountId``; the sink tags it ``self_authored`` against
``params.self_identity``. Responses: 202 accepted/ignored/disabled, 200 when every delivery was
a duplicate.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

from culture_rules.actors.secrets import resolve as resolve_secret
from culture_rules.apps.jira import ISSUE_KEY_RE, JiraClient, JiraError
from culture_rules.events.hook_sink import DUPLICATE, sink
from culture_rules.store.port import StoragePort

__all__ = ["MAX_BODY", "SURFACE", "handle", "router"]

log = logging.getLogger(__name__)

SURFACE = "jira"
MAX_BODY = 2 * 1024 * 1024
_EVENT_TYPES = {
    "jira:issue_created": "jira.issue.created",
    "jira:issue_updated": "jira.issue.updated",
    "comment_created": "jira.comment.created",
}
_KEY_FIELDS = ("key", "issueKey", "issue_key")
_COMMENT_MAX = 500

Resolver = Callable[[str], str]
ClientFactory = Callable[[Mapping[str, Any], Resolver], Any]


def _default_client(connection: Mapping[str, Any], secrets: Resolver) -> JiraClient:
    api_secret = secrets(str(connection.get("token") or ""))
    return JiraClient(
        token=api_secret,
        site=str(connection.get("site") or ""),
        email=str(connection.get("email") or ""),
        api_base=str(connection.get("api_base") or ""),
    )


def _digest(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8")).digest()


def _verifies(
    connection: Mapping[str, Any], secrets: Resolver, body: bytes, signature: str, token: str
) -> bool:
    """Whether this actor's webhook secret (signature) or token (query) verifies."""
    try:
        if signature:
            ref = connection.get("webhook_secret")
            if not ref:
                return False
            secret = secrets(str(ref))
            if not secret:
                return False
            mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            presented = signature.removeprefix("sha256=").strip().lower()
            return hmac.compare_digest(_digest(mac), _digest(presented))
        ref = connection.get("webhook_token")
        if not ref or not token:
            return False
        expected = secrets(str(ref))
        return bool(expected) and hmac.compare_digest(_digest(token), _digest(expected))
    except Exception:  # noqa: BLE001 - an unresolvable secret never verifies; no detail logged
        log.warning("jira webhook: secret reference could not be resolved")
        return False


def _jira_actors(store: StoragePort) -> list[Mapping[str, Any]]:
    return [
        a
        for a in store.find("actors", {"kind": "app"})
        if (a.get("params") or {}).get("surface") == SURFACE
    ]


def _issue_keys(value: Any) -> list[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for k, child in node.items():
                if k in _KEY_FIELDS and isinstance(child, str) and ISSUE_KEY_RE.match(child):
                    found.add(child)
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return sorted(found)


def _name(value: Any, *fields: str) -> str | None:
    if isinstance(value, Mapping):
        for field in fields:
            if isinstance(value.get(field), str):
                return value[field]
    return None


def _issue_data(issue: Mapping[str, Any], key: str, site: str, base: str) -> dict[str, Any]:
    fields = issue.get("fields") if isinstance(issue.get("fields"), Mapping) else {}
    project = _name(fields.get("project"), "key") or key.split("-")[0]
    host = site.removeprefix("https://").strip("/") if site else ""
    root = f"https://{host}" if host else base.rstrip("/")
    return {
        "key": key,
        "project": project,
        "summary": fields.get("summary"),
        "status": _name(fields.get("status"), "name"),
        "assignee": _name(fields.get("assignee"), "accountId"),
        "updated": fields.get("updated"),
        "url": f"{root}/browse/{key}" if root else None,
    }


def _delivery_id(header: str, event: str, key: str, timestamp: Any, n_keys: int) -> str:
    if header:
        return header if n_keys == 1 else f"{header}:{key}"
    raw = f"{event}|{key}|{timestamp}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _error(status: int, error: str, **extra: Any) -> tuple[int, dict]:
    return status, {"error": error, **extra}


def handle(  # noqa: PLR0911, C901 - a linear verify -> parse -> fetch -> write pipeline
    store: StoragePort,
    *,
    body: bytes,
    headers: Mapping[str, str],
    query: Mapping[str, str],
    secrets: Resolver | None = None,
    client_factory: ClientFactory | None = None,
) -> tuple[int, dict]:
    """Process one Jira delivery; returns ``(http_status, json_body)``."""
    secrets = secrets or resolve_secret
    client_factory = client_factory or _default_client
    if len(body) > MAX_BODY:
        return _error(413, "body_too_large")
    hdr = {str(k).lower(): v for k, v in headers.items()}
    signature = (hdr.get("x-hub-signature") or "").strip()
    token = query.get("token") or ""

    actor = None
    for candidate in _jira_actors(store):
        connection = (candidate.get("params") or {}).get("connection") or {}
        if _verifies(connection, secrets, body, signature, token):
            actor = candidate
            break
    if actor is None:
        return _error(401, "unauthorized")
    connection = actor["params"]["connection"]

    try:
        payload = json.loads(body)
    except ValueError:
        return _error(400, "bad_json")
    if not isinstance(payload, Mapping):
        return _error(400, "bad_json")

    event = payload.get("webhookEvent")
    etype = _EVENT_TYPES.get(event) if isinstance(event, str) else None
    if etype is None:
        return 200, {"ignored": True}
    keys = _issue_keys(payload)
    if not keys:
        return _error(400, "no_issue_key")
    allowed = connection.get("projects")
    if allowed:
        keys = [k for k in keys if k.split("-")[0] in allowed]
        if not keys:
            return 200, {"ignored": True}

    comment = payload.get("comment") if isinstance(payload.get("comment"), Mapping) else None
    author_src = comment.get("author") if comment else payload.get("user")
    author = _name(author_src, "accountId")

    try:
        client = client_factory(connection, secrets)
        issues = {key: client.get_issue(key) for key in keys}
    except JiraError as exc:
        return _error(502, "refetch_failed", code=exc.code, retryable=exc.retryable)
    except Exception as exc:  # noqa: BLE001 - e.g. an unresolvable API token reference
        log.warning("jira webhook: refetch failed (%s)", type(exc).__name__)
        return _error(502, "refetch_failed", code=type(exc).__name__, retryable=True)

    base = str(connection.get("api_base") or "")
    site = str(connection.get("site") or "")
    delivery_header = (hdr.get("x-atlassian-webhook-identifier") or "").strip()
    results = []
    for key in keys:
        data = _issue_data(issues[key], key, site, base)
        if comment is not None:
            text = comment.get("body")
            text = text if isinstance(text, str) else json.dumps(text)
            data["comment_id"] = str(comment.get("id")) if comment.get("id") is not None else None
            data["comment_body"] = text[:_COMMENT_MAX]
        delivery = _delivery_id(delivery_header, event, key, payload.get("timestamp"), len(keys))
        outcome = sink(store, actor, etype, data, delivery, author)
        results.append({"key": key, "outcome": outcome})
    status = 200 if all(r["outcome"] == DUPLICATE for r in results) else 202
    return status, {"results": results}


def router(
    store: StoragePort,
    *,
    secrets: Resolver | None = None,
    client_factory: ClientFactory | None = None,
) -> Any:
    """A FastAPI ``APIRouter`` serving ``POST /hooks/jira`` (needs the ``server`` extra)."""
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    api = APIRouter()

    async def jira_hook(request):
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BODY:
            return JSONResponse({"error": "body_too_large"}, status_code=413)
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY:
                return JSONResponse({"error": "body_too_large"}, status_code=413)
            chunks.append(chunk)
        status, out = await run_in_threadpool(
            handle,
            store,
            body=b"".join(chunks),
            headers=dict(request.headers),
            query=dict(request.query_params),
            secrets=secrets,
            client_factory=client_factory,
        )
        return JSONResponse(out, status_code=status)

    # ``from __future__ import annotations`` would leave this a string FastAPI cannot resolve
    # (``Request`` is a local lazy import), so the annotation is set explicitly.
    jira_hook.__annotations__ = {"request": Request}
    api.add_api_route("/hooks/jira", jira_hook, methods=["POST"])
    return api
