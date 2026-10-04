"""``culture-rules actors`` — actors over the HTTP API."""

from __future__ import annotations

import argparse
from typing import Any

from culture_rules.cli import _api
from culture_rules.cli._build import register_noun
from culture_rules.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from culture_rules.cli._nounlib import definition_verbs, seg
from culture_rules.cli.registry import Context, Param, Verb

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


VERBS: list[Verb] = [
    *definition_verbs(NOUN, "actor", "Actors are who or what can perform work.", exchange=True),
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
]


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, "Actors are who or what can perform work.")
