"""``culture-rules rules`` — rules over the HTTP API."""

from __future__ import annotations

import argparse
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


VERBS: list[Verb] = [
    *definition_verbs(NOUN, "rule", "Rules say when work should happen.", exchange=True),
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
