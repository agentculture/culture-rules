"""``culture-rules actions`` — the action catalogue over the HTTP API (read-only).

``kinds`` lists the action kinds the API's extension registry holds, in catalogue order, with
each kind's typed params: the same data the editor's action picker is built from
(``GET /actions/kinds``). An API older than :data:`ACTIONS_SINCE` lacks the route, which is
exit 2.
"""

from __future__ import annotations

import argparse
from typing import Any

from culture_rules.cli._build import register_noun
from culture_rules.cli._nounlib import new_route, sections_overview
from culture_rules.cli.registry import Context, Param, Verb

NOUN = "actions"
_KINDS = "/actions/kinds"
ACTIONS_SINCE = "0.44.0"  # the release that serves /actions/kinds
SUMMARY = "Actions are the registered kinds of work a rule's workflow can end in."
KIND = Param("kind", help="show only this kind (a name or a legacy alias)", positional=True)


def _overview(ctx: Context) -> dict[str, Any]:
    return sections_overview(NOUN, SUMMARY, ctx, _KINDS)


def _param_line(p: dict[str, Any]) -> str:
    return f"{p['name']}: {p['type']}{' (required)' if p['required'] else ''}"


def _line(item: dict[str, Any]) -> str:
    params = ", ".join(_param_line(p) for p in item["params"]) or "no params"
    return f"{item['kind']}  {item['summary']}  [{params}]"


def _kinds(ctx: Context, kind: str | None = None) -> dict[str, Any]:
    items = new_route(ctx, ACTIONS_SINCE, lambda: ctx.client.request("GET", _KINDS)).get(
        "items", []
    )
    if kind is not None:
        items = [i for i in items if kind == i["kind"] or kind in i["aliases"]]
    return {"items": items, "lines": [_line(i) for i in items] or ["no such action kind"]}


VERBS: list[Verb] = [
    Verb(NOUN, "overview", f"Describe the {NOUN} noun and its verbs", _overview),
    Verb(
        NOUN,
        "kinds",
        "List the registered action kinds with their typed params, in catalogue order",
        _kinds,
        (KIND,),
    ),
]


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, SUMMARY)
