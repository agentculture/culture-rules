"""GitHub webhook receiver: HMAC check, delivery-id dedupe, typed ``github.*`` events.

Cited (cite-don't-import) from culture-nodes ``internal/api/githubwebhook.go``: a 2 MiB body
limit, a constant-time ``X-Hub-Signature-256`` comparison **before** the JSON is parsed, a
required ``X-GitHub-Delivery`` header, and a fast answer. Unlike the Go original, the secret is
the App actor's ``connection.webhook_secret`` (a ``grant:NAME`` reference, resolved at call
time) and the write goes through :func:`culture_rules.events.hook_sink.sink`, so redelivery is
deduped by delivery id. The handler only records the event; the ``events`` change feed fires
triggers elsewhere, so no rule is evaluated and no run is started in the request path.

:func:`handle` is framework-agnostic (``(status, json body)``); :func:`router` wraps it in a
FastAPI ``POST /hooks/github`` (the ``server`` extra, imported lazily). The endpoint is public:
authentication is the signature alone, failures are a bare 401 that does not say whether the
app, the header or the secret was wrong, and logs carry the outcome and event type only -
never the payload, signature or secret.
"""

# No ``from __future__ import annotations``: FastAPI resolves the route's lazily imported
# ``Request`` annotation from the enclosing scope.
import hashlib
import hmac
import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

from culture_rules.actors.secrets import resolve
from culture_rules.events.hook_sink import (
    BAD_REQUEST,
    DUPLICATE,
    SELF_TAG_EXEMPT_TYPES,
    TOO_LARGE,
    UNAUTHORIZED,
    record_outcome,
    sink,
)

__all__ = ["MAX_BODY_BYTES", "SURFACE", "handle", "router"]

_log = logging.getLogger(__name__)

SURFACE = "github"
MAX_BODY_BYTES = 2 << 20
_COMMENT_MAX = 500
_UNAUTHORIZED = (401, {"error": "unauthorized"})

# (X-GitHub-Event, payload action) -> typed event.
_TYPES = {
    ("pull_request", "opened"): "github.pr.opened",
    ("pull_request", "closed"): "github.pr.closed",
    ("pull_request", "reopened"): "github.pr.reopened",
    ("pull_request", "synchronize"): "github.pr.synchronize",
    ("pull_request", "ready_for_review"): "github.pr.ready",
    ("issue_comment", "created"): "github.comment.created",
    ("issues", "opened"): "github.issue.opened",
    ("pull_request_review", "submitted"): "github.review.submitted",
    ("pull_request_review_comment", "created"): "github.review_comment.created",
    ("check_suite", "completed"): "github.checks.suite_completed",
    ("workflow_run", "completed"): "github.checks.workflow_completed",
}
_EVENTS = frozenset(key[0] for key in _TYPES)
_IGNORED = (200, {"ignored": True})
_TOO_LARGE = "body too large"


def _app_actors(store: Any) -> list[Mapping[str, Any]]:
    """Every GitHub app actor, disabled ones included (the sink hides their state)."""
    out = []
    for doc in store.find("actors"):
        params = doc.get("params") or {}
        if doc.get("kind") == "app" and params.get("surface") == SURFACE:
            out.append(doc)
    return out


def _select_actor(store: Any, target_id: str) -> Mapping[str, Any] | None:
    actors = _app_actors(store)
    if len(actors) == 1 and not target_id:
        return actors[0]
    for doc in actors:
        conn = (doc.get("params") or {}).get("connection") or {}
        if target_id and str(conn.get("app_id", "")) == target_id:
            return doc
    return None


def _verified(actor: Mapping[str, Any], body: bytes, signature: str, secrets: Callable) -> bool:
    conn = (actor.get("params") or {}).get("connection") or {}
    ref = conn.get("webhook_secret")
    if not isinstance(ref, str) or not signature.startswith("sha256="):
        return False
    try:
        secret = secrets(ref)
    except Exception:  # noqa: BLE001 - never surface why; the value must not leak
        return False
    if not isinstance(secret, str) or not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


def _dig(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, Mapping):
            return None
        obj = obj.get(key)
    return obj


def _data(event: str, action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """A compact, stable subset of the payload - never the whole thing."""
    subject = payload.get("pull_request") or payload.get("issue") or {}
    data: dict[str, Any] = {
        "repository": _dig(payload, "repository", "full_name"),
        "number": _dig(subject, "number"),
        "title": _dig(subject, "title"),
        "url": _dig(subject, "html_url"),
        "author": _dig(payload, "sender", "login"),
        "action": action,
    }
    if event == "pull_request":
        data["merged"] = bool(_dig(subject, "merged"))
        _enrich_pr(data, payload)
    elif event == "issue_comment":
        body = _dig(payload, "comment", "body")
        data["comment"] = body[:_COMMENT_MAX] if isinstance(body, str) else None
    elif event == "pull_request_review":
        data["review_state"] = _dig(payload, "review", "state")
        _enrich_pr(data, payload)
    elif event == "pull_request_review_comment":
        comment = _dig(payload, "comment")
        if isinstance(comment, Mapping):
            comment_user = comment.get("user")
            if isinstance(comment_user, Mapping):
                login = comment_user.get("login")
                if isinstance(login, str):
                    data["author"] = login
        _enrich_pr(data, payload)
    elif event == "check_suite":
        cs = _dig(payload, "check_suite")
        if isinstance(cs, Mapping):
            data["head_sha"] = cs.get("head_sha")
            data["head_branch"] = cs.get("head_branch")
            prs = cs.get("pull_requests")
            data["pr_numbers"] = (
                [
                    pr["number"]
                    for pr in prs
                    if isinstance(pr, Mapping) and isinstance(pr.get("number"), int)
                ]
                if isinstance(prs, list)
                else []
            )
            data["app_slug"] = _dig(cs, "app", "slug")
            data["workflow_name"] = None
            data["status"] = cs.get("status")
            data["conclusion"] = cs.get("conclusion")
    elif event == "workflow_run":
        wr = _dig(payload, "workflow_run")
        if isinstance(wr, Mapping):
            data["head_sha"] = wr.get("head_sha")
            data["head_branch"] = wr.get("head_branch")
            prs = wr.get("pull_requests")
            data["pr_numbers"] = (
                [
                    pr["number"]
                    for pr in prs
                    if isinstance(pr, Mapping) and isinstance(pr.get("number"), int)
                ]
                if isinstance(prs, list)
                else []
            )
            data["app_slug"] = None
            data["workflow_name"] = wr.get("name")
            data["status"] = wr.get("status")
            data["conclusion"] = wr.get("conclusion")
    return data


def _enrich_pr(data: dict[str, Any], payload: Mapping[str, Any]) -> None:
    """Add head/base PR fields from the pull_request sub-payload."""
    pr = _dig(payload, "pull_request")
    if not isinstance(pr, Mapping):
        return
    head = _dig(pr, "head")
    base = _dig(pr, "base")
    if isinstance(head, Mapping):
        data["head_sha"] = head.get("sha")
        data["head_branch"] = head.get("ref")
        repo = _dig(head, "repo", "full_name") or head.get("full_name")
        data["head_repo"] = repo
    if isinstance(base, Mapping):
        data["base_repo"] = _dig(base, "repo", "full_name") or _dig(base, "full_name")
        data["base_branch"] = base.get("ref")
    data["draft"] = bool(pr.get("draft"))
    data["pr_author"] = _dig(pr, "user", "login")


def handle(
    store: Any,
    *,
    body: bytes,
    headers: Mapping[str, str],
    query: Mapping[str, str],
    secrets: Callable[[str], str] = resolve,
    on_check: Callable[[Mapping[str, Any]], Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Verify and record one GitHub delivery; return ``(status, json body)``.

    ``on_check`` (the once-per-SHA settler) is called with the event data of an accepted or
    redelivered check completion, so a failed earlier attempt is retried; it never changes the
    response and its failures are logged only."""
    del query  # GitHub signs the body; nothing in the query is trusted or used
    if len(body) > MAX_BODY_BYTES:
        record_outcome(store, SURFACE, TOO_LARGE)
        return 413, {"error": _TOO_LARGE}
    h = {k.lower(): v for k, v in headers.items()}
    actor = _select_actor(store, h.get("x-github-hook-installation-target-id", "").strip())
    if actor is None or not _verified(actor, body, h.get("x-hub-signature-256", ""), secrets):
        _log.warning("github hook refused: unauthorized")
        record_outcome(store, SURFACE, UNAUTHORIZED)
        return _UNAUTHORIZED
    delivery = h.get("x-github-delivery", "").strip()
    if not delivery:
        record_outcome(store, SURFACE, BAD_REQUEST, actor.get("id"))
        return 400, {"error": "missing delivery id"}
    event = h.get("x-github-event", "")
    if event == "ping":
        return 200, {"pong": True}
    if event not in _EVENTS:
        return _IGNORED
    try:
        payload = json.loads(body)
    except ValueError:
        record_outcome(store, SURFACE, BAD_REQUEST, actor.get("id"))
        return 400, {"error": "invalid payload"}
    if not isinstance(payload, dict):
        record_outcome(store, SURFACE, BAD_REQUEST, actor.get("id"))
        return 400, {"error": "invalid payload"}
    action = payload.get("action")
    etype = _TYPES.get((event, action))
    if etype is None:
        return _IGNORED
    data = _data(event, action, payload)
    outcome = sink(store, actor, etype, data, delivery, data["author"])
    if (
        on_check is not None
        and etype in SELF_TAG_EXEMPT_TYPES
        and outcome in ("accepted", DUPLICATE)
    ):
        try:
            on_check(data)
        except Exception:  # noqa: BLE001 - arming failed after the sink stored the event
            # 5xx so GitHub redelivers; the sink dedupes the delivery id and a duplicate
            # re-runs on_check, so the SHA is armed on the retry rather than lost.
            _log.warning("check settle failed type=%s", etype)
            return 503, {"error": "settle failed, retry"}
    if outcome == DUPLICATE:
        return 200, {"duplicate": True}
    return 202, {"accepted": True}


def router(
    store: Any,
    *,
    secrets: Callable[[str], str] | None = None,
    on_check: Callable[[Mapping[str, Any]], Any] | None = None,
) -> Any:
    """A FastAPI router with ``POST /hooks/github`` (needs the ``server`` extra)."""
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    api = APIRouter()
    resolver = secrets or resolve

    @api.post("/hooks/github", include_in_schema=False)
    async def github_hook(request: Request) -> JSONResponse:
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            record_outcome(store, SURFACE, TOO_LARGE)
            return JSONResponse({"error": _TOO_LARGE}, status_code=413)
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                record_outcome(store, SURFACE, TOO_LARGE)
                return JSONResponse({"error": _TOO_LARGE}, status_code=413)
            chunks.append(chunk)
        # a thread: handling may list check suites over the network (the settler)
        status, out = await run_in_threadpool(
            handle,
            store,
            body=b"".join(chunks),
            headers=request.headers,
            query=request.query_params,
            secrets=resolver,
            on_check=on_check,
        )
        return JSONResponse(out, status_code=status)

    return api
