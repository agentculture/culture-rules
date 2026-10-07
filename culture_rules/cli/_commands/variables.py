"""``culture-rules variables`` — shared variables over the HTTP API.

Reads are viewer; ``set``, ``add`` and ``remove`` need the admin role and append a version
naming the caller. ``add`` / ``remove`` edit one item of a list variable atomically (the server
retries a compare-and-set on the version), so two callers adding at once both land.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from culture_rules.cli import _api
from culture_rules.cli._build import register_noun
from culture_rules.cli._errors import EXIT_USER_ERROR, CliError
from culture_rules.cli._nounlib import sections_overview, seg, write
from culture_rules.cli.registry import Context, Param, Verb

NOUN = "variables"
SUMMARY = "Variables are shared values (a JSON scalar or flat list) rules read."
NAME = Param(
    "name", help="variable name (a-z, 0-9, _; starts with a letter)", required=True, positional=True
)


def _overview(ctx: Context) -> dict[str, Any]:
    return sections_overview(NOUN, SUMMARY, ctx, "/variables")


def _list(ctx: Context) -> Any:
    return _api.call(lambda: ctx.client.request("GET", "/variables"))


def _get(ctx: Context, name: str) -> Any:
    return _api.call(lambda: ctx.client.request("GET", f"/variables/{seg(name)}"))


def _history(ctx: Context, name: str) -> Any:
    return _api.call(lambda: ctx.client.request("GET", f"/variables/{seg(name)}/history"))


def _refs(ctx: Context, name: str) -> Any:
    return _api.call(lambda: ctx.client.request("GET", f"/variables/{seg(name)}/refs"))


def _set(ctx: Context, name: str, value: Any, description: str | None = None) -> dict[str, Any]:
    """Append a version. A dry-run sends no PUT; it shows the current version if there is one."""
    body: dict[str, Any] = {"value": value}
    if description is not None:
        body["description"] = description
    path = f"/variables/{seg(name)}"
    out = write(ctx, "variables set", "PUT", path, body)
    if not ctx.apply:  # the list route answers "no such variable" without a 404
        items = _api.call(lambda: ctx.client.request("GET", "/variables")).get("items", [])
        out["current"] = next((v for v in items if v.get("name") == name), None)
    return out


def _coerce(item: str, value: Any) -> Any:
    """The CLI's text ``item`` as the list's item type: a number or boolean when the list
    holds those (the text must parse as one), else the text itself."""
    kinds = {type(v).__name__ for v in value} if isinstance(value, list) else set()
    if kinds and kinds <= {"int", "float", "bool"}:
        try:
            parsed = json.loads(item)
        except ValueError:
            parsed = None
        ok = (
            isinstance(parsed, bool)
            if kinds == {"bool"}
            else (isinstance(parsed, (int, float)) and not isinstance(parsed, bool))
        )
        if not ok:
            raise CliError(
                EXIT_USER_ERROR,
                f"{item!r} is not a {' or '.join(sorted(kinds))} like the list's items",
                "pass an item of the list's type",
            )
        return parsed
    return item


def _edit(ctx: Context, op: str, name: str, item: str) -> dict[str, Any]:
    current = _get(ctx, name)
    value = current.get("value")
    body = {"item": _coerce(item, value)}
    path = f"/variables/{seg(name)}/items/{op}"
    out = write(ctx, f"variables {op}", "POST", path, body)
    if not ctx.apply:
        present = isinstance(value, list) and body["item"] in value
        out["current"] = current
        out["would_change"] = (not present) if op == "add" else present
    return out


def _add(ctx: Context, name: str, item: str) -> dict[str, Any]:
    """Add one item to a list variable; already present writes no version."""
    return _edit(ctx, "add", name, item)


def _remove(ctx: Context, name: str, item: str) -> dict[str, Any]:
    """Remove one item from a list variable; absent writes no version."""
    return _edit(ctx, "remove", name, item)


ITEM = Param(
    "item",
    help="the item (text; a number or boolean when the list holds those), e.g. owner/repo",
    required=True,
    positional=True,
)

VERBS: list[Verb] = [
    Verb(NOUN, "overview", f"Describe the {NOUN} noun and its verbs", _overview),
    Verb(NOUN, "list", "List variables (latest version of each)", _list),
    Verb(NOUN, "get", "Show a variable's current value and version", _get, (NAME,)),
    Verb(
        NOUN,
        "set",
        "Set a variable: appends a new version naming you (admin)",
        _set,
        (
            NAME,
            Param("value", "any", "the new value: JSON scalar or flat list", required=True),
            Param("description", help="what the variable is for"),
        ),
        True,
        "admin",
    ),
    Verb(
        NOUN,
        "add",
        "Add an item to a list variable atomically; no new version if present (admin)",
        _add,
        (NAME, ITEM),
        True,
        "admin",
    ),
    Verb(
        NOUN,
        "remove",
        "Remove an item from a list variable atomically; no new version if absent (admin)",
        _remove,
        (NAME, ITEM),
        True,
        "admin",
    ),
    Verb(NOUN, "history", "Show every version of a variable, oldest first", _history, (NAME,)),
    Verb(NOUN, "refs", "List the rules that reference a variable", _refs, (NAME,)),
]


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, SUMMARY)
