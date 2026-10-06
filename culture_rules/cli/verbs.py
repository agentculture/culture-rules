"""The one command registry: every noun module's verbs, registered once.

The CLI parser, ``learn``, ``explain``, the MCP server and the parity test all enumerate
:data:`REGISTRY`; nothing is registered anywhere else.
"""

from __future__ import annotations

from culture_rules.cli._commands import actors, machines, rules, runs, variables, workflows
from culture_rules.cli.registry import Registry

__all__ = ["REGISTRY"]

REGISTRY = Registry()
for _module in (rules, workflows, actors, machines, runs, variables):
    REGISTRY.extend(_module.VERBS)
