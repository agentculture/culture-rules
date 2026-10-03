"""The FastAPI application factory. Imported lazily (never at ``culture_rules.server`` import).

``create_app(store, ...)`` closes over configuration only (the store handle, admins, hooks);
no request or session state lives in the process, so any number of instances can serve one
store active-active. Every request resolves to a :class:`~culture_rules.auth.principal.Principal`
in a middleware that runs before routing (401 if it cannot, 403 if the route's required role
from :func:`culture_rules.auth.policy.required_role` is not held), so no handler ever runs for
an unauthenticated or unauthorized caller. Every mutating route goes through an audited verb.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from culture_rules.actors import human
from culture_rules.actors.code import InlineScriptDenied
from culture_rules.actors.secrets import SecretError
from culture_rules.auth import guards
from culture_rules.auth.policy import required_role
from culture_rules.auth.principal import AuthError, Forbidden, Principal
from culture_rules.auth.resolve import ACCESS_HEADER, DEV_IDENTITY_HEADER, AuthSettings, Resolver
from culture_rules.auth.tokens import SERVICE_TOKENS, ServiceTokens, TokenError
from culture_rules.engine import replay as replay_engine
from culture_rules.engine.audit import AuditError, AuditLog
from culture_rules.engine.decisions import RULE_DECISIONS, decisions_for
from culture_rules.engine.lifecycle import Lifecycle, LifecycleError, PermissionDenied
from culture_rules.engine.runs import (
    RUN_COLLECTIONS,
    RUNS_COLLECTION,
    Containment,
    Executor,
    RunError,
    drained_machines,
    is_paused,
)
from culture_rules.ops.health import health_status
from culture_rules.server import events, static
from culture_rules.server import status as read_models
from culture_rules.server.repos import RepoTarget, repos_from_env
from culture_rules.server.service import (
    DEFINITION_KINDS,
    Conflict,
    Definitions,
    Invalid,
    NotFound,
    RuleReferenced,
    ServiceError,
)
from culture_rules.store.port import StoragePort

__all__ = ["API_VERSION", "IDENTITY_HEADER", "create_app", "current_identity", "current_principal"]

API_VERSION = "1.0.0"
"""The HTTP contract version (independent of the package version, so a release bump never
churns ``api/openapi.json``); bump it when the contract changes incompatibly."""
IDENTITY_HEADER = DEV_IDENTITY_HEADER
"""Dev-only identity header; honoured only with ``AuthSettings(insecure_dev_identity=True)``."""
_ALL_COLLECTIONS = tuple(
    dict.fromkeys(
        (*DEFINITION_KINDS, "secrets", SERVICE_TOKENS, *RUN_COLLECTIONS, RULE_DECISIONS)
        + events.STREAMABLE
    )
)

AnswerAsk = Callable[[StoragePort, str, Any, str], Any]


# --------------------------------------------------------------------------- schemas


class ErrorItem(BaseModel):
    path: str = ""
    code: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    errors: list[ErrorItem] = Field(default_factory=list)


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class Health(BaseModel):
    """ops.health.health_status: status is ok, degraded or down; details vary by node."""

    model_config = ConfigDict(extra="allow")
    status: str


class ItemList(BaseModel):
    items: list[dict[str, Any]]


class Controls(BaseModel):
    paused: bool
    drained: list[str]


class RunStart(BaseModel):
    rule_id: str
    trigger: dict[str, Any] = Field(default_factory=dict)
    upstream: dict[str, dict[str, Any]] | None = None


class RunCancel(BaseModel):
    reason: str = ""


class ImportRequest(BaseModel):
    files: dict[str, str] | None = Field(
        None, description="relative path (rules/<id>.json, ...) -> text; or give `repo`"
    )
    repo: str | None = Field(
        None, description="read from this configured repository (GET /repos) instead of files"
    )
    directory: str = Field("", description="with `repo`: the definitions directory inside it")
    ref: str | None = Field(None, description="with `repo`: branch or tag to read")
    apply: bool = Field(False, description="false = dry-run plan only")


class ImportChange(BaseModel):
    kind: str
    id: str
    path: str
    action: str


class ImportPlan(BaseModel):
    applied: bool
    changes: list[ImportChange]
    errors: list[ErrorItem] = Field(default_factory=list)


class ExportResult(BaseModel):
    format: str
    files: dict[str, str]


class RepoExportRequest(BaseModel):
    repo: str = Field(description="a configured repository name (GET /repos)")
    format: str = Field("json", description="json | yaml")
    directory: str = Field("", description="the definitions directory inside the repository")
    apply: bool = Field(False, description="false = dry-run plan only; true = write + commit")
    push: bool = Field(False, description="with apply: git push origin HEAD after committing")


class RepoExportResult(BaseModel):
    repo: str
    applied: bool
    committed: bool
    pushed: bool
    commit: str | None = None
    changes: list[ImportChange]


class Repo(BaseModel):
    name: str = Field(description="what clients pass as `repo`")
    url: str = Field(description="the configured location: a local path or a git remote")
    writable: bool = Field(description="a local working tree: exports can commit to it")


class RepoList(BaseModel):
    items: list[Repo]


class Ask(BaseModel):
    """A human ask (``asks`` collection, culture_rules/actors/human.py)."""

    model_config = ConfigDict(extra="allow")
    id: str
    run_id: str | None = None
    step_id: str | None = None
    question: str | None = None
    options: list[Any] | None = None
    deadline: str | None = None
    status: str = Field(description="open | answered | expired")
    asked_at: str | None = None


class AskList(BaseModel):
    items: list[Ask]


class MachineLoad(BaseModel):
    cpu: float | None = Field(description="percent 0-100; null = not reported")
    gpu: float | None
    mem: float | None


class RunningStep(BaseModel):
    step: str
    workflow: str
    run_id: str


class MachineStatus(BaseModel):
    name: str
    online: bool = Field(description="latest heartbeat younger than 30 s")
    last_seen: str | None = Field(description="latest heartbeat, ISO-8601; null = never")
    load: MachineLoad | None = Field(description="null while offline or never reported")
    running: list[RunningStep]
    queue_depth: int = Field(description="pending steps of active runs bound to this machine")


class MachineStatusList(BaseModel):
    items: list[MachineStatus]


class AskAnswer(BaseModel):
    answer: Any


class WhoAmI(BaseModel):
    identity: str
    kind: str = Field(description="sso | service | agent")
    roles: list[str] = Field(description="viewer < editor < admin")


class ReplayRequest(BaseModel):
    rule_id: str | None = Field(None, description="report only this rule (matching sees all)")
    limit: int | None = Field(None, ge=1, le=10000, description="replay at most N events")


class PurgeRequest(BaseModel):
    apply: bool = Field(False, description="false = dry-run: check only, remove nothing")


class PurgeResult(BaseModel):
    collection: str
    id: str
    applied: bool


class TokenIssue(BaseModel):
    name: str = Field(description="the identity the token authenticates as")
    roles: list[str]
    kind: str = Field("service", description="service | agent")


class TokenRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    identity: str
    kind: str
    roles: list[str]
    revoked_at: str | None = None


class IssuedTokenBody(TokenRecord):
    token: str = Field(description="the bearer secret; shown once, stored only as a hash")


class TokenList(BaseModel):
    items: list[TokenRecord]


ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorEnvelope, "description": "Not found"},
    409: {"model": ErrorEnvelope, "description": "Wrong state / already exists"},
    422: {"model": ErrorEnvelope, "description": "Validation failed (errors listed)"},
}


# --------------------------------------------------------------------------- identity

_BEARER = HTTPBearer(auto_error=False, description="Service token: Bearer crt_<id>.<secret>")
_ACCESS = APIKeyHeader(
    name=ACCESS_HEADER,
    auto_error=False,
    description="Cloudflare Access JWT (honoured on the loopback listener only)",
)


def current_principal(request: Request) -> Principal:
    """The principal the auth middleware resolved for this request (never re-parsed here)."""
    return request.state.principal


def current_identity(principal: Annotated[Principal, Depends(current_principal)]) -> str:
    """The audit identity of the caller."""
    return principal.identity


Identity = Annotated[str, Depends(current_identity)]
Caller = Annotated[Principal, Depends(current_principal)]


def _ask_status(code: str) -> int:
    if code == "ask_not_found":
        return 404
    if code in ("invalid_answer", "unsupported_schema_version"):
        return 422
    return 409  # ask_already_answered, ask_expired


# --------------------------------------------------------------------------- app


def _envelope(status: int, code: str, message: str, errors: Any = ()) -> JSONResponse:
    items = [
        {
            "path": str(e.get("path", "")),
            "code": str(e.get("code", code)),
            "message": str(e.get("message", "")),
        }
        for e in errors
    ]
    body = {"error": {"code": code, "message": message, "errors": items}}
    return JSONResponse(status_code=status, content=body)


def _run_status(code: str) -> int:
    if code.endswith("_not_found"):
        return 404
    if code.startswith("invalid") or code.startswith("workflow_") or code.startswith("trigger"):
        return 422 if code.startswith("invalid") else 409
    return 409


def _install_errors(app: FastAPI) -> None:
    status = {Invalid: 422, NotFound: 404, Conflict: 409, RuleReferenced: 409}

    @app.exception_handler(ServiceError)
    async def _service(request: Request, exc: ServiceError) -> JSONResponse:
        return _envelope(status.get(type(exc), 400), exc.code, exc.message, exc.errors)

    @app.exception_handler(PermissionDenied)
    async def _denied(request: Request, exc: PermissionDenied) -> JSONResponse:
        return _envelope(403, "forbidden", str(exc))

    @app.exception_handler(LifecycleError)
    async def _lifecycle(request: Request, exc: LifecycleError) -> JSONResponse:
        return _envelope(409, "conflict", str(exc))

    @app.exception_handler(AuditError)
    async def _audit(request: Request, exc: AuditError) -> JSONResponse:
        return _envelope(400, "bad_identity", str(exc))

    @app.exception_handler(RunError)
    async def _run(request: Request, exc: RunError) -> JSONResponse:
        return _envelope(_run_status(exc.code), exc.code, exc.message, exc.details)

    @app.exception_handler(human.AskError)
    async def _ask(request: Request, exc: human.AskError) -> JSONResponse:
        return _envelope(_ask_status(exc.code), exc.code, exc.message)

    @app.exception_handler(InlineScriptDenied)
    async def _inline(request: Request, exc: InlineScriptDenied) -> JSONResponse:
        return _envelope(403, exc.code, str(exc))

    @app.exception_handler(SecretError)
    async def _secret(request: Request, exc: SecretError) -> JSONResponse:
        return _envelope(422, "secret_literal", str(exc))

    @app.exception_handler(TokenError)
    async def _token(request: Request, exc: TokenError) -> JSONResponse:
        code = {"not_found": 404, "conflict": 409}.get(exc.code, 422)
        return _envelope(code, exc.code, exc.message)

    @app.exception_handler(NotImplementedError)
    async def _todo(request: Request, exc: NotImplementedError) -> JSONResponse:
        return _envelope(501, "not_implemented", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _request(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"path": ".".join(str(p) for p in e["loc"]), "code": e["type"], "message": e["msg"]}
            for e in exc.errors()
        ]
        return _envelope(422, "invalid", "request validation failed", errors)


def _run_summary(doc: dict[str, Any]) -> dict[str, Any]:
    wf = doc.get("workflow") or {}
    return {
        "hosts": read_models.run_hosts(doc),
        "id": doc["id"],
        "status": doc.get("status"),
        "rule_id": (doc.get("rule") or {}).get("id"),
        "workflow_id": wf.get("id"),
        "started_by": doc.get("started_by"),
        "created_at": doc.get("created_at"),
        "finished_at": doc.get("finished_at"),
    }


def _hosts(doc: dict[str, Any]) -> list[str]:
    return read_models.run_hosts(doc)


def create_app(
    store: StoragePort,
    *,
    admins: tuple[str, ...] = (),
    host: str | None = None,
    answer_ask: AnswerAsk | None = None,
    auth: AuthSettings | None = None,
    web_dist: Path | None = None,
    repos: Sequence[RepoTarget] | None = None,
) -> FastAPI:
    """Build the API over ``store``. Holds configuration only, never request state.

    ``auth`` configures the listener this app serves (default: the LAN listener, service
    tokens only, no dev header); ``admins`` are identities elevated to the admin role.
    ``web_dist`` is the built web UI to serve at ``/`` (default: the packaged ``web_dist``;
    nothing is mounted when it does not exist). ``repos`` are the definition repositories
    clients may name (default: ``CULTURE_RULES_REPOS``, see :mod:`culture_rules.server.repos`).
    """
    ensure = getattr(store, "ensure_collections", None)
    if callable(ensure):
        ensure(*_ALL_COLLECTIONS)
    audit = AuditLog(host=host)
    defs = Definitions(store, audit)
    life = Lifecycle(store, audit, admins=admins)
    tokens = ServiceTokens(store, audit)
    resolver = Resolver((auth or AuthSettings()).with_admins(admins), tokens)
    containment = Containment(store, audit)
    node = host or socket.gethostname()
    targets = {r.name: r for r in (repos_from_env() if repos is None else repos)}

    def repo_target(name: str) -> RepoTarget:
        target = targets.get(name)
        if target is None:
            raise NotFound(f"repo {name!r} is not configured", code="repo_not_found")
        return target

    executor = Executor(store, node, {}, audit=audit)
    if answer_ask is None:

        def answer_ask(store_: StoragePort, ask_id: str, answer: Any, identity: str) -> Any:
            return human.answer_ask(store_, executor, ask_id, answer, identity, audit=audit)

    app = FastAPI(
        title="culture-rules API",
        version=API_VERSION,
        description=(
            "HTTP API of the culture-rules editor. Definition bodies follow the JSON Schemas "
            "in schemas/ (rule, workflow, actor, machine). Stateless: any instance serves any "
            "request from the shared store."
        ),
        dependencies=[Security(_BEARER), Security(_ACCESS)],
        responses={
            401: {"model": ErrorEnvelope, "description": "No valid credential"},
            403: {"model": ErrorEnvelope, "description": "Role not held"},
        },
    )
    _install_errors(app)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        """Resolve the principal and check the route's role before routing (any handler)."""
        try:
            principal = resolver.resolve(request.headers)
            need = required_role(request.method, request.url.path)
            if not principal.has_role(need):
                raise Forbidden("forbidden_role", f"{need} role required")
        except AuthError as exc:
            return _envelope(exc.status, exc.code, exc.message)
        request.state.principal = principal
        return await call_next(request)

    @app.get("/whoami", response_model=WhoAmI, tags=["auth"], operation_id="whoami")
    def whoami(principal: Caller) -> WhoAmI:
        return WhoAmI(**principal.to_dict())

    @app.get(
        "/service-tokens",
        response_model=TokenList,
        tags=["auth"],
        operation_id="list_service_tokens",
    )
    def list_tokens():
        return {"items": tokens.list()}

    @app.post(
        "/service-tokens",
        status_code=201,
        response_model=IssuedTokenBody,
        tags=["auth"],
        operation_id="issue_service_token",
        responses={422: ERRORS[422]},
    )
    def issue_token(body: TokenIssue, identity: Identity):
        issued = tokens.issue(identity, name=body.name, roles=body.roles, kind=body.kind)
        return {**issued.record, "token": issued.token}

    @app.delete(
        "/service-tokens/{token_id}",
        response_model=TokenRecord,
        tags=["auth"],
        operation_id="revoke_service_token",
        responses={404: ERRORS[404], 409: ERRORS[409]},
    )
    def revoke_token(token_id: str, identity: Identity):
        return tokens.revoke(token_id, identity)

    @app.get("/health", response_model=Health, tags=["ops"], operation_id="health")
    def health() -> Health:
        return Health(**health_status(store, datetime.now(UTC), node))

    # registered before the definition routes so /machines/{id} cannot shadow it
    @app.get(
        "/machines/status",
        response_model=MachineStatusList,
        tags=["machines"],
        operation_id="machine_statuses",
    )
    def machine_statuses():
        """Per enrolled machine: liveness, load, in-flight steps and queue depth."""
        return {"items": read_models.machine_statuses(store, datetime.now(UTC))}

    for kind in DEFINITION_KINDS:
        _register_kind(app, kind, defs, life, store, audit)

    # ---- runs
    @app.get("/runs", response_model=ItemList, tags=["runs"], operation_id="list_runs")
    def list_runs(
        status: str | None = None,
        rule_id: str | None = None,
        workflow_id: str | None = None,
        host: Annotated[
            str | None, Query(description="only runs with a step dispatched to this host")
        ] = None,
        limit: int = 100,
    ):
        where = {"status": status} if status else None
        docs = store.find(RUNS_COLLECTION, where)
        if rule_id:
            docs = [d for d in docs if (d.get("rule") or {}).get("id") == rule_id]
        if workflow_id:
            docs = [d for d in docs if (d.get("workflow") or {}).get("id") == workflow_id]
        if host:
            docs = [d for d in docs if host in _hosts(d)]
        docs = sorted(docs, key=lambda d: d.get("created_at") or "", reverse=True)[: max(limit, 0)]
        return {"items": [_run_summary(d) for d in docs]}

    @app.get(
        "/rules/{id}/history",
        response_model=ItemList,
        tags=["rules"],
        operation_id="get_rule_history",
        responses={404: ERRORS[404]},
    )
    def rule_history(id: str, limit: Annotated[int, Query(ge=1, le=500)] = 20):
        """A rule's contextual history, newest first: its runs (``kind: run``) and its
        recorded skips (``kind: decision``: superseded_by, blocked_by_predecessor,
        group_lost, with ``by`` naming the responsible rules)."""
        defs.get("rules", id)
        runs = [
            {"kind": "run", "at": d.get("created_at"), **_run_summary(d)}
            for d in store.find(RUNS_COLLECTION)
            if (d.get("rule") or {}).get("id") == id
        ]
        skips = [{"kind": "decision", **d} for d in decisions_for(store, id)]
        merged = sorted(runs + skips, key=lambda item: item.get("at") or "", reverse=True)
        return {"items": merged[:limit]}

    @app.get(
        "/runs/{run_id}",
        tags=["runs"],
        operation_id="get_run",
        responses={404: ERRORS[404]},
        response_model=dict[str, Any],
    )
    def get_run(run_id: str):
        doc = store.get(RUNS_COLLECTION, run_id)
        if doc is None:
            raise NotFound(f"run {run_id!r} does not exist")
        return doc

    @app.post(
        "/runs",
        status_code=201,
        tags=["runs"],
        operation_id="start_run",
        responses=ERRORS,
        response_model=dict[str, Any],
    )
    def start_run(body: RunStart, identity: Identity):
        return executor.start_from_store(
            body.rule_id, trigger=body.trigger, upstream=body.upstream, identity=identity
        )

    @app.post(
        "/runs/{run_id}/cancel",
        tags=["runs"],
        operation_id="cancel_run",
        responses=ERRORS,
        response_model=dict[str, Any],
    )
    def cancel_run(run_id: str, identity: Identity, body: RunCancel | None = None):
        return containment.cancel(run_id, identity, (body or RunCancel()).reason)

    # ---- containment
    def state() -> Controls:
        return Controls(paused=is_paused(store), drained=sorted(drained_machines(store)))

    @app.get("/controls", response_model=Controls, tags=["controls"], operation_id="get_controls")
    def get_controls():
        return state()

    @app.post(
        "/controls/pause",
        response_model=Controls,
        tags=["controls"],
        operation_id="pause_engine",
        responses={409: ERRORS[409]},
    )
    def pause(identity: Identity):
        containment.pause(identity)
        return state()

    @app.post(
        "/controls/resume",
        response_model=Controls,
        tags=["controls"],
        operation_id="resume_engine",
        responses={409: ERRORS[409]},
    )
    def resume(identity: Identity):
        containment.resume(identity)
        return state()

    @app.post(
        "/machines/{name}/drain",
        response_model=Controls,
        tags=["controls"],
        operation_id="drain_machine",
        responses={409: ERRORS[409]},
    )
    def drain(name: str, identity: Identity):
        containment.drain(name, identity)
        return state()

    @app.post(
        "/machines/{name}/undrain",
        response_model=Controls,
        tags=["controls"],
        operation_id="undrain_machine",
        responses={409: ERRORS[409]},
    )
    def undrain(name: str, identity: Identity):
        containment.undrain(name, identity)
        return state()

    # ---- import / export
    @app.get(
        "/export",
        response_model=ExportResult,
        tags=["exchange"],
        operation_id="export_definitions",
        responses={422: ERRORS[422]},
    )
    def export(format: str = "json"):
        return {"format": format, "files": defs.export_files(format)}

    @app.post(
        "/export",
        response_model=RepoExportResult,
        tags=["exchange"],
        operation_id="export_to_repo",
        responses={404: ERRORS[404], 422: ERRORS[422]},
    )
    def export_to_repo(body: RepoExportRequest, identity: Identity):
        """Write live definitions into a configured repository; dry-run unless `apply`."""
        return defs.export_to_repo(
            repo_target(body.repo),
            identity,
            fmt=body.format,
            directory=body.directory,
            apply=body.apply,
            push=body.push,
        )

    @app.get("/repos", response_model=RepoList, tags=["exchange"], operation_id="list_repos")
    def list_repos():
        """The definition repositories this server is configured with (CULTURE_RULES_REPOS)."""
        return {"items": [t.to_dict() for t in targets.values()]}

    @app.post(
        "/replay",
        tags=["rules"],
        operation_id="replay_rules",
        response_model=dict[str, Any],
        responses={422: ERRORS[422]},
    )
    def replay(body: ReplayRequest):
        """Replay recorded events through matching; reports would-fire runs, executes nothing."""
        rules, workflows = defs.rule_set()
        try:
            report = replay_engine.replay(
                store, rules, workflows=workflows, rule_id=body.rule_id, limit=body.limit
            )
        except replay_engine.ReplayError as exc:
            return _envelope(422, "replay_invalid", str(exc))
        return report.to_dict()

    @app.post(
        "/import",
        response_model=ImportPlan,
        tags=["exchange"],
        operation_id="import_definitions",
        responses={422: ERRORS[422]},
    )
    def import_definitions(body: ImportRequest, principal: Caller):
        if (body.files is None) == (body.repo is None):
            raise Invalid(
                "give exactly one of `files` or `repo`",
                [{"path": "repo", "code": "one_source", "message": "files xor repo"}],
            )
        if body.repo is not None:
            files = defs.repo_files(repo_target(body.repo), directory=body.directory, ref=body.ref)
        else:
            files = body.files or {}
        guards.check_import(principal, files)
        return defs.import_files(files, principal.identity, apply=body.apply)

    # ---- asks
    @app.get("/asks", response_model=AskList, tags=["asks"], operation_id="list_asks")
    def list_asks(
        run_id: str | None = None,
        status_: Annotated[
            str | None, Query(alias="status", description="open | answered | expired")
        ] = None,
    ):
        if status_ is not None and status_ not in read_models.ASK_STATUSES:
            raise Invalid(
                f"unknown ask status {status_!r}",
                [{"path": "status", "code": "unknown", "message": status_}],
            )
        return {"items": read_models.list_asks(store, run_id=run_id, status=status_)}

    @app.post(
        "/asks/{ask_id}/answer",
        tags=["asks"],
        operation_id="answer_ask",
        responses={
            404: {"model": ErrorEnvelope, "description": "No such ask"},
            409: {"model": ErrorEnvelope, "description": "Already answered or expired"},
            422: {"model": ErrorEnvelope, "description": "Answer not one of the options"},
        },
        response_model=dict[str, Any],
    )
    def answer(ask_id: str, body: AskAnswer, identity: Identity):
        result = answer_ask(store, ask_id, body.answer, identity)
        return result if isinstance(result, dict) else {"ok": True}

    # ---- live updates
    @app.get(
        "/events/stream",
        tags=["events"],
        operation_id="stream_events",
        response_model=None,
        responses={
            200: {
                "description": "Server-sent events: one `change` event per committed write",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            },
            422: ERRORS[422],
        },
    )
    def stream(
        collections: Annotated[
            str, Query(description="Comma-separated collections; default: all streamable")
        ] = "",
        after: Annotated[
            str | None, Query(description="JSON cursor map (an earlier event id) to resume from")
        ] = None,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
        max_events: Annotated[int | None, Query(ge=1)] = None,
        max_seconds: Annotated[float, Query(gt=0, le=3600)] = 300.0,
    ):
        names = [c.strip() for c in collections.split(",") if c.strip()] or list(events.STREAMABLE)
        unknown = [c for c in names if c not in events.STREAMABLE]
        if unknown:
            raise Invalid(
                f"unknown collections: {', '.join(unknown)}",
                [{"path": "collections", "code": "unknown", "message": c} for c in unknown],
            )
        try:
            cursors = events.parse_cursors(after or last_event_id)
        except ValueError as exc:
            raise Invalid(str(exc), [{"path": "after", "code": "cursor", "message": str(exc)}])
        return StreamingResponse(
            events.stream_changes(
                store, names, cursors, max_events=max_events, max_seconds=max_seconds
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    static.install(app, web_dist)
    return app


def _register_kind(
    app: FastAPI,
    kind: str,
    defs: Definitions,
    life: Lifecycle,
    store: StoragePort,
    audit: AuditLog,
) -> None:
    """The same six routes for every definition kind (rules, workflows, actors, machines)."""
    tag = [kind]
    one = kind[:-1]
    path = f"/{kind}/{{id}}"

    @app.get(f"/{kind}", response_model=ItemList, tags=tag, operation_id=f"list_{kind}")
    def list_items(include_deleted: bool = False):
        return {"items": defs.list(kind, include_deleted=include_deleted)}

    @app.post(
        f"/{kind}",
        status_code=201,
        tags=tag,
        operation_id=f"create_{one}",
        responses={409: ERRORS[409], 422: ERRORS[422]},
        response_model=dict[str, Any],
    )
    def create(body: dict[str, Any], principal: Caller):
        guards.check_definition(principal, kind, body)
        return defs.create(kind, body, principal.identity)

    @app.get(
        path,
        tags=tag,
        operation_id=f"get_{one}",
        responses={404: ERRORS[404]},
        response_model=dict[str, Any],
    )
    def get_item(id: str):
        return defs.get(kind, id)

    @app.put(
        path,
        tags=tag,
        operation_id=f"update_{one}",
        responses=ERRORS,
        response_model=dict[str, Any],
    )
    def update(id: str, body: dict[str, Any], principal: Caller):
        guards.check_definition(principal, kind, body)
        return defs.update(kind, id, body, principal.identity)

    for verb, flag in (("enable", True), ("disable", False)):

        def toggle(id: str, identity: Identity, _flag: bool = flag):
            return defs.set_enabled(kind, id, _flag, identity)

        app.post(
            f"{path}/{verb}",
            tags=tag,
            operation_id=f"{verb}_{one}",
            responses=ERRORS,
            response_model=dict[str, Any],
        )(toggle)

    @app.delete(
        path,
        tags=tag,
        operation_id=f"delete_{one}",
        responses=ERRORS,
        response_model=dict[str, Any],
    )
    def delete(id: str, identity: Identity):
        defs.get(kind, id)
        defs.guard_unreferenced(kind, id)
        return life.soft_delete(kind, id, identity)

    @app.post(
        f"{path}/restore",
        tags=tag,
        operation_id=f"restore_{one}",
        responses=ERRORS,
        response_model=dict[str, Any],
    )
    def restore(id: str, identity: Identity):
        defs.get(kind, id)
        return life.restore(kind, id, identity)

    @app.post(
        f"{path}/purge",
        tags=tag,
        operation_id=f"purge_{one}",
        response_model=PurgeResult,
        responses=ERRORS,
    )
    def purge(id: str, identity: Identity, body: PurgeRequest | None = None):
        defs.get(kind, id)
        defs.guard_unreferenced(kind, id)
        # the middleware already required the admin role for this route
        admin_life = Lifecycle(store, audit, admins=(identity,))
        result = admin_life.purge(kind, id, identity, apply=(body or PurgeRequest()).apply)
        return {"collection": result.collection, "id": result.id, "applied": result.applied}
