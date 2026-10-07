"""``culture-rules rules`` — rules over the HTTP API."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from culture_rules.cli import _api
from culture_rules.cli._build import register_noun
from culture_rules.cli._nounlib import ID, definition_verbs, migration, seg, write
from culture_rules.cli.registry import Context, Param, Verb

NOUN = "rules"


def _run(ctx: Context, id: str, trigger: dict | None = None) -> Any:
    _api.call(lambda: ctx.client.request("GET", f"/rules/{seg(id)}"))  # 404 even in a dry-run
    return write(ctx, "rules run", "POST", "/runs", {"rule_id": id, "trigger": trigger or {}})


def _replay(ctx: Context, rule_id: str | None = None, limit: int | None = None) -> Any:
    body = {"rule_id": rule_id, "limit": limit}
    return _api.call(lambda: ctx.client.request("POST", "/replay", body=body))


def _migrate_typeless(ctx: Context) -> Any:
    return migration(ctx, "rules migrate-typeless", "/rules/migrate-typeless")


def stop_hint(rule_id: str, total: int) -> str:
    return (
        f"{total} current run(s) of {rule_id} are still active; disabling does not stop them. "
        f"Stop them with: culture-rules rules stop-runs {rule_id} --apply"
    )


def _reporting_active_runs(handler: Callable[..., Any]) -> Callable[..., Any]:
    """d17: an applied write that switched the rule off reports its active runs with a hint."""

    def wrapped(ctx: Context, id: str, **params: Any) -> Any:
        out = handler(ctx, id, **params)
        result = out.get("result") if isinstance(out, dict) and out.get("applied") else None
        total = result.get("active_runs_total") if isinstance(result, dict) else None
        if total:
            out["hint"] = stop_hint(id, total)
        return out

    return wrapped


def _stop_runs(ctx: Context, id: str, reason: str | None = None) -> Any:
    body: dict[str, Any] = {"apply": ctx.apply}
    if reason:
        body["reason"] = reason
    result = _api.call(lambda: ctx.client.request("POST", f"/rules/{seg(id)}/stop-runs", body=body))
    return {
        "verb": "rules stop-runs",
        "applied": ctx.apply,
        "dry_run": not ctx.apply,
        "result": result,
    }


def _definition_verbs() -> list[Verb]:
    verbs = definition_verbs(NOUN, "rule", "Rules say when work should happen.", exchange=True)
    for v in verbs:
        if v.name in ("disable", "update"):
            v.handler = _reporting_active_runs(v.handler)
    return verbs


VERBS: list[Verb] = [
    *_definition_verbs(),
    Verb(
        NOUN,
        "run",
        "Start a run of a rule now",
        _run,
        (ID, Param("trigger", "object", "trigger payload (JSON)")),
        True,
        "editor",
    ),
    Verb(
        NOUN,
        "stop-runs",
        "Cancel every active run of a disabled rule (d17); a dry-run lists them",
        _stop_runs,
        (ID, Param("reason", help="recorded on each cancelled run")),
        True,
        "editor",
    ),
    Verb(
        NOUN,
        "migrate-typeless",
        "List event rules with no event type; --apply disables them (audited, never deleted)",
        _migrate_typeless,
        (),
        True,
        "admin",
    ),
    Verb(
        NOUN,
        "replay",
        "Replay recorded events through matching; reports would-fire runs, runs nothing",
        _replay,
        (
            Param("rule_id", "string", "report only this rule"),
            Param("limit", "integer", "replay at most N events"),
        ),
        False,
        "viewer",
    ),
]


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, "Rules: when work should happen (list/show/create/run/...).")
