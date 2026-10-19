"""``culture-rules workflows`` — workflows over the HTTP API."""

from __future__ import annotations

import argparse
import json
from typing import Any

from culture_rules.cli import _api
from culture_rules.cli._build import register_noun
from culture_rules.cli._errors import EXIT_USER_ERROR, CliError
from culture_rules.cli._nounlib import (
    ID,
    MODEL_ACTORS_SINCE,
    definition_verbs,
    describe_verb,
    migration,
    new_route,
    seg,
    write,
)
from culture_rules.cli.registry import Context, Param, Verb

NOUN = "workflows"
VERBS: list[Verb] = definition_verbs(
    NOUN, "workflow", "Workflows are the reusable work a rule runs.", exchange=True
)
VERBS.append(describe_verb(NOUN, "workflow", "its numbered steps"))


def _parse_inputs(inputs_json: dict | None, inputs: list[str] | None) -> dict[str, Any]:
    """``--inputs-json`` as the base, then each ``--input name=value`` on top of it."""
    if inputs_json is not None and not isinstance(inputs_json, dict):
        raise CliError(EXIT_USER_ERROR, "--inputs-json must be a JSON object", "pass {...}")
    out: dict[str, Any] = dict(inputs_json or {})
    for item in inputs or ():
        name, sep, raw = str(item).partition("=")
        if not sep or not name:
            raise CliError(
                EXIT_USER_ERROR, f"bad --input {item!r}", "use --input name=value (repeatable)"
            )
        try:
            out[name] = json.loads(raw)
        except ValueError:
            out[name] = raw
    return out


def _run(
    ctx: Context, id: str, input: list[str] | None = None, inputs_json: dict | None = None
) -> dict[str, Any]:
    # No server-side validate-only path exists: a dry-run GETs the workflow (so a missing id
    # fails) and shows the request it would send; the server validates the inputs on --apply
    # and answers 422 with errors[].path naming the port.
    path = f"/workflows/{seg(id)}"
    body = {"inputs": _parse_inputs(inputs_json, input)}
    return write(ctx, "workflows run", "POST", f"{path}/run", body, preview=path)


VERBS.append(
    Verb(
        NOUN,
        "run",
        "Start a run of a workflow directly with typed inputs",
        _run,
        (
            ID,
            Param(
                "input",
                "array",
                "an input as name=value (repeatable; value is JSON when it parses, else text)",
            ),
            Param("inputs_json", "object", "all inputs as a JSON object (--input overrides)"),
        ),
        True,
        "editor",
    )
)


def _migrate_notes(ctx: Context) -> Any:
    return migration(ctx, "workflows migrate-notes", "/workflows/migrate-notes")


VERBS.append(
    Verb(
        NOUN,
        "migrate-notes",
        "List workflows whose description moves to notes; --apply moves it (audited)",
        _migrate_notes,
        (),
        True,
        "admin",
    )
)


def _repoint_actor(
    ctx: Context,
    id: str,
    to: str,
    expected_revision: int | None = None,
    force: bool = False,
    **named: Any,
) -> dict[str, Any]:
    """Point a workflow's action steps (and placement) at another actor, one workflow at a time.

    The server plans the change itself, so even a dry-run is a call (``dry_run: true``) that
    reads and writes nothing; ``--apply`` sends ``dry_run: false``.
    """
    from_actor, to_actor = str(named.get("from") or ""), to  # `from` is a Python keyword
    if not from_actor:
        raise CliError(EXIT_USER_ERROR, "--from is required", "name the actor to replace")
    path = f"/workflows/{seg(id)}"
    if expected_revision is None:
        current = _api.call(lambda: ctx.client.request("GET", path))
        expected_revision = int(current.get("revision") or 0)
    body = {
        "from": from_actor,
        "to": to_actor,
        "expected_revision": expected_revision,
        "force": bool(force),
        "dry_run": not ctx.apply,
    }
    result = new_route(
        ctx,
        MODEL_ACTORS_SINCE,
        lambda: ctx.client.request("POST", f"{path}/repoint-actor", body=body),
    )
    changed = result.get("changed") or []
    lines = [
        f"{'applied' if ctx.apply else 'dry-run'}: {id}: {from_actor} -> {to_actor}: "
        f"{len(changed)} place(s), revision {result.get('revision')}",
        *(f"  {c}" for c in changed),
        *(f"  warning: {w}" for w in result.get("warnings") or ()),
    ]
    if not ctx.apply:
        lines.append("re-run with --apply to commit")
    return {
        "verb": "workflows repoint-actor",
        "applied": ctx.apply,
        "dry_run": not ctx.apply,
        "result": result,
        "lines": lines,
    }


VERBS.append(
    Verb(
        NOUN,
        "repoint-actor",
        "Point a workflow's steps from one actor to another (dry-run plans it on the server)",
        _repoint_actor,
        (
            ID,
            Param("from", help="the actor the steps name today", required=True),
            Param("to", help="an existing, enabled actor", required=True),
            Param(
                "expected_revision",
                "integer",
                "the workflow revision you read (default: read the current one, then send it)",
            ),
            Param("force", "boolean", "repoint a trusted workflow anyway (drops its trust)"),
        ),
        True,
        "editor",
    )
)


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, "Workflows are the reusable work a rule runs.")
