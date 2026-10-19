"""Model actors over HTTP: enrol a gateway's models, list an actor's uses, repoint a workflow.

Routing, auth context and I/O only; the logic lives in the pure modules:
:mod:`culture_rules.actors.model_enrol` (the enrolment plan),
:mod:`culture_rules.actors.model_rule` (one enabled endpoint actor per model) and
:mod:`culture_rules.model.references` (references and the repoint).

- ``POST /actors/enrol-models`` (admin, the route matrix's default for an unmatched POST)
  names gateways by machine. Each must be a machine of the shared variable
  ``model_gateways`` (machine -> URL: a list of ``"machine=URL"`` strings, since a variable
  holds a scalar or a list of scalars; an object is read too), the allowlist an admin
  writes, so the request can never point the server at a free URL. Only after every
  gateway passed the allowlist is the ``api_key`` grant resolved; the bearer goes only to
  those URLs' ``/v1/models``. The stored actors keep the grant reference, never the value,
  and no answer carries it.
- ``GET /actors/{id}/references`` lists the live workflows and rules naming the actor.
- ``POST /workflows/{id}/repoint-actor`` rewrites the workflow's action actor and saves it
  through the same revisioned update ``PUT /workflows/{id}`` uses.

Writes go through :class:`~culture_rules.server.service.Definitions` (one audit entry per
actor created or updated and per workflow repointed, naming the caller). Both write routes
are dry runs unless ``dry_run`` is false.

No ``from __future__ import annotations`` here: the route handlers are defined inside
:func:`register_model_actors`, and FastAPI must see their dependency annotations evaluated.
"""

import json
import urllib.error
import urllib.request  # nosec B404 - allow-listed http(s) URLs only
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from culture_rules.actors import model_enrol, secrets, trusted
from culture_rules.actors.model_rule import model_holder
from culture_rules.auth import guards
from culture_rules.model.references import NoMatchingStep, find_references, repoint_workflow
from culture_rules.model.workflow import Workflow
from culture_rules.server.service import (
    Conflict,
    Definitions,
    Invalid,
    NotFound,
    ServiceError,
    _check_revision,
)
from culture_rules.store import revision as rev
from culture_rules.store.port import StoragePort

__all__ = [
    "DEFAULT_API_KEY",
    "MODEL_GATEWAYS",
    "ModelsTransport",
    "register_model_actors",
    "urllib_models_transport",
]

MODEL_GATEWAYS = "model_gateways"
"""The shared variable that allow-lists the gateways, machine -> base URL: a list of
``"machine=URL"`` strings (or an object, should variables ever hold one)."""
DEFAULT_API_KEY = "grant:LOBES_GATEWAY_API_KEY"
_TIMEOUT_S = 10.0
_MAX_BODY = 1024 * 1024

ModelsTransport = Callable[[str, Mapping[str, str], float], tuple[int, bytes]]
"""``(url, headers, timeout) -> (status, body)``: one GET; raises ``OSError`` when the
gateway cannot be reached."""


# --------------------------------------------------------------------------- the transport


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None  # never follow a redirect with the bearer to another host


def urllib_models_transport(
    url: str, headers: Mapping[str, str], timeout: float
) -> tuple[int, bytes]:
    """GET ``url`` (http/https, checked by the caller); no proxies, no redirects."""
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        _NoRedirect(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        response = opener.open(request, timeout=timeout)  # nosec B310 - http(s) only
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        status = int(getattr(response, "status", None) or response.code)
        return status, response.read(_MAX_BODY + 1)


def _models_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    return base + "/models" if base.endswith("/v1") else base + "/v1/models"


# --------------------------------------------------------------------------- schemas


class EnrolModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gateways: list[str] = Field(
        min_length=1,
        description="machines, each a key of the model_gateways variable; first listed wins",
    )
    match: list[str] = Field(min_length=1, description="fnmatch globs; only hits are enrolled")
    pools: list[str] = Field(
        default_factory=list, description="model=pool overrides (default pool: the model id)"
    )
    api_key: str = Field(
        DEFAULT_API_KEY, description="grant:<NAME> reference; stored as the reference only"
    )
    dry_run: bool = Field(True, description="true = plan only, write nothing")


class GatewayModels(BaseModel):
    machine: str
    url: str
    models: list[str]


class ModelChangeOut(BaseModel):
    action: str = Field(description="create | update | unchanged | refused")
    id: str
    reason: str = ""
    body: dict[str, Any] | None = Field(
        default=None, description="create/update: the actor document to write"
    )


class AppliedActor(BaseModel):
    id: str
    action: str = Field(description="create | update")


class FailedActor(BaseModel):
    id: str
    action: str
    code: str
    message: str


class EnrolModelsResult(BaseModel):
    dry_run: bool
    gateways: list[GatewayModels]
    changes: list[ModelChangeOut]
    also_served_by: dict[str, list[str]]
    unseen: list[str] = Field(description="endpoint actors whose model no gateway lists")
    applied: list[AppliedActor] = Field(default_factory=list)
    failed: list[FailedActor] = Field(default_factory=list)


class ActorReference(BaseModel):
    kind: str = Field(description="workflow | rule")
    id: str
    path: str = Field(description="step path (summarise.body[summary]) or the rule field")
    via: str = Field(description="action | placement")
    enabled: bool


class ActorReferences(BaseModel):
    actor_id: str
    items: list[ActorReference]


class RepointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from", min_length=1, description="the actor steps name today")
    to: str = Field(min_length=1, description="an existing, enabled actor")
    expected_revision: int | None = Field(
        default=None, description="the workflow revision read; missing or stale is 409"
    )
    force: bool = Field(False, description="repoint a trusted workflow anyway (drops trust)")
    dry_run: bool = Field(True, description="true = plan only, write nothing")


class RepointResult(BaseModel):
    workflow_id: str
    dry_run: bool
    from_: str = Field(alias="from")
    to: str
    changed: list[str] = Field(description="step paths whose action actor changes")
    warnings: list[str]
    revision: int = Field(description="dry run: the current revision; applied: the new one")
    digest: str | None = Field(description="the repointed definition's v1 trust digest")
    digest_v2: str | None = Field(description="the repointed definition's v2 trust digest")
    trusted_digest: str | None = Field(
        default=None, description="the pinned digest a forced repoint drops, if any"
    )


# --------------------------------------------------------------------------- helpers


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _gateway_pairs(value: Any) -> list[tuple[Any, Any]] | None:
    """``model_gateways`` as (machine, URL) pairs: an object, or (what the variable model can
    hold today: a scalar or a list of scalars) a list of ``"machine=URL"`` strings."""
    if isinstance(value, dict):
        return list(value.items())
    if isinstance(value, list) and all(isinstance(i, str) and "=" in i for i in value):
        return [tuple(i.split("=", 1)) for i in value]
    return None


def _gateway_allowlist(store: StoragePort) -> dict[str, str]:
    """The ``model_gateways`` variable as machine -> URL ({} when unset). A value that is not
    machine -> http(s) URL, or names a machine twice, is refused: it is the allowlist, so it
    fails closed."""
    doc = store.get_variable(MODEL_GATEWAYS)
    if doc is None:
        return {}
    pairs = _gateway_pairs(doc.get("value"))
    names = [k for k, _ in pairs or ()]
    if (
        pairs is None
        or len(set(names)) != len(names)
        or not all(isinstance(k, str) and k.strip() and _is_http_url(v) for k, v in pairs)
    ):
        message = (
            f"the {MODEL_GATEWAYS} variable must map each machine to an http(s) URL "
            '(a list of "machine=URL" strings, or an object)'
        )
        raise Conflict(
            message,
            [{"path": MODEL_GATEWAYS, "code": "invalid_model_gateways", "message": message}],
            code="invalid_model_gateways",
        )
    return {k.strip(): v for k, v in pairs}


def _chosen_gateways(store: StoragePort, names: list[str]) -> list[tuple[str, str]]:
    allow = _gateway_allowlist(store)
    ordered = list(dict.fromkeys(names))
    unknown = [n for n in ordered if n not in allow]
    if unknown:
        known = ", ".join(sorted(allow)) or f"none: set the {MODEL_GATEWAYS} variable"
        message = f"unknown gateway {', '.join(unknown)}; known gateways: {known}"
        raise Invalid(
            message,
            [{"path": "gateways", "code": "unknown_gateway", "message": n} for n in unknown],
            code="unknown_gateway",
        )
    return [(n, allow[n]) for n in ordered]


class _GatewayUnavailable(Exception):
    """A gateway did not list its models (HTTP 502 ``gateway_unavailable``); the text names
    the gateway only, never the key or the body."""


def _list_models(transport: ModelsTransport, machine: str, url: str, key: str) -> list[str]:
    """The model ids ``url``'s ``/v1/models`` lists. Errors name the gateway, never the key
    or the body."""
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        status, raw = transport(_models_url(url), headers, _TIMEOUT_S)
    except Exception:  # noqa: BLE001 - unreachable, whatever the reason
        raise _GatewayUnavailable(f"gateway {machine} is unreachable") from None
    if status != 200:
        raise _GatewayUnavailable(f"gateway {machine} answered {status}")
    try:
        data = json.loads(raw[:_MAX_BODY].decode("utf-8")).get("data")
        models = [m["id"] for m in data if isinstance(m.get("id"), str)]
    except (ValueError, AttributeError, TypeError, KeyError):
        raise _GatewayUnavailable(f"gateway {machine} answered no model list") from None
    return models


def _hold_models(existing: list[dict[str, Any]], plan: model_enrol.ModelPlan) -> None:
    """Spec c26: a create/update whose model another enabled endpoint actor already holds
    (counting this plan's earlier writes) becomes a refusal naming the holder."""
    effective = {d["id"]: d for d in existing}
    for i, change in enumerate(plan.changes):
        if change.action not in ("create", "update") or change.body is None:
            continue
        if change.body.get("enabled") is not False:
            holder = model_holder(effective.values(), change.body)
            if holder is not None:
                reason = f"model_taken: {holder} (disable it first)"
                plan.changes[i] = model_enrol.ModelChange("refused", change.id, None, reason)
                continue
        effective[change.id] = change.body


def _trusted_digest(doc: Mapping[str, Any]) -> str | None:
    """The pinned v1 or v2 digest ``doc`` hashes to, or None (read at call time)."""
    v1 = trusted.workflow_digest(doc)
    if v1 is not None and v1 in frozenset().union(*trusted.TRUSTED_WORKFLOWS.values()):
        return v1
    v2 = trusted.workflow_digest_v2(doc)
    if v2 is not None and v2 in frozenset().union(*trusted.TRUSTED_WORKFLOWS_V2.values()):
        return v2
    return None


def _check_target(store: StoragePort, to: str) -> None:
    doc = store.get("actors", to)
    if doc is None:
        raise Invalid(f"actor {to!r} does not exist", code="target_not_found")
    if doc.get("deleted_at"):
        raise Conflict(f"actor {to!r} is deleted; restore it first", code="target_deleted")
    if doc.get("enabled") is False:
        raise Conflict(f"actor {to!r} is disabled; enable it first", code="target_disabled")


def _definition(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The workflow definition of a stored document (server-owned fields dropped)."""
    try:
        return Workflow.from_dict(dict(doc), strict=False).to_dict()
    except Exception as exc:  # noqa: BLE001 - an unparseable stored workflow
        raise Invalid(f"workflow does not parse: {exc}", code="invalid_workflow") from None


# --------------------------------------------------------------------------- routes


def register_model_actors(
    app: FastAPI,
    store: StoragePort,
    defs: Definitions,
    resolve_secret: Callable[[str], str] | None,
    transport: ModelsTransport | None,
) -> None:
    """Register the three model-actor routes. ``resolve_secret`` and ``transport`` replace
    the grant resolver and the ``/v1/models`` HTTP transport (tests)."""
    from culture_rules.server.app import ERRORS, Caller, ErrorEnvelope, _envelope  # noqa: PLC0415

    resolve = resolve_secret or secrets.resolve
    fetch = transport or urllib_models_transport

    @app.post(
        "/actors/enrol-models",
        tags=["actors"],
        operation_id="enrol_models",
        response_model=EnrolModelsResult,
        responses={
            409: ERRORS[409],
            422: ERRORS[422],
            502: {"model": ErrorEnvelope, "description": "A gateway did not list its models"},
        },
    )
    def enrol_models(body: EnrolModelsRequest, principal: Caller) -> Any:
        """Plan (dry run) or apply one endpoint actor per matching model the named gateways
        serve. The gateways must be keys of ``model_gateways``; the key grant is resolved
        only after that, and is sent only to those gateways."""
        try:
            pools = model_enrol.parse_pools(body.pools)
        except ValueError as exc:
            raise Invalid(str(exc), code="invalid_pool") from None
        if not secrets.is_secret_ref(body.api_key):
            raise Invalid("api_key must be a grant:<NAME> reference", code="invalid_api_key")
        chosen = _chosen_gateways(store, body.gateways)
        try:
            key = resolve(body.api_key)
        except Exception:  # noqa: BLE001 - never echo why: the text may hold a value
            raise Conflict(
                "the api_key grant cannot be read here", code="secret_unavailable"
            ) from None
        try:
            listed = [(m, url, _list_models(fetch, m, url, key)) for m, url in chosen]
        except _GatewayUnavailable as exc:
            return _envelope(502, "gateway_unavailable", str(exc))
        existing = defs.list("actors", include_deleted=True)
        plan = model_enrol.plan_model_enrolment(existing, listed, body.match, pools, body.api_key)
        _hold_models(existing, plan)
        applied: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        if not body.dry_run:
            for change in plan.changes:
                if change.action not in ("create", "update") or change.body is None:
                    continue
                guards.check_definition(principal, "actors", change.body)
                check = guards.save_check(principal)
                try:
                    if change.action == "create":
                        defs.create("actors", change.body, principal.identity, check=check)
                    else:
                        defs.update(
                            "actors", change.id, change.body, principal.identity, check=check
                        )
                except ServiceError as exc:
                    failed.append(
                        {
                            "id": change.id,
                            "action": change.action,
                            "code": exc.code,
                            "message": exc.message,
                        }
                    )
                    continue
                applied.append({"id": change.id, "action": change.action})
        return {
            "dry_run": body.dry_run,
            "gateways": [{"machine": m, "url": u, "models": list(ids)} for m, u, ids in listed],
            "changes": [
                {"action": c.action, "id": c.id, "reason": c.reason, "body": c.body}
                for c in plan.changes
            ],
            "also_served_by": plan.also_served_by,
            "unseen": plan.unseen,
            "applied": applied,
            "failed": failed,
        }

    @app.get(
        "/actors/{id}/references",
        tags=["actors"],
        operation_id="actor_references",
        response_model=ActorReferences,
        responses={404: ERRORS[404]},
    )
    def actor_references(id: str) -> dict[str, Any]:
        """The live workflow steps (loop bodies included) and rules naming the actor, by
        action ``params.actor`` or by ``placement.actor``."""
        if store.get("actors", id) is None:
            raise NotFound(f"actors/{id} does not exist")
        items = find_references(id, defs.list("workflows"), defs.list("rules"))
        return {"actor_id": id, "items": items}

    @app.post(
        "/workflows/{id}/repoint-actor",
        tags=["workflows"],
        operation_id="repoint_workflow_actor",
        response_model=RepointResult,
        response_model_by_alias=True,
        responses=ERRORS,
    )
    def repoint_actor(id: str, body: RepointRequest, principal: Caller) -> dict[str, Any]:
        """Rewrite every step action's ``params.actor`` == ``from`` to ``to`` (loop bodies
        included; description, notes and placement untouched, reported as warnings). A
        missing or stale ``expected_revision`` is 409 ``revision_conflict``; a trusted
        workflow is 409 ``workflow_trusted`` unless ``force``."""
        doc = store.get("workflows", id)
        if doc is None:
            raise NotFound(f"workflows/{id} does not exist")
        if doc.get("deleted_at"):
            raise Conflict(f"workflows/{id} is deleted; restore it first")
        _check_revision("workflows", id, doc, body.expected_revision)
        if body.from_ == body.to:
            raise Invalid("from and to name the same actor", code="same_actor")
        _check_target(store, body.to)
        try:
            new_def, changed, warnings = repoint_workflow(_definition(doc), body.from_, body.to)
        except NoMatchingStep as exc:
            raise Invalid(str(exc), code="no_matching_step") from None
        pinned = _trusted_digest(doc)
        if pinned is not None:
            message = (
                f"workflows/{id} is trusted (digest {pinned}); a repoint changes its digest "
                "and drops that trust: pass force to repoint anyway, then pin the new digest"
            )
            if not body.force:
                raise Conflict(
                    message,
                    [{"path": "digest", "code": "workflow_trusted", "message": pinned}],
                    code="workflow_trusted",
                )
            warnings = [*warnings, f"repoint drops trust: {pinned} no longer matches"]
        revision = rev.current_revision(doc)
        if not body.dry_run:
            guards.check_definition(principal, "workflows", new_def)
            stored = defs.update(
                "workflows",
                id,
                {**new_def, "expected_revision": body.expected_revision},
                principal.identity,
                check=guards.save_check(principal),
            )
            revision = stored["revision"]
        return {
            "workflow_id": id,
            "dry_run": body.dry_run,
            "from": body.from_,
            "to": body.to,
            "changed": changed,
            "warnings": warnings,
            "revision": revision,
            "digest": trusted.workflow_digest(new_def),
            "digest_v2": trusted.workflow_digest_v2(new_def),
            "trusted_digest": pinned,
        }
