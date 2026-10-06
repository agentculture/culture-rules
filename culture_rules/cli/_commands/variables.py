"""``culture-rules variables`` — shared variables over the HTTP API.

Reads are viewer; ``set`` needs the admin role and appends a version naming the caller.
"""

from __future__ import annotations

import argparse
from typing import Any

from culture_rules.cli import _api
from culture_rules.cli._build import register_noun
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
    Verb(NOUN, "history", "Show every version of a variable, oldest first", _history, (NAME,)),
    Verb(NOUN, "refs", "List the rules that reference a variable", _refs, (NAME,)),
]


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, SUMMARY)
