"""``culture-rules variables`` — shared variables over the HTTP API.

Reads are viewer; ``set``, ``add`` and ``remove`` need the admin role and append a version
naming the caller. ``add`` / ``remove`` edit one item of a list variable atomically (the server
retries a compare-and-set on the version), so two callers adding at once both land.
"""

from __future__ import annotations

import argparse
import json
import math
from typing import Any

from culture_rules.cli import _api
from culture_rules.cli._build import register_noun
from culture_rules.cli._errors import EXIT_USER_ERROR, CliError
from culture_rules.cli._nounlib import sections_overview, seg, write
from culture_rules.cli.registry import Context, Param, Verb

NOUN = "variables"
_ROOT = "/variables"
SUMMARY = "Variables are shared values (a JSON scalar or flat list) rules read."
NAME = Param(
    "name", help="variable name (a-z, 0-9, _; starts with a letter)", required=True, positional=True
)


def _overview(ctx: Context) -> dict[str, Any]:
    return sections_overview(NOUN, SUMMARY, ctx, _ROOT)


def _list(ctx: Context) -> Any:
    return _api.call(lambda: ctx.client.request("GET", _ROOT))


def _get(ctx: Context, name: str) -> Any:
    return _api.call(lambda: ctx.client.request("GET", f"{_ROOT}/{seg(name)}"))


def _history(ctx: Context, name: str) -> Any:
    return _api.call(lambda: ctx.client.request("GET", f"{_ROOT}/{seg(name)}/history"))


def _refs(ctx: Context, name: str) -> Any:
    return _api.call(lambda: ctx.client.request("GET", f"{_ROOT}/{seg(name)}/refs"))


def _set(ctx: Context, name: str, value: Any, description: str | None = None) -> dict[str, Any]:
    """Append a version. A dry-run sends no PUT; it shows the current version if there is one."""
    body: dict[str, Any] = {"value": value}
    if description is not None:
        body["description"] = description
    path = f"{_ROOT}/{seg(name)}"
    out = write(ctx, "variables set", "PUT", path, body)
    if not ctx.apply:  # the list route answers "no such variable" without a 404
        items = _api.call(lambda: ctx.client.request("GET", _ROOT)).get("items", [])
        out["current"] = next((v for v in items if v.get("name") == name), None)
    return out


def _kind(value: Any) -> str:
    """A list item's JSON type, as the service judges it (a boolean is not a number)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string" if isinstance(value, str) else "other"


def _json_scalar(item: str) -> Any:
    """``--json-item``: the text parsed as one finite JSON scalar."""
    try:
        parsed = json.loads(item)
    except ValueError as exc:
        raise CliError(
            EXIT_USER_ERROR, f"--json-item: {item!r} is not JSON", "quote a string: '\"123\"'"
        ) from exc
    if _kind(parsed) == "other" or (isinstance(parsed, float) and not math.isfinite(parsed)):
        raise CliError(
            EXIT_USER_ERROR,
            f"--json-item: {item!r} is not a finite JSON scalar",
            "an item is a string, number, boolean or null",
        )
    return parsed


def _coerce(item: str, value: Any, *, op: str, json_item: bool) -> Any:
    """The CLI's text ``item`` as one of the list's own item types.

    With ``--json-item`` the text is JSON (``'"123"'`` is the string, ``123`` the number).
    Otherwise it is the parsed scalar when that scalar's type - number, boolean or null - is
    one the list holds, else the text itself when the list holds strings, is empty, or (for
    ``remove``) cannot hold it anyway; an ``add`` of a type the list does not hold is refused
    here as the service would refuse it."""
    if json_item:
        return _json_scalar(item)
    kinds = {_kind(v) for v in value} if isinstance(value, list) else set()
    try:
        parsed = json.loads(item)
    except ValueError:
        parsed = item
    if _kind(parsed) in kinds - {"string"}:
        return parsed
    if not kinds or "string" in kinds or op == "remove":
        return item
    raise CliError(
        EXIT_USER_ERROR,
        f"{item!r} is not a {' or '.join(sorted(kinds))} like the list's items",
        "pass an item of one of the list's types, or a typed one with --json-item",
    )


def _present(value: Any, item: Any) -> bool:
    return isinstance(value, list) and any(_kind(v) == _kind(item) and v == item for v in value)


def _edit(ctx: Context, op: str, name: str, item: str, json_item: bool) -> dict[str, Any]:
    current = _get(ctx, name)
    value = current.get("value")
    body = {"item": _coerce(item, value, op=op, json_item=json_item)}
    path = f"{_ROOT}/{seg(name)}/items/{op}"
    out = write(ctx, f"variables {op}", "POST", path, body)
    if not ctx.apply:
        present = _present(value, body["item"])
        out["current"] = current
        out["would_change"] = (not present) if op == "add" else present
    return out


def _add(ctx: Context, name: str, item: str, json_item: bool = False) -> dict[str, Any]:
    """Add one item to a list variable; already present writes no version."""
    return _edit(ctx, "add", name, item, json_item)


def _remove(ctx: Context, name: str, item: str, json_item: bool = False) -> dict[str, Any]:
    """Remove one item from a list variable; absent writes no version."""
    return _edit(ctx, "remove", name, item, json_item)


ITEM = Param(
    "item",
    help=(
        "the item, e.g. owner/repo: text, or a number/boolean/null when the list holds that type"
    ),
    required=True,
    positional=True,
)
JSON_ITEM = Param(
    "json_item",
    "boolean",
    "parse ITEM as one JSON scalar ('\"123\"' is a string, 123 a number)",
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
        (NAME, ITEM, JSON_ITEM),
        True,
        "admin",
    ),
    Verb(
        NOUN,
        "remove",
        "Remove an item from a list variable atomically; no new version if absent (admin)",
        _remove,
        (NAME, ITEM, JSON_ITEM),
        True,
        "admin",
    ),
    Verb(NOUN, "history", "Show every version of a variable, oldest first", _history, (NAME,)),
    Verb(NOUN, "refs", "List the rules that reference a variable", _refs, (NAME,)),
]


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, SUMMARY)
