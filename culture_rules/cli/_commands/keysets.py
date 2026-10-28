"""``culture-rules keysets`` — the named sets of remembered keys, over the HTTP API (admin).

A keyset is a named set of keys, each remembered for a window (``culture_rules.keysets``).
``list`` names the sets with their live key counts, ``show`` lists one set's keys with
``first_seen`` and ``expires_at``, and ``clear`` forgets one key (``--key``) or the whole set.
Every verb needs the admin role, reads included. ``clear`` is a dry run unless ``--apply``:
the server counts what would go and deletes nothing. An API older than
:data:`KEYSETS_SINCE` lacks the routes, which is exit 2.
"""

from __future__ import annotations

import argparse
from typing import Any

from culture_rules.cli._build import register_noun
from culture_rules.cli._nounlib import new_route, sections_overview, seg
from culture_rules.cli.registry import Context, Param, Verb

NOUN = "keysets"
_ROOT = "/keysets"
KEYSETS_SINCE = "0.37.0"  # the release that serves /keysets
SUMMARY = "Keysets are named sets of keys, each remembered for a window (admin only)."
NAME = Param(
    "name", help="set name (letters, digits, _ : -; at most 100)", required=True, positional=True
)


def _get(ctx: Context, path: str) -> Any:
    return new_route(ctx, KEYSETS_SINCE, lambda: ctx.client.request("GET", path))


def _overview(ctx: Context) -> dict[str, Any]:
    return sections_overview(NOUN, SUMMARY, ctx, _ROOT)


def _lines(rows: list[dict[str, Any]], fmt: str, empty: str) -> list[str]:
    return [fmt.format(**r) for r in rows] or [empty]


def _list(ctx: Context) -> dict[str, Any]:
    items = _get(ctx, _ROOT).get("items", [])
    return {"items": items, "lines": _lines(items, "{set}  {keys} key(s)", "no keysets")}


def _show(ctx: Context, name: str) -> dict[str, Any]:
    out = _get(ctx, f"{_ROOT}/{seg(name)}")
    rows = out.get("items", [])
    fmt = "{key}  first_seen={first_seen}  expires_at={expires_at}"
    return {**out, "lines": _lines(rows, fmt, f"{name}: no live keys")}


def _clear(ctx: Context, name: str, key: str | None = None) -> dict[str, Any]:
    """Forget one key or the whole set; without ``--apply`` the server only counts."""
    body = {"key": key, "dry_run": not ctx.apply}
    result = new_route(
        ctx,
        KEYSETS_SINCE,
        lambda: ctx.client.request("POST", f"{_ROOT}/{seg(name)}/clear", body=body),
    )
    what = f"key {key!r} of {name}" if key is not None else f"set {name}"
    count = result.get("count", 0)
    head = (
        f"cleared {what}: {count} removed"
        if ctx.apply
        else f"dry-run: clear {what}: {count} would go; re-run with --apply to commit"
    )
    return {
        "verb": "keysets clear",
        "applied": ctx.apply,
        "dry_run": not ctx.apply,
        "result": result,
        "lines": [head],
    }


VERBS: list[Verb] = [
    Verb(NOUN, "overview", f"Describe the {NOUN} noun and its verbs", _overview),
    Verb(
        NOUN, "list", "List keysets with their live key counts (admin)", _list, (), False, "admin"
    ),
    Verb(
        NOUN,
        "show",
        "Show a keyset's live keys with first_seen and expires_at (admin)",
        _show,
        (NAME,),
        False,
        "admin",
    ),
    Verb(
        NOUN,
        "clear",
        "Forget one key (--key) or a whole keyset; dry-run unless --apply (admin)",
        _clear,
        (NAME, Param("key", help="forget only this key (default: the whole set)")),
        True,
        "admin",
    ),
]


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, SUMMARY)
