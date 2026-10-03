"""Markdown catalog for ``culture-rules explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple
and ``("culture-rules",)`` both resolve to the root entry.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.
"""

from __future__ import annotations

_ROOT = """\
# culture-rules

The rules engine for the AgentCulture mesh: rules -> conditions -> workflows ->
actions, carried out by actors (agents, humans, code). It is a Python library
(`culture_rules`), a CLI and MCP server, and a React Flow editor with Rules,
Workflows, Actors and Statistics tabs.

Status: the engine is being built. Today the package carries the agent-first CLI
(cited from the teken `python-cli` reference), the mesh identity (`culture.yaml`
+ `CLAUDE.md`), the guildmaster skill kit under `.claude/skills/`, and the
CI/publish baseline. The engine, API and editor are planned.

## Verbs

- `culture-rules whoami` — identity probe from `culture.yaml`.
- `culture-rules learn` — structured self-teaching prompt.
- `culture-rules explain <path>` — markdown docs for any noun/verb.
- `culture-rules overview` — descriptive snapshot of the agent.
- `culture-rules doctor` — check the agent-identity invariants.
- `culture-rules cli overview` — describe the CLI surface.

## Exit-code policy

- `0` success
- `1` user-input error
- `2` environment / setup error
- `3+` reserved

## See also

- `culture-rules explain whoami`
- `culture-rules explain doctor`
"""

_WHOAMI = """\
# culture-rules whoami

Reports the agent's identity from `culture.yaml`: nick (`suffix`), backend,
served model, and the package version. Read-only.

## Usage

    culture-rules whoami
    culture-rules whoami --json
"""

_LEARN = """\
# culture-rules learn

Prints a structured self-teaching prompt covering purpose, command map,
exit-code policy, `--json` support, and the `explain` pointer.

## Usage

    culture-rules learn
    culture-rules learn --json
"""

_EXPLAIN = """\
# culture-rules explain <path>

Prints markdown documentation for any noun/verb path. Unlike `--help` (terse,
positional), `explain` is global and addressable by path.

## Usage

    culture-rules explain culture-rules
    culture-rules explain whoami
    culture-rules explain --json <path>
"""

_OVERVIEW = """\
# culture-rules overview

Read-only descriptive snapshot of the agent: identity (from `culture.yaml`), the
verb surface, and the sibling-pattern artifacts the package carries. Accepts an
ignored `target` so a stray path never hard-fails.

## Usage

    culture-rules overview
    culture-rules overview --json
"""

_DOCTOR = """\
# culture-rules doctor

Checks the agent-identity invariants `steward doctor` verifies:
prompt-file-present and backend-consistency (`claude` → `CLAUDE.md`), plus a
skills-present check. Exits 1 when unhealthy.

prompt-file-present requires the *resident* prompt the declared backend
actually reads. Other harness prompt files recognized under the same backend
name (`AGENTS.override.md`, `.pi/SYSTEM.md`, `QWEN.md`) belong to
interactively available harnesses the mesh daemon never loads; they are
reported by the informational harness-prompts check and never substituted.

## Usage

    culture-rules doctor
    culture-rules doctor --json
"""

_CLI = """\
# culture-rules cli

Noun group for CLI-surface introspection. `cli overview` describes the CLI
itself (distinct from the global `overview`, which describes the agent).

## Usage

    culture-rules cli overview
    culture-rules cli overview --json
"""


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    ("culture-rules",): _ROOT,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
}
