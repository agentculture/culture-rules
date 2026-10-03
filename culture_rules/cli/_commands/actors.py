"""``culture-rules actors`` — actors over the HTTP API."""

from __future__ import annotations

import argparse

from culture_rules.cli._build import register_noun
from culture_rules.cli._nounlib import definition_verbs
from culture_rules.cli.registry import Verb

NOUN = "actors"
VERBS: list[Verb] = definition_verbs(
    NOUN, "actor", "Actors are who or what can perform work.", exchange=True
)


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, "Actors are who or what can perform work.")
