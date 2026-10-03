"""``culture-rules workflows`` — workflows over the HTTP API."""

from __future__ import annotations

import argparse

from culture_rules.cli._build import register_noun
from culture_rules.cli._nounlib import definition_verbs
from culture_rules.cli.registry import Verb

NOUN = "workflows"
VERBS: list[Verb] = definition_verbs(
    NOUN, "workflow", "Workflows are the reusable work a rule runs.", exchange=True
)


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, NOUN, "Workflows are the reusable work a rule runs.")
