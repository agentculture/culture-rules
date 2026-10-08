"""``culture-rules learn`` — the learnability affordance.

Prints a structured self-teaching prompt. Must satisfy the agent-first rubric:
>=200 chars and mention purpose, command map, exit codes, --json, and explain.
"""

from __future__ import annotations

import argparse
import textwrap

from culture_rules import __version__
from culture_rules.cli._output import emit_result

_WHY = (
    "One graphical, agent-operable place to decide when work happens, how it flows across "
    "machines and who does it, and it keeps working when one machine falters, so automation "
    "stops being per-host glue only its author understands."
)

_AUDIENCES = [
    {
        "who": "The operator",
        "how": "composing and supervising automation across spark, thor and spark2 from a "
        "browser at rules.culture.dev.",
    },
    {
        "who": "Mesh agents",
        "how": "that drive the same rules, workflows and actors through the culture-rules "
        "CLI and MCP server.",
    },
]

_TEXT = """\
culture-rules — the rules engine for the AgentCulture mesh.

Purpose
-------
Rules -> conditions -> workflows -> actions, carried out by actors (agents,
humans, code). It is a Python library (culture_rules) with a CLI, an HTTP API,
an MCP server, an engine node per host, and a React Flow editor with five tabs
(Rules | Workflows | Actors | Variables | Statistics).

Who it is for
-------------
Two readers, one system:
  - The operator composing and supervising automation across spark, thor and
    spark2 from a browser at rules.culture.dev.
  - Mesh agents that drive the same rules, workflows and actors through the
    culture-rules CLI and MCP server.

Why
---
{why}

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
Disabling a rule (rules disable, or an update that sets enabled false) never stops
its current runs: the write reports them as active_runs with a hint, and
rules stop-runs <id> --apply cancels them (status cancelled, nothing more is pushed).
rules describe <id> and workflows describe <id> print a definition in plain words,
built only from its config (When/If/Run/Then lines; numbered steps), never by AI.

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


def _wrap(text: str) -> str:
    return textwrap.fill(text, width=79)


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
        "purpose": "Rules engine for the AgentCulture mesh: rules -> conditions -> workflows -> "
        "actions, carried out by actors.",
        "audiences": _AUDIENCES,
        "why": _WHY,
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
        text = _TEXT.replace("{noun_verbs}", _noun_verb_lines()).replace("{why}", _wrap(_WHY))
        emit_result(text, json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "learn",
        help="Print a structured self-teaching prompt for agent consumers.",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_learn)
