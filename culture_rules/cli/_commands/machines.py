"""``culture-rules machines`` — machines over the HTTP API."""

from __future__ import annotations

import argparse
from typing import Any

from culture_rules.cli._build import register_noun
from culture_rules.cli._nounlib import ID, definition_verbs, seg, write
from culture_rules.cli.registry import Context, Verb

NOUN = "machines"
VERBS: list[Verb] = definition_verbs(
    NOUN, "machine", "Machines are the hosts that run steps.", exchange=False
)


def _drain(name: str):
    def handler(ctx: Context, id: str) -> Any:
        return write(ctx, f"machines {name}", "POST", f"/machines/{seg(id)}/{name}")

    return handler


VERBS += [
    Verb(
        NOUN,
        "drain",
        "Stop new steps being placed on a machine",
        _drain("drain"),
        (ID,),
        True,
        "admin",
    ),
    Verb(
        NOUN,
        "undrain",
        "Allow steps on a drained machine again",
        _drain("undrain"),
        (ID,),
        True,
        "admin",
    ),
]


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, "Machines are the hosts that run steps.")
