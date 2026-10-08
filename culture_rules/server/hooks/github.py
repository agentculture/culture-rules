"""GitHub webhook receiver: HMAC check, delivery-id dedupe, typed ``github.*`` events.

Cited (cite-don't-import) from culture-nodes ``internal/api/githubwebhook.go``: a 2 MiB body
limit, a constant-time ``X-Hub-Signature-256`` comparison **before** the JSON is parsed, a
required ``X-GitHub-Delivery`` header, and a fast answer. Unlike the Go original, the secret is
the App actor's ``connection.webhook_secret`` (a ``grant:NAME`` reference, resolved at call
time) and the write goes through :func:`culture_rules.events.hook_sink.sink`, so redelivery is
deduped by delivery id. The handler only records the event; the ``events`` change feed fires
triggers elsewhere, so no rule is evaluated and no run is started in the request path.

:func:`handle` is framework-agnostic (``(status, json body)``); :func:`router` wraps it in a
FastAPI ``POST /hooks/github`` (the ``server`` extra, imported lazily).

PR facts (d14): every PR-scoped event (``github.pr.*``, ``github.review.submitted``,
``github.review_comment.created`` and ``github.comment.created`` on a pull request) carries
the same :data:`~culture_rules.apps.github.PR_FACT_FIELDS` (``head_sha``, ``head_branch``,
``head_repo``, ``base_repo``, ``base_branch``, ``base_sha``, ``draft``, ``pr_author``). An
``issue_comment`` payload has no head/base, so a comment on a PR is enriched through the
read-only ``pull`` lookup (the GitHub App, bounded by :data:`PULL_LOOKUP_TIMEOUT_S`) and
marked ``pr_enriched``; a failed lookup stores the comment without the PR fields and
``pr_enriched: false`` (fail-closed for the fixer's condition); so does an answer missing any
valid PR fact. On every type a missing or malformed fact is omitted rather than null, so it
never compares equal (a deleted fork has no ``head_repo``). The lookup runs only when the
sink would store the delivery, so a redelivery never re-reads the PR. The endpoint is public:
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
import re
from collections.abc import Callable, Mapping
from typing import Any

from culture_rules.actors.secrets import resolve
from culture_rules.apps.github import PR_FACT_FIELDS, complete_pr_facts, pr_facts
from culture_rules.events.hook_sink import (
    BAD_REQUEST,
    DUPLICATE,
    SELF_TAG_EXEMPT_TYPES,
    TOO_LARGE,
    UNAUTHORIZED,
    event_id_for,
    record_outcome,
    sink,
)
from culture_rules.events.ingest import EVENTS_COLLECTION

__all__ = [
    "MAX_BODY_BYTES",
    "PULL_LOOKUP_TIMEOUT_S",
    "SURFACE",
    "PullLookup",
    "comment_intent",
    "handle",
    "router",
]

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
_COMMENT_TYPE = "github.comment.created"

PULL_LOOKUP_TIMEOUT_S = 5.0
"""The bound the production wiring puts on the PR-comment lookup (GitHub gives a delivery
10 s to answer), token exchange included."""

PullLookup = Callable[[str, int], Mapping[str, Any]]
"""``(repo, number) -> the pull request document``: a read-only App lookup (d14)."""


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


INTENT_MAX_CHARS = 10_000
"""How much of a comment body :func:`comment_intent` reads (a bound on the regex work)."""
_COMMAND_RE = re.compile(r"/([a-z][a-z0-9_-]{0,31})(?=\s|$)", re.IGNORECASE)
_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")
_BODY_PATHS = {
    "issue_comment": ("comment", "body"),
    "pull_request_review": ("review", "body"),
    "pull_request_review_comment": ("comment", "body"),
}


def comment_intent(body: Any, self_identity: Any) -> dict[str, str]:
    """What a comment asks of the App (d21): ``command`` - the body's first word when the
    body starts with ``/`` (``/fix``), lowercased; ``mention`` - ``@<slug>`` when the body
    mentions the App (``self_identity`` without ``[bot]``) outside quoted lines and code.
    Each is omitted when absent, so a rule comparing it is false. Only the first
    :data:`INTENT_MAX_CHARS` characters are read."""
    if not isinstance(body, str):
        return {}
    text = body[:INTENT_MAX_CHARS]
    out: dict[str, str] = {}
    command = _COMMAND_RE.match(text.lstrip())
    if command:
        out["command"] = "/" + command.group(1).lower()
    slug = self_identity.strip() if isinstance(self_identity, str) else ""
    if slug.lower().endswith("[bot]"):
        slug = slug[: -len("[bot]")]
    if not _SLUG_RE.match(slug):
        return out
    visible = _INLINE_CODE_RE.sub(" ", _FENCE_RE.sub(" ", text))
    lines = [ln for ln in visible.splitlines() if not ln.lstrip().startswith(">")]
    mention = re.compile(
        r"(?<![\w@.-])@" + re.escape(slug) + r"(?:\[bot\])?(?![\w-])", re.IGNORECASE
    )
    if mention.search("\n".join(lines)):
        out["mention"] = "@" + slug.lower()
    return out


def _enrich_pr(data: dict[str, Any], payload: Mapping[str, Any]) -> None:
    """Add the PR facts (:data:`PR_FACT_FIELDS`) from the pull_request sub-payload; a missing
    or malformed fact is omitted (:func:`pr_facts`), never stored as null."""
    data.update(pr_facts(_dig(payload, "pull_request")))


def _is_pr_comment(event: str, payload: Mapping[str, Any]) -> bool:
    """An ``issue_comment`` on a pull request (GitHub marks the issue with a ``pull_request``
    stub; a plain issue has none)."""
    return event == "issue_comment" and isinstance(_dig(payload, "issue", "pull_request"), Mapping)


def _would_store(store: Any, actor: Mapping[str, Any], etype: str, delivery: str) -> bool:
    """Whether :func:`sink` would insert this delivery (so a lookup is worth making): the
    actor is enabled, declares the type, and the delivery is not already stored. A
    redelivery is never looked up again, so it cannot change what was stored."""
    if actor.get("enabled", True) is False:
        return False
    if etype not in ((actor.get("params") or {}).get("events") or ()):
        return False
    return store.get(EVENTS_COLLECTION, event_id_for(SURFACE, delivery)) is None


def _enrich_comment(data: dict[str, Any], pull: PullLookup | None) -> None:
    """Add the PR facts to a PR comment through the read-only App lookup (d14).

    Fail-closed: when there is no lookup seam, or it fails (network, allowlist, timeout), or
    its answer lacks a valid value for any PR fact (:func:`complete_pr_facts`: an empty or
    malformed mapping, a null repo, a short SHA, a non-bool ``draft``), the comment is stored
    *without* the PR fields and ``pr_enriched: false``, so a condition that needs
    ``head_repo == base_repo`` and ``draft == false`` does not match - rather than dropping
    the comment or holding the delivery open."""
    repo, number = data.get("repository"), data.get("number")
    facts: dict[str, Any] | None = None
    if pull is not None and isinstance(repo, str) and isinstance(number, int):
        try:
            pr = pull(repo, number)
            if isinstance(pr, Mapping):
                facts = complete_pr_facts(dict(pr))
                if facts is None:
                    _log.warning("github pr comment lookup failed (malformed)")
        except Exception as exc:  # noqa: BLE001 - enrichment must never fail the delivery
            _log.warning("github pr comment lookup failed (%s)", getattr(exc, "code", "error"))
            facts = None
    for key in (*PR_FACT_FIELDS, "state"):
        data.pop(key, None)
    data.update(facts or {})
    data["pr_enriched"] = facts is not None


def handle(
    store: Any,
    *,
    body: bytes,
    headers: Mapping[str, str],
    query: Mapping[str, str],
    secrets: Callable[[str], str] = resolve,
    on_check: Callable[[Mapping[str, Any]], Any] | None = None,
    pull: PullLookup | None = None,
) -> tuple[int, dict[str, Any]]:
    """Verify and record one GitHub delivery; return ``(status, json body)``.

    ``on_check`` (the once-per-SHA settler) is called with the event data of an accepted or
    redelivered check completion, so a failed earlier attempt is retried. It runs only after
    the sink stored the event, and a failure answers 503 - but GitHub does not redeliver a
    failed delivery on its own, so the node's settle tick re-arms the SHA from the stored
    completion (:mod:`culture_rules.node.checks_settle`, "Recovery"); a manual redelivery
    still re-runs ``on_check``.

    ``pull`` is the read-only PR lookup that enriches a comment on a pull request with the PR
    facts (d14, :func:`_enrich_comment`); it runs only when the sink would store the delivery,
    and a failure stores the comment with ``pr_enriched: false``."""
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
    if event in _BODY_PATHS:  # d21: what the comment asks of the App, read from its body
        me = (actor.get("params") or {}).get("self_identity")
        data.update(comment_intent(_dig(payload, *_BODY_PATHS[event]), me))
    if _is_pr_comment(event, payload) and _would_store(store, actor, etype, delivery):
        _enrich_comment(data, pull)
    outcome = sink(store, actor, etype, data, delivery, data["author"])
    if (
        on_check is not None
        and etype in SELF_TAG_EXEMPT_TYPES
        and outcome in ("accepted", DUPLICATE)
    ):
        try:
            on_check(data)
        except Exception:  # noqa: BLE001 - arming failed after the sink stored the event
            # The event is already stored, so the node's settle tick recovers the arm from
            # it (GitHub never redelivers a 503 by itself); a manual redelivery is deduped
            # by delivery id and re-runs on_check.
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
    pull: PullLookup | None = None,
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
        # a thread: handling may list check suites (the settler) or read a PR over the network
        status, out = await run_in_threadpool(
            handle,
            store,
            body=b"".join(chunks),
            headers=request.headers,
            query=request.query_params,
            secrets=resolver,
            on_check=on_check,
            pull=pull,
        )
        return JSONResponse(out, status_code=status)

    return api
