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
- `culture-rules rules|workflows|actors|machines|runs <verb>` — the engine's nouns over the
  HTTP API; `culture-rules explain <noun>` lists each noun's verbs.
- `culture-rules serve` — run the HTTP API (needs the `server` extra).
- `culture-rules node run` — run this host's engine node (talks to the store directly).
- `culture-rules mcp` — serve the CLI verbs as MCP tools over stdio (needs the `mcp` extra).

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

_SERVE = """\
# culture-rules serve

Runs the HTTP API under uvicorn (needs `pip install 'culture-rules[server]'` and, for the
default store, the `store` extra). Stateless: run as many copies as you like against one store.

## Usage

    culture-rules serve --host 127.0.0.1 --port 8765
    culture-rules serve --admin alice
"""

_NODE = """\
# culture-rules node

The engine node daemon: one per host, it *is* the rules engine. Unlike the other nouns it
talks to the store directly (`CULTURE_RULES_MONGO_*`, needs the `store` extra), not the API.

## Verbs

- `culture-rules node run` — run the node loop (or one cycle with `--once`).

## Usage

    culture-rules node run --once --host spark --json
"""

_NODE_RUN = """\
# culture-rules node run

Runs this host's engine node: heartbeat (with the platform probe), event ingest (events-cli,
optional `events` extra), rule evaluation (placed rules only on their host, unplaced rules
once across hosts; a drained/offline host keeps its placed rules' events), run starts and
the executor loop. `--once` performs one full cycle and exits (0, or 2 when a stage
failed); without it the node loops until SIGINT/SIGTERM and stops gracefully.

## Parameters

- `--host` (string) — this node's machine name (default: the short hostname)
- `--once` (boolean) — one full cycle, then exit
- `--idle` (number) — pause between cycles in seconds (default 1)
- `--json` (boolean) — report what the cycle did as JSON

## Usage

    culture-rules node run --once --host spark --json
    culture-rules node run --host spark
"""

_MCP = """\
# culture-rules mcp

Serves every registered noun verb as an MCP tool over stdio, for an MCP client (an agent
harness) that launches it as a subprocess. The tools talk to the HTTP API exactly like the
CLI does (`CULTURE_RULES_API_URL`, `CULTURE_RULES_TOKEN`); writes stay dry-run unless the
tool call passes `apply`. Stdout is the protocol channel; diagnostics go to stderr.

Needs `pip install 'culture-rules[mcp]'`; without it the command exits `2` with that hint.

## Usage

    culture-rules mcp
    python -m culture_rules.mcp
"""

_NOUN_BLURBS = {
    "rules": "Rules say *when* work should happen: trigger, condition, workflow, action.",
    "workflows": "Workflows are the reusable *how*: steps, branching and waits.",
    "actors": "Actors are who or what can perform work: agents, humans, code, services.",
    "machines": "Machines are the hosts that execute steps; drain one to stop new placements.",
    "runs": "Runs are executions of a rule's workflow; pause and resume gate the whole engine.",
}


def _noun_entry(noun: str, verbs: list) -> str:
    lines = [f"- `culture-rules {noun} {v.name}` — {v.summary}" for v in verbs]
    return (
        f"# culture-rules {noun}\n\n{_NOUN_BLURBS[noun]}\n\n## Verbs\n\n"
        + "\n".join(lines)
        + "\n\nEvery verb supports `--json`. Writes are dry-run unless `--apply`. The CLI talks "
        "only to the HTTP API (`CULTURE_RULES_API_URL`, default `http://127.0.0.1:8765`; "
        "`CULTURE_RULES_TOKEN` is a bearer token or a `grant:<NAME>` reference).\n\n"
        f"## Usage\n\n    culture-rules {noun} overview\n    culture-rules {noun} list --json\n"
    )


def _verb_entry(v) -> str:
    params = "\n".join(
        f"- `{p.name}` ({p.type}{', required' if p.required else ''}) — {p.help}" for p in v.params
    )
    if v.mutating:
        params += (
            "\n" if params else ""
        ) + "- `apply` (boolean) — commit; the default is a dry-run"
    mode = (
        "Writes: dry-run unless `--apply`; a dry-run changes nothing."
        if v.mutating
        else "Read-only."
    )
    return (
        f"# culture-rules {v.noun} {v.name}\n\n{v.summary}.\n\n{mode} "
        f"Required role: `{v.role}`.\n\n## Parameters\n\n{params or '(none)'}\n\n"
        f"## Usage\n\n    culture-rules {v.noun} {v.name} --json\n"
    )


def _generated() -> dict[tuple[str, ...], str]:
    """One entry per registered noun and verb, read from the command registry."""
    from culture_rules.cli.verbs import REGISTRY  # noqa: PLC0415 - registry imports the CLI

    out: dict[tuple[str, ...], str] = {
        ("serve",): _SERVE,
        ("node",): _NODE,
        ("node", "run"): _NODE_RUN,
        ("mcp",): _MCP,
    }
    for noun in REGISTRY.nouns():
        out[(noun,)] = _noun_entry(noun, REGISTRY.verbs(noun))
        for v in REGISTRY.verbs(noun):
            out[v.path] = _verb_entry(v)
    return out


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

ENTRIES.update(_generated())
