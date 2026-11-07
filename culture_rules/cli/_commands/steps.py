"""``culture-rules steps`` — the builtin catalogue over the HTTP API (read-only).

``builtins`` lists the built-in code steps the API's extension registry holds, in
registration order, with each builtin's label and typed input and output ports: the same data
the editor's Built-in picker is built from (``GET /steps/builtins``). An API older than
:data:`STEPS_SINCE` lacks the route, which is exit 2.
"""

from __future__ import annotations

import argparse
from typing import Any

from culture_rules.cli._build import register_noun
from culture_rules.cli._nounlib import new_route, sections_overview
from culture_rules.cli.registry import Context, Param, Verb

NOUN = "steps"
_BUILTINS = "/steps/builtins"
STEPS_SINCE = "0.45.0"  # the release that serves /steps/builtins
SUMMARY = "Steps are what a workflow is made of; builtins are the code steps the engine ships."
NAME = Param("name", help="show only this builtin", positional=True)


def _overview(ctx: Context) -> dict[str, Any]:
    return sections_overview(NOUN, SUMMARY, ctx, _BUILTINS)


def _port_line(p: dict[str, Any]) -> str:
    return f"{p['name']}: {p['type']}{' (required)' if p['required'] else ''}"


def _ports(ports: list[dict[str, Any]]) -> str:
    return ", ".join(_port_line(p) for p in ports) or "none declared"


def _line(item: dict[str, Any]) -> str:
    return (
        f"{item['name']}  {item['label']}  "
        f"[in: {_ports(item['inputs'])}; out: {_ports(item['outputs'])}]"
    )


def _builtins(ctx: Context, name: str | None = None) -> dict[str, Any]:
    items = new_route(ctx, STEPS_SINCE, lambda: ctx.client.request("GET", _BUILTINS)).get(
        "items", []
    )
    if name is not None:
        items = [i for i in items if i["name"] == name]
    return {"items": items, "lines": [_line(i) for i in items] or ["no such builtin"]}


VERBS: list[Verb] = [
    Verb(NOUN, "overview", f"Describe the {NOUN} noun and its verbs", _overview),
    Verb(
        NOUN,
        "builtins",
        "List the registered built-in code steps with their typed ports, in registration order",
        _builtins,
        (NAME,),
    ),
]


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, SUMMARY)
