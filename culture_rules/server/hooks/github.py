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
sink would store the delivery, so a redelivery never re-reads the PR. Every PR-scoped event
also carries the PR's ``state`` (``open``/``closed``, d21) when it is known.

Comment intent (d21): an ``issue_comment``, ``pull_request_review`` or
``pull_request_review_comment`` also carries ``command`` and ``mention``, read from the body's
**first token** only (:func:`comment_intent`): ``/fix`` when the comment starts with it,
``@<slug>`` when it starts with the App's mention (its ``params.self_identity`` without
``[bot]``); each is omitted when absent. The fixer's comment rules match them against
``vars.fixer_comment_triggers``: start the comment with ``/fix`` or ``@rules-culture-dev``.

The endpoint is public:
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
        data["comment"] = _comment_body(payload)
    elif event == "pull_request_review":
        data["review_state"] = _dig(payload, "review", "state")
        _enrich_pr(data, payload)
    elif event == "pull_request_review_comment":
        _review_comment_author(data, payload)
        _enrich_pr(data, payload)
    elif event == "check_suite":
        cs = _dig(payload, "check_suite")
        if isinstance(cs, Mapping):
            _check_facts(data, cs, app_slug=_dig(cs, "app", "slug"), workflow_name=None)
    elif event == "workflow_run":
        wr = _dig(payload, "workflow_run")
        if isinstance(wr, Mapping):
            _check_facts(data, wr, app_slug=None, workflow_name=wr.get("name"))
    return data


def _comment_body(payload: Mapping[str, Any]) -> str | None:
    """An issue comment's body, capped; None when it has no text body."""
    body = _dig(payload, "comment", "body")
    return body[:_COMMENT_MAX] if isinstance(body, str) else None


def _review_comment_author(data: dict[str, Any], payload: Mapping[str, Any]) -> None:
    """A review comment's own author (when readable) replaces the sender as ``author``."""
    comment = _dig(payload, "comment")
    if not isinstance(comment, Mapping):
        return
    comment_user = comment.get("user")
    if isinstance(comment_user, Mapping):
        login = comment_user.get("login")
        if isinstance(login, str):
            data["author"] = login


def _check_facts(
    data: dict[str, Any], run: Mapping[str, Any], *, app_slug: Any, workflow_name: Any
) -> None:
    """The completion facts of a check suite or workflow run, in the stored key order."""
    data["head_sha"] = run.get("head_sha")
    data["head_branch"] = run.get("head_branch")
    data["pr_numbers"] = _pr_numbers(run.get("pull_requests"))
    data["app_slug"] = app_slug
    data["workflow_name"] = workflow_name
    data["status"] = run.get("status")
    data["conclusion"] = run.get("conclusion")


def _pr_numbers(prs: Any) -> list[Any]:
    """The int ``number`` of each pull request listed, in order (``[]`` for no list)."""
    if not isinstance(prs, list):
        return []
    return [
        pr["number"] for pr in prs if isinstance(pr, Mapping) and isinstance(pr.get("number"), int)
    ]


INTENT_WINDOW = 512
"""How much of a comment, from its first token on, :func:`comment_intent` reads: far more
than any token it accepts, so the character after a token is always the real one."""
_COMMAND_RE = re.compile(r"/([a-z][a-z0-9_-]{0,31})", re.IGNORECASE | re.ASCII)
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$", re.ASCII)
_BOT = "[bot]"
_BODY_PATHS = {
    "issue_comment": ("comment", "body"),
    "pull_request_review": ("review", "body"),
    "pull_request_review_comment": ("comment", "body"),
}


def _ascii_equal(text: str, word: str) -> bool:
    """``text == word`` with ASCII letters compared case-insensitively and nothing else
    folded (Unicode folding would read ``ſ`` as ``s`` or the Kelvin sign as ``k``)."""
    return text.isascii() and text.lower() == word.lower()


def _boundary(rest: str) -> bool:
    """Whether a token ends where ``rest`` begins: at the end of the body, or before a
    character that cannot continue it (not a Unicode letter or digit, ``_`` or ``-``, and
    not a ``.`` followed by one - ``@app.example`` is a host)."""
    if not rest:
        return True
    head = rest[0]
    if head.isalnum() or head in "_-":
        return False
    return not (head == "." and len(rest) > 1 and (rest[1].isalnum() or rest[1] in "_-"))


def comment_intent(body: Any, self_identity: Any) -> dict[str, str]:
    """What a comment asks of the App (d21): only its **first token** counts - the first
    non-whitespace characters of the body. Start the comment with ``/fix`` or with the
    App's mention.

    * ``command`` - the token when it is ``/word`` (ASCII letters, digits, ``_``, ``-``,
      lowercased: ``/fix``) followed by whitespace or the end of the body; ``/fix,
      please`` is no command;
    * ``mention`` - ``@<slug>`` when the token is the App's mention (``self_identity``
      without ``[bot]``; an optional ``[bot]`` is part of the token), compared
      ASCII-case-insensitively and ending at a token boundary (:func:`_boundary`):
      ``@rules-culture-dev,`` counts, ``@rules-culture-devx`` and
      ``@rules-culture-dev[bot]x`` do not.

    Nothing later in the body ever counts, so there is no Markdown to parse. Leading
    whitespace (Unicode included) is stripped from the whole body first, then
    :data:`INTENT_WINDOW` characters are read, so a bound never ends a token. Each fact is
    omitted when absent, so a rule comparing it is false."""
    if not isinstance(body, str):
        return {}
    text = body.lstrip()[:INTENT_WINDOW]
    out: dict[str, str] = {}
    command = _COMMAND_RE.match(text)
    if command and _command_end(text[command.end() :]):
        out["command"] = "/" + command.group(1).lower()
    slug = self_identity.strip() if isinstance(self_identity, str) else ""
    if slug.lower().endswith(_BOT):
        slug = slug[: -len(_BOT)]
    if not _SLUG_RE.match(slug) or not text.startswith("@"):
        return out
    if not _ascii_equal(text[1 : 1 + len(slug)], slug):
        return out
    rest = text[1 + len(slug) :]
    if _ascii_equal(rest[: len(_BOT)], _BOT):
        rest = rest[len(_BOT) :]  # a present [bot] is consumed for good, then the boundary
    if _boundary(rest):
        out["mention"] = "@" + slug.lower()
    return out


def _command_end(rest: str) -> bool:
    """A command ends at whitespace (Unicode included) or the end of the body."""
    return not rest or rest[0].isspace()


def _add_comment_intent(
    data: dict[str, Any], event: str, payload: Mapping[str, Any], actor: Mapping[str, Any]
) -> None:
    """d21: what a comment asks of the App (:func:`comment_intent`), read from its body; a
    delivery of any other event is left alone."""
    if event in _BODY_PATHS:
        me = (actor.get("params") or {}).get("self_identity")
        data.update(comment_intent(_dig(payload, *_BODY_PATHS[event]), me))


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


def _arms_settle(on_check: Any, etype: str, outcome: str) -> bool:
    """Whether a stored (or redelivered) check completion goes on to the settler."""
    return (
        on_check is not None
        and etype in SELF_TAG_EXEMPT_TYPES
        and outcome in ("accepted", DUPLICATE)
    )


def _json_object(body: bytes) -> dict[str, Any] | None:
    """The delivery body as a JSON object, or None (not JSON, or not an object)."""
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


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
    payload = _json_object(body)
    if payload is None:
        record_outcome(store, SURFACE, BAD_REQUEST, actor.get("id"))
        return 400, {"error": "invalid payload"}
    action = payload.get("action")
    etype = _TYPES.get((event, action))
    if etype is None:
        return _IGNORED
    data = _data(event, action, payload)
    _add_comment_intent(data, event, payload, actor)
    if _is_pr_comment(event, payload) and _would_store(store, actor, etype, delivery):
        _enrich_comment(data, pull)
    outcome = sink(store, actor, etype, data, delivery, data["author"])
    if _arms_settle(on_check, etype, outcome):
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
