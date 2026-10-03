"""The FastAPI application factory. Imported lazily (never at ``culture_rules.server`` import).

``create_app(store, ...)`` closes over configuration only (the store handle, admins, hooks);
no request or session state lives in the process, so any number of instances can serve one
store active-active. Authentication is not implemented here: :func:`current_identity` is the
single seam an auth layer replaces. Every mutating route goes through an audited verb.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from culture_rules.actors import human
from culture_rules.engine.audit import AuditError, AuditLog
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
from culture_rules.server import events
from culture_rules.server.service import (
    DEFINITION_KINDS,
    Conflict,
    Definitions,
    Invalid,
    NotFound,
    ServiceError,
)
from culture_rules.store.port import StoragePort

__all__ = ["API_VERSION", "IDENTITY_HEADER", "create_app", "current_identity"]

API_VERSION = "1.0.0"
"""The HTTP contract version (independent of the package version, so a release bump never
churns ``api/openapi.json``); bump it when the contract changes incompatibly."""
IDENTITY_HEADER = "X-Culture-Identity"
ANONYMOUS = "anonymous"
_ALL_COLLECTIONS = (*DEFINITION_KINDS, "secrets", *RUN_COLLECTIONS)

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
    files: dict[str, str] = Field(description="relative path (rules/<id>.json, ...) -> text")
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


class AskAnswer(BaseModel):
    answer: Any


ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorEnvelope, "description": "Not found"},
    409: {"model": ErrorEnvelope, "description": "Wrong state / already exists"},
    422: {"model": ErrorEnvelope, "description": "Validation failed (errors listed)"},
}


# --------------------------------------------------------------------------- identity


def current_identity(
    x_culture_identity: Annotated[
        str | None, Header(description="Principal set by the auth layer; default 'anonymous'")
    ] = None,
) -> str:
    """Who is calling. The one seam the auth layer replaces; reads a header, never verifies."""
    return (x_culture_identity or "").strip() or ANONYMOUS


Identity = Annotated[str, Depends(current_identity)]


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
    status = {Invalid: 422, NotFound: 404, Conflict: 409}

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
        "id": doc["id"],
        "status": doc.get("status"),
        "rule_id": (doc.get("rule") or {}).get("id"),
        "workflow_id": wf.get("id"),
        "started_by": doc.get("started_by"),
        "created_at": doc.get("created_at"),
        "finished_at": doc.get("finished_at"),
    }


def create_app(
    store: StoragePort,
    *,
    admins: tuple[str, ...] = (),
    host: str | None = None,
    answer_ask: AnswerAsk | None = None,
) -> FastAPI:
    """Build the API over ``store``. Holds configuration only, never request state."""
    ensure = getattr(store, "ensure_collections", None)
    if callable(ensure):
        ensure(*_ALL_COLLECTIONS)
    audit = AuditLog(host=host)
    defs = Definitions(store, audit)
    life = Lifecycle(store, audit, admins=admins)
    containment = Containment(store, audit)
    node = host or socket.gethostname()
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
    )
    _install_errors(app)

    @app.get("/health", response_model=Health, tags=["ops"], operation_id="health")
    def health() -> Health:
        return Health(**health_status(store, datetime.now(UTC), node))

    for kind in DEFINITION_KINDS:
        _register_kind(app, kind, defs, life)

    # ---- runs
    @app.get("/runs", response_model=ItemList, tags=["runs"], operation_id="list_runs")
    def list_runs(status: str | None = None, rule_id: str | None = None, limit: int = 100):
        where = {"status": status} if status else None
        docs = store.find(RUNS_COLLECTION, where)
        if rule_id:
            docs = [d for d in docs if (d.get("rule") or {}).get("id") == rule_id]
        docs = sorted(docs, key=lambda d: d.get("created_at") or "", reverse=True)[: max(limit, 0)]
        return {"items": [_run_summary(d) for d in docs]}

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
        "/import",
        response_model=ImportPlan,
        tags=["exchange"],
        operation_id="import_definitions",
        responses={422: ERRORS[422]},
    )
    def import_definitions(body: ImportRequest, identity: Identity):
        return defs.import_files(body.files, identity, apply=body.apply)

    # ---- asks
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

    return app


def _register_kind(app: FastAPI, kind: str, defs: Definitions, life: Lifecycle) -> None:
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
    def create(body: dict[str, Any], identity: Identity):
        return defs.create(kind, body, identity)

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
    def update(id: str, body: dict[str, Any], identity: Identity):
        return defs.update(kind, id, body, identity)

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
