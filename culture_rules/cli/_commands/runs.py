"""``culture-rules runs`` — runs and engine containment over the HTTP API."""

from __future__ import annotations

import argparse
from typing import Any

from culture_rules.cli import _api
from culture_rules.cli._build import register_noun
from culture_rules.cli._nounlib import ID, migration, sections_overview, seg, write
from culture_rules.cli.registry import Context, Param, Verb

NOUN = "runs"


def _overview(ctx: Context) -> Any:
    return sections_overview(NOUN, "Runs are executions of a rule's workflow.", ctx, "/runs")


def _list(
    ctx: Context, status: str | None = None, rule_id: str | None = None, limit: int = 100
) -> Any:
    q = {"status": status, "rule_id": rule_id, "limit": limit}
    return _api.call(lambda: ctx.client.request("GET", "/runs", query=q))


def _show(ctx: Context, id: str) -> Any:
    return _api.call(lambda: ctx.client.request("GET", f"/runs/{seg(id)}"))


def _controls(ctx: Context) -> Any:
    return _api.call(lambda: ctx.client.request("GET", "/controls"))


def _cancel(ctx: Context, id: str, reason: str = "") -> Any:
    return write(
        ctx,
        "runs cancel",
        "POST",
        f"/runs/{seg(id)}/cancel",
        {"reason": reason},
        preview=f"/runs/{seg(id)}",
    )


def _backfill_ids(ctx: Context) -> Any:
    return migration(ctx, "runs backfill-ids", "/runs/backfill-ids")


def _toggle(name: str):
    def handler(ctx: Context) -> Any:
        return write(ctx, f"runs {name}", "POST", f"/controls/{name}", preview="/controls")

    return handler


VERBS: list[Verb] = [
    Verb(NOUN, "overview", "Describe the runs noun and its verbs", _overview),
    Verb(
        NOUN,
        "list",
        "List runs, newest first",
        _list,
        (
            Param("status", help="only runs in this status"),
            Param("rule_id", help="only runs of this rule"),
            Param("limit", "integer", "maximum runs", default=100),
        ),
    ),
    Verb(NOUN, "show", "Show one run in full", _show, (ID,)),
    Verb(NOUN, "controls", "Show whether the engine is paused and which machines drain", _controls),
    Verb(
        NOUN,
        "cancel",
        "Cancel a running run",
        _cancel,
        (ID, Param("reason", help="why it is cancelled")),
        True,
        "editor",
    ),
    Verb(
        NOUN,
        "backfill-ids",
        "Fill rule_id/workflow_id on legacy run documents (admin migration)",
        _backfill_ids,
        (),
        True,
        "admin",
    ),
    Verb(NOUN, "pause", "Pause the engine: no new runs start", _toggle("pause"), (), True, "admin"),
    Verb(NOUN, "resume", "Resume a paused engine", _toggle("resume"), (), True, "admin"),
]


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, "Runs: inspect, cancel, pause and resume execution.")
