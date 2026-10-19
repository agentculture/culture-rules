"""Plan the enrolment of gateway models as endpoint actors (``actors enrol-models``).

Pure planning, no I/O: the caller lists each gateway's models and the actors the API holds
(deleted ones included); :func:`plan_model_enrolment` returns what to write. Zero third-party
imports.

Rules the plan keeps:

* Gateways are ordered ``(machine, url, model ids)``; the **first** gateway that lists a model
  wins it, later ones are only reported as ``also_served_by``.
* Only models matching a ``match`` glob (fnmatch) are planned; an empty ``match`` is an error.
* An enrolled actor is ``{"id": <model>, "kind": "agent", "machine", "params": {"base_url",
  "model", "concurrency_pool", "max_concurrency", "api_key"}, "enabled": true,
  "config_source": "db"}``. The pool defaults to the model id; ``pools`` overrides it.
* An update touches only the enrolled fields (``machine`` and ``params.base_url``, ``model``,
  ``concurrency_pool``, ``max_concurrency``, ``api_key``); everything else is preserved. An
  existing ``max_concurrency`` is kept (operator tuning); ``api_key=None`` leaves the key alone.
* Refusals are data (``action == "refused"``, the reason in ``reason``): an invalid actor id
  (the model layer's :func:`~culture_rules.model.validate.validate`), an id held by a
  non-agent actor, by an agent actor without ``params.base_url``, or by a soft-deleted actor.
  The existing document is never touched.
* An existing endpoint actor (agent with ``params.base_url``) on one of the listed gateways
  (its ``base_url`` is the gateway URL) whose model none of them lists is reported as
  ``unseen``; it is never disabled. Actors on gateways the call did not list are not judged.
"""

from __future__ import annotations

import copy
import fnmatch
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from culture_rules.model.actor import Actor
from culture_rules.model.validate import validate

DEFAULT_MAX_CONCURRENCY = 1
#: The gateway key reference ``enrol-models`` sends when none is given (the CLI and API share it).
DEFAULT_API_KEY = "grant:LOBES_GATEWAY_API_KEY"
# An actor id is one URL path segment (/actors/{id}); a model id like "org/name" is not one.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_PARAMS = ("base_url", "model", "concurrency_pool", "max_concurrency", "api_key")
# Fields kept on an update body (server-owned ones such as timestamps are dropped).
_FIELDS = (
    "id",
    "name",
    "description",
    "kind",
    "capabilities",
    "harness",
    "model",
    "machine",
    "config_source",
    "repo",
    "params",
    "enabled",
)

Gateway = tuple[str, str, Sequence[str]]


@dataclass(frozen=True)
class ModelChange:
    """One planned outcome per matched model: create | update | unchanged | refused."""

    action: str
    id: str
    body: dict[str, Any] | None = None
    reason: str = ""


@dataclass
class ModelPlan:
    changes: list[ModelChange] = field(default_factory=list)
    also_served_by: dict[str, list[str]] = field(default_factory=dict)
    unseen: list[str] = field(default_factory=list)  # actor ids; never disabled

    def by_action(self, action: str) -> list[ModelChange]:
        return [c for c in self.changes if c.action == action]


def parse_pools(items: Iterable[str]) -> dict[str, str]:
    """``["model=pool", ...]`` -> ``{model: pool}`` (each side stripped); a malformed item is a
    ``ValueError``."""
    out: dict[str, str] = {}
    for item in items:
        model, sep, pool = (part.strip() for part in str(item).partition("="))
        if not sep or not model or not pool:
            raise ValueError(f"invalid pool {item!r}; expected model=pool")
        out[model] = pool
    return out


def _is_endpoint(doc: Mapping[str, Any]) -> bool:
    params = doc.get("params") or {}
    return doc.get("kind") == "agent" and bool(params.get("base_url"))


def _wanted_params(
    model: str, url: str, pool: str, api_key: str | None, have: Mapping[str, Any] | None
) -> dict[str, Any]:
    params: dict[str, Any] = {"base_url": url, "model": model, "concurrency_pool": pool}
    kept = (have or {}).get("max_concurrency")
    params["max_concurrency"] = kept if kept is not None else DEFAULT_MAX_CONCURRENCY
    if api_key:
        params["api_key"] = api_key
    return params


def _create_body(model: str, machine: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": model,
        "name": model,
        "description": f"Model {model} served on {machine}",
        "kind": "agent",
        "machine": machine,
        "params": params,
        "enabled": True,
        "config_source": "db",
        "schema_version": "1.0",
    }


def _id_errors(body: dict[str, Any]) -> list[str]:
    ident = body.get("id")
    if isinstance(ident, str) and ident and not _SAFE_ID.match(ident):
        return [f"id {ident!r} is not a valid actor id (letters, digits, '.', '_', '-')"]
    actor, errors = _build(body)
    if actor is None:
        return errors
    return [f"{e.path or 'actor'}: {e.message}" for e in validate(actor)]


def _build(body: dict[str, Any]) -> tuple[Actor | None, list[str]]:
    try:
        return Actor.from_dict(body), []
    except Exception as exc:  # noqa: BLE001 - any model rejection is a refusal reason
        return None, [str(exc)]


def _held_refusal(have: Mapping[str, Any]) -> str:
    """Why an existing actor with the model's id cannot be updated ("" when it can)."""
    if have.get("deleted_at") or have.get("deleted"):
        return "held by a soft-deleted actor; restore or purge"
    if have.get("kind") != "agent":
        return f"id held by a {have.get('kind')} actor"
    if not (have.get("params") or {}).get("base_url"):
        return "id held by an agent actor without base_url"
    return ""


def _plan_one(
    model: str,
    machine: str,
    url: str,
    pool: str,
    api_key: str | None,
    have: dict[str, Any] | None,
) -> ModelChange:
    if have is None:
        body = _create_body(model, machine, _wanted_params(model, url, pool, api_key, None))
        problems = _id_errors(body)
        if problems:
            return ModelChange("refused", model, None, f"invalid actor: {problems[0]}")
        return ModelChange("create", model, body, f"served by {machine}")
    refusal = _held_refusal(have)
    if refusal:
        return ModelChange("refused", model, None, refusal)
    old_params = have.get("params") or {}
    wanted = _wanted_params(model, url, pool, api_key, old_params)
    if api_key is None and "api_key" in old_params:
        wanted["api_key"] = old_params["api_key"]
    body = {k: copy.deepcopy(have[k]) for k in _FIELDS if have.get(k) is not None}
    body["machine"] = machine
    body["params"] = {**copy.deepcopy(old_params), **wanted}
    changed = ["machine"] if have.get("machine") != machine else []
    changed += [f"params.{k}" for k in _PARAMS if old_params.get(k) != body["params"].get(k)]
    if not changed:
        return ModelChange("unchanged", model, None, "")
    return ModelChange("update", model, body, "changed " + ", ".join(changed))


def _winners(
    gateways: Sequence[Gateway], plan: ModelPlan
) -> tuple[dict[str, tuple[str, str]], set[str]]:
    """Each served model's first gateway ``(machine, url)``, and every served model id; later
    gateways serving a model are recorded on ``plan.also_served_by``."""
    winner: dict[str, tuple[str, str]] = {}
    served: set[str] = set()
    for machine, url, models in gateways:
        for model in models:
            served.add(model)
            if model not in winner:
                winner[model] = (machine, url)
            elif machine not in plan.also_served_by.setdefault(model, []):
                plan.also_served_by[model].append(machine)
    return winner, served


def _norm_url(url: Any) -> str:
    return url.strip().rstrip("/") if isinstance(url, str) else ""


def _unseen(
    by_id: Mapping[str, dict[str, Any]], gateways: Sequence[Gateway], served: set[str]
) -> list[str]:
    """Live endpoint actors on one of the asked gateways (by ``params.base_url``) whose model
    none of them lists. Actors on gateways this call did not ask are not judged."""
    asked = {_norm_url(url) for _machine, url, _models in gateways}
    out = []
    for ident, have in sorted(by_id.items()):
        params = have.get("params") or {}
        if (
            _is_endpoint(have)
            and not (have.get("deleted_at") or have.get("deleted"))
            and _norm_url(params.get("base_url")) in asked
            and params.get("model") not in served
        ):
            out.append(ident)
    return out


def plan_model_enrolment(
    existing: Sequence[dict[str, Any]],
    gateways: Sequence[Gateway],
    match: Sequence[str],
    pools: Mapping[str, str] | None = None,
    api_key: str | None = None,
) -> ModelPlan:
    """Diff the models ``gateways`` serve (those hitting ``match``) against ``existing``.

    ``gateways`` is ordered ``(machine, url, model ids)``; ``pools`` maps model to pool;
    ``api_key`` is a grant reference such as ``grant:LOBES_GATEWAY_API_KEY``.
    """
    if not match:
        raise ValueError("match must list at least one glob")
    pools = pools or {}
    plan = ModelPlan()
    by_id = {d["id"]: d for d in existing}
    winner, served = _winners(gateways, plan)
    for model, (machine, url) in winner.items():
        if not any(fnmatch.fnmatchcase(model, g) for g in match):
            plan.also_served_by.pop(model, None)
            continue
        pool = pools.get(model) or model
        plan.changes.append(_plan_one(model, machine, url, pool, api_key, by_id.get(model)))
    plan.unseen = _unseen(by_id, gateways, served)
    return plan
