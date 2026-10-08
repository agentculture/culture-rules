"""``culture-rules workflows`` — workflows over the HTTP API."""

from __future__ import annotations

import argparse
import json
from typing import Any

from culture_rules.cli._build import register_noun
from culture_rules.cli._errors import EXIT_USER_ERROR, CliError
from culture_rules.cli._nounlib import ID, definition_verbs, describe_verb, seg, write
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


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, "Workflows are the reusable work a rule runs.")
