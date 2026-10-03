"""``culture-rules learn`` — the learnability affordance.

Prints a structured self-teaching prompt. Must satisfy the agent-first rubric:
>=200 chars and mention purpose, command map, exit codes, --json, and explain.
"""

from __future__ import annotations

import argparse

from culture_rules import __version__
from culture_rules.cli._output import emit_result

_TEXT = """\
culture-rules — the rules engine for the AgentCulture mesh.

Purpose
-------
Rules -> conditions -> workflows -> actions, carried out by actors (agents,
humans, code). Planned shape: a Python library (culture_rules), a CLI and MCP
server, and a React Flow editor (Rules, Workflows, Actors, Statistics tabs).
Today (the engine is being built): an agent-first CLI (cited from the teken
`python-cli` reference), an identity (culture.yaml + CLAUDE.md), the guildmaster
skill kit under .claude/skills/, and a deploy/CI baseline.

Commands
--------
  culture-rules whoami             Identity from culture.yaml.
  culture-rules learn              This self-teaching prompt.
  culture-rules explain <path>...  Markdown docs for any noun/verb path.
  culture-rules overview           Descriptive snapshot of the agent.
  culture-rules doctor             Check the agent-identity invariants.
  culture-rules cli overview       Describe the CLI surface itself.
  culture-rules serve              Run the HTTP API (needs the 'server' extra).
  culture-rules node run           Run this host's engine node (--once: one cycle).
  culture-rules mcp                Serve the CLI verbs as MCP tools over stdio ('mcp' extra).
{noun_verbs}
Every noun verb below is dry-run unless --apply (writes change nothing without it);
the CLI talks only to the HTTP API (CULTURE_RULES_API_URL, CULTURE_RULES_TOKEN).

Machine-readable output
-----------------------
Every command supports --json. Errors in JSON mode emit
{"code", "message", "remediation"} to stderr. Stdout and stderr never mix.

Exit-code policy
----------------
  0 success
  1 user-input error (bad flag, bad path, missing arg)
  2 environment / setup error
  3+ reserved

More detail
-----------
  culture-rules explain culture-rules
"""


def _noun_verb_lines() -> str:
    from culture_rules.cli.verbs import REGISTRY  # noqa: PLC0415

    return "\n".join(
        f"  culture-rules {v.noun} {v.name:<10} {v.summary}"
        + (" [write: --apply]" if v.mutating else "")
        for v in REGISTRY.verbs()
    )


def _noun_commands() -> list[dict[str, object]]:
    from culture_rules.cli.verbs import REGISTRY  # noqa: PLC0415

    return [
        {
            "path": list(v.path),
            "summary": v.summary,
            "mutating": v.mutating,
            "role": v.role,
        }
        for v in REGISTRY.verbs()
    ]


def _as_json_payload() -> dict[str, object]:
    return {
        "tool": "culture-rules",
        "version": __version__,
        "purpose": "Rules engine for the AgentCulture mesh (engine being built).",
        "commands": [
            {"path": ["whoami"], "summary": "Identity probe from culture.yaml."},
            {"path": ["learn"], "summary": "Self-teaching prompt."},
            {"path": ["explain"], "summary": "Markdown docs by path."},
            {"path": ["overview"], "summary": "Descriptive snapshot of the agent."},
            {"path": ["doctor"], "summary": "Check the agent-identity invariants."},
            {"path": ["cli", "overview"], "summary": "Describe the CLI surface."},
            {"path": ["serve"], "summary": "Run the HTTP API (needs the 'server' extra)."},
            {
                "path": ["node", "run"],
                "summary": "Run this host's engine node (talks to the store).",
            },
            {
                "path": ["mcp"],
                "summary": "Serve the CLI verbs as MCP tools over stdio (needs the 'mcp' extra).",
            },
            *_noun_commands(),
        ],
        "exit_codes": {
            "0": "success",
            "1": "user-input error",
            "2": "environment/setup error",
        },
        "json_support": True,
        "explain_pointer": "culture-rules explain <path>",
    }


def cmd_learn(args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        emit_result(_as_json_payload(), json_mode=True)
    else:
        emit_result(_TEXT.replace("{noun_verbs}", _noun_verb_lines()), json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "learn",
        help="Print a structured self-teaching prompt for agent consumers.",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_learn)
