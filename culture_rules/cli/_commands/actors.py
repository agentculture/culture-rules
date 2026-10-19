"""``culture-rules actors`` — actors over the HTTP API."""

from __future__ import annotations

import argparse
from typing import Any

from culture_rules.cli import _api
from culture_rules.cli._build import register_noun
from culture_rules.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from culture_rules.cli._nounlib import (
    ID,
    MODEL_ACTORS_SINCE,
    definition_verbs,
    missing_route,
    new_route,
    seg,
    write,
)
from culture_rules.cli.registry import Context, Param, Verb
from culture_rules.client.http import ApiError

NOUN = "actors"


def _read_desired(ea: Any, server_yaml: str | None, machine: str | None) -> Any:
    """The actors this machine's server.yaml asks for, with errors mapped to ``CliError``."""
    try:
        return ea.desired_actors(
            ea.read_server_yaml(server_yaml or ea.DEFAULT_SERVER_YAML), machine=machine
        )
    except ea.EnrolError as exc:
        raise CliError(
            EXIT_ENV_ERROR if exc.missing_extra else EXIT_USER_ERROR,
            str(exc),
            (
                "pass --server-yaml PATH to a culture server.yaml (default ~/.culture/server.yaml)"
                if not exc.missing_extra
                else "install the extra: pip install 'culture-rules[yaml]'"
            ),
        ) from exc


def _apply_change(ctx: Context, ch: Any) -> list[Any]:
    """Send one planned change through the API; return the responses in order."""
    if ch.action == "create":
        return [_api.call(lambda: ctx.client.request("POST", "/actors", body=ch.body))]
    path = f"/actors/{seg(ch.id)}"
    results: list[Any] = []
    if ch.body is not None:  # update, or the disable stamp
        results.append(_api.call(lambda: ctx.client.request("PUT", path, body=ch.body)))
    if ch.action == "disable":
        results.append(_api.call(lambda: ctx.client.request("POST", f"{path}/disable")))
    return results


def _enrol_lines(ctx: Context, machine: str, changes: list[dict], warnings: list[str]) -> list:
    lines = [
        f"{'applied' if ctx.apply else 'dry-run'}: enrol-agents for machine {machine}: "
        f"{len(changes)} change(s)",
        *(f"  {c['action']:<7} {c['id']}  ({c['reason']})" for c in changes),
        *(f"  warning: {w}" for w in warnings),
    ]
    if not ctx.apply and changes:
        lines.append("re-run with --apply to commit")
    return lines


def _enrol_agents(
    ctx: Context, server_yaml: str | None = None, machine: str | None = None
) -> dict[str, Any]:
    """Enrol this machine's mesh agents (``~/.culture/server.yaml``) as agent actors.

    Runs locally (it reads local files) and writes through the API. The first call is a GET of
    the existing actors, so a dry-run sends nothing else.
    """
    from culture_rules.actors import enrol_agents as ea  # noqa: PLC0415

    existing = _api.call(
        lambda: ctx.client.request("GET", "/actors", query={"include_deleted": "true"})
    ).get("items", [])
    desired = _read_desired(ea, server_yaml, machine)
    plan = ea.plan_enrolment(
        existing, desired.actors, machine=desired.machine, listed=desired.listed
    )
    results: list[Any] = []
    if ctx.apply:
        for ch in plan.changes:
            results.extend(_apply_change(ctx, ch))
    changes = [
        {"action": c.action, "id": c.id, "reason": c.reason, "body": c.body} for c in plan.changes
    ]
    warnings = [*desired.warnings, *plan.warnings]
    return {
        "verb": "actors enrol-agents",
        "applied": ctx.apply,
        "dry_run": not ctx.apply,
        "machine": desired.machine,
        "changes": changes,
        "warnings": warnings,
        "results": results,
        "lines": _enrol_lines(ctx, desired.machine, changes, warnings),
    }


def _discord_targets(ctx: Context, id: str) -> dict[str, Any]:
    """The servers and text channels a Discord app actor's bot can post to."""
    return _api.call(lambda: ctx.client.request("GET", f"/actors/{seg(id)}/discord/targets"))


DEFAULT_API_KEY = "grant:LOBES_GATEWAY_API_KEY"


def _check_pools(pools: list[str] | None) -> list[str]:
    """Each ``--pool`` must be ``MODEL=POOL``; the server re-checks, this fails before a request."""
    out: list[str] = []
    for item in pools or ():
        model, sep, pool = str(item).partition("=")
        if not sep or not model.strip() or not pool.strip():
            raise CliError(
                EXIT_USER_ERROR, f"bad --pool {item!r}", "use --pool MODEL=POOL (repeatable)"
            )
        out.append(f"{model.strip()}={pool.strip()}")
    return out


def _enrol_models_lines(ctx: Context, result: dict[str, Any]) -> list[str]:
    changes = result.get("changes") or []
    lines = [
        f"{'applied' if ctx.apply else 'dry-run'}: enrol-models: {len(changes)} model(s) matched "
        f"on {', '.join(g['machine'] for g in result.get('gateways') or []) or 'no gateway'}",
        *(f"  {c['action']:<9} {c['id']}  ({c.get('reason', '')})" for c in changes),
    ]
    for model, others in sorted((result.get("also_served_by") or {}).items()):
        lines.append(f"  {model} is also served by {', '.join(others)}")
    for actor in result.get("unseen") or ():
        lines.append(f"  unseen    {actor}  (no gateway lists its model)")
    for a in result.get("applied") or ():
        lines.append(f"  wrote     {a['id']} ({a['action']})")
    for f in result.get("failed") or ():
        lines.append(f"  FAILED    {f['id']} ({f['action']}): {f['code']}: {f['message']}")
    if not ctx.apply and any(c["action"] in ("create", "update") for c in changes):
        lines.append("re-run with --apply to commit")
    return lines


def _enrol_models(
    ctx: Context,
    gateway: list[str],
    match: list[str],
    pool: list[str] | None = None,
    api_key: str = DEFAULT_API_KEY,
) -> dict[str, Any]:
    """Enrol one endpoint actor per matching model the named gateways serve.

    The server lists each gateway's models and computes the plan, so even a dry-run is a call
    (with ``dry_run: true``); ``--apply`` sends ``dry_run: false``.
    """
    if not str(api_key).startswith("grant:"):
        # the value is never echoed: it may be a key typed by mistake
        raise CliError(
            EXIT_USER_ERROR,
            "--api-key must be a grant:<NAME> reference, not a key",
            f"store the key with the grant tool and pass --api-key {DEFAULT_API_KEY}",
        )
    body = {
        "gateways": list(gateway or ()),
        "match": list(match or ()),
        "pools": _check_pools(pool),
        "api_key": api_key,
        "dry_run": not ctx.apply,
    }
    result = new_route(
        ctx,
        MODEL_ACTORS_SINCE,
        lambda: ctx.client.request("POST", "/actors/enrol-models", body=body),
    )
    out = {
        "verb": "actors enrol-models",
        "applied": ctx.apply,
        "dry_run": not ctx.apply,
        "result": result,
        "lines": _enrol_models_lines(ctx, result),
    }
    if ctx.apply and result.get("failed"):
        failed = "; ".join(f"{f['id']}: {f['code']}" for f in result["failed"])
        done = ", ".join(a["id"] for a in result.get("applied") or ()) or "none"
        raise CliError(
            EXIT_USER_ERROR,
            f"enrol-models wrote {done}; failed: {failed}",
            "fix the cause and re-run (the enrolment is idempotent)",
        )
    return out


def _references(ctx: Context, id: str) -> dict[str, Any]:
    """The workflows and rules that name an actor (action actor or placement)."""
    result = new_route(
        ctx,
        MODEL_ACTORS_SINCE,
        lambda: ctx.client.request("GET", f"/actors/{seg(id)}/references"),
    )
    items = result.get("items") or []
    lines = [f"{id}: {len(items)} reference(s)" if items else f"{id}: no references"]
    lines += [
        f"  {i['kind']} {i['id']}  {i['via']} at {i['path']}"
        f"{'' if i.get('enabled') else '  (disabled)'}"
        for i in items
    ]
    return {**result, "lines": lines}


def _in_use_message(actor: str, errors: list[dict]) -> str:
    rows = "".join(f"\n  - {e.get('message') or e.get('path')}" for e in errors)
    return f"actor_in_use: {actor} is still used by:{rows}"


def _disable(ctx: Context, id: str, force: bool = False) -> dict[str, Any]:
    """Disable an actor; refused (exit 1) while enabled workflows or rules use it, unless forced.

    A dry-run also lists the enabled referrers, so the refusal is visible before ``--apply``.
    """
    path = f"/actors/{seg(id)}/disable" + ("?force=true" if force else "")
    if not ctx.apply:
        out = write(ctx, "actors disable", "POST", path, preview=f"/actors/{seg(id)}")

        def references() -> dict[str, Any]:
            try:
                return ctx.client.request("GET", f"/actors/{seg(id)}/references")
            except ApiError as exc:
                if missing_route(exc):
                    return {}  # an older API has no references route: the apply still guards
                raise

        refs = _api.call(references)
        out["in_use"] = [i for i in refs.get("items") or [] if i.get("enabled")]
        if out["in_use"] and not force:
            out["hint"] = "in use: --apply is refused unless you pass --force"
        return out

    def attempt() -> Any:
        try:
            return ctx.client.request("POST", path)
        except ApiError as exc:
            if exc.code == "actor_in_use":
                raise CliError(
                    EXIT_USER_ERROR,
                    _in_use_message(id, exc.errors),
                    "repoint them (workflows repoint-actor) or pass --force to disable anyway",
                ) from exc
            raise

    result = _api.call(attempt)
    return {"verb": "actors disable", "applied": True, "dry_run": False, "result": result}


_DISABLE_VERB = Verb(
    NOUN,
    "disable",
    "Disable an actor (refused while enabled workflows or rules use it, unless --force)",
    _disable,
    (ID, Param("force", "boolean", "disable even if workflows or rules still use the actor")),
    True,
    "editor",
)

VERBS: list[Verb] = [
    *(
        _DISABLE_VERB if v.name == "disable" else v
        for v in definition_verbs(
            NOUN, "actor", "Actors are who or what can perform work.", exchange=True
        )
    ),
    Verb(
        NOUN,
        "enrol-models",
        "Enrol one endpoint actor per model that matching gateways serve (server computes plan)",
        _enrol_models,
        (
            Param(
                "gateway",
                "array",
                "a machine name from the model_gateways variable (repeatable; order = priority)",
                True,
            ),
            Param("match", "array", "fnmatch glob of model ids to enrol (repeatable)", True),
            Param(
                "pool",
                "array",
                "MODEL=POOL concurrency pool override (repeatable; default pool: the model id)",
            ),
            Param(
                "api_key",
                "string",
                "grant:<NAME> reference for the gateway key (never a literal key)",
                default=DEFAULT_API_KEY,
            ),
        ),
        True,
        "editor",
    ),
    Verb(
        NOUN,
        "references",
        "List the workflows and rules that use an actor",
        _references,
        (ID,),
    ),
    Verb(
        NOUN,
        "enrol-agents",
        "Enrol this machine's mesh agents (culture server.yaml + culture.yaml) as agent actors",
        _enrol_agents,
        (
            Param("server_yaml", help="culture server.yaml (default ~/.culture/server.yaml)"),
            Param("machine", help="machine name to record (default: server.name)"),
        ),
        True,
        "editor",
    ),
    Verb(
        NOUN,
        "discord-targets",
        "List the Discord servers and channels a Discord app actor's bot can post to",
        _discord_targets,
        (ID,),
    ),
]


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, "Actors are who or what can perform work.")
