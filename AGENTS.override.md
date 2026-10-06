# AGENTS.override.md

This file is the **context layer** for the Pi harness (the `pi` CLI, and the
`associate` non-coding harness modelled on it) when it runs inside this repo.
Pi's CONTEXT loader concatenates `AGENTS.md` or `CLAUDE.md` from its user-level
config directory (see Pi's own docs), each parent directory, and the working
directory — but an `AGENTS.override.md`
present in a directory replaces that directory's `AGENTS.md`/`CLAUDE.md` entry
outright rather than adding to it. That is why this repo ships this file
instead of an `AGENTS.md`: Pi must **not** inherit `CLAUDE.md` (the Claude Code
guidance file) — the two harnesses read the same repository very differently,
and `CLAUDE.md` assumes a coding session with full repo-write authority that
Pi's non-coding lane does not have.

The identity and behavioral bounds for that lane — who Pi is here, what it may
and may not do — live one layer up, in Pi's **system prompt** file,
[`.pi/SYSTEM.md`](.pi/SYSTEM.md). That file replaces Pi's default
coding-assistant system prompt entirely. This file is project *context* only:
what the repo is and how it is laid out, not who is reading it.

## What this project is

`culture-rules` is the **rules engine for the AgentCulture mesh**: rules →
conditions → workflows → actions, carried out by actors (agents, humans,
code, services, robots, …). It has two parts, both on disk:

- a Python backend library, `culture_rules`, with a thin `culture-rules` CLI,
  an HTTP API and an MCP server over it, plus the engine node daemon
  (`culture-rules node run`);
- a Node.js + React Flow visual editor in `web/` with five tabs: **Rules |
  Workflows | Actors | Variables | Statistics**.

**Status: the first mile is shipped on `main` (PR #4); the second
mile (issues #5–#7: typed and scheduled triggers, app actors with GitHub/Jira webhooks and
a Discord listener, real actions (a mesh `message` and a separate
`discord.message`), direct workflow runs, Workflows-tab
editing) is built on `rules/second-mile`, spec
`docs/specs/2026-10-03-culture-rules-second-mile.md`.** guildmaster provisioned the
repo from `culture-agent-template`, and the build followed the devague plan.
The engine, HTTP API (contract pinned in `api/openapi.json`), node daemon,
CLI noun groups (`rules`, `workflows`, `actors`, `machines`, `runs`, plus
`serve`, `node` and `mcp`), MCP server, web editor and ops docs
(`docs/operations/`) exist. `docs/demo.md` is the executed end-to-end
walkthrough. Two GitHub issues carried the design:

- **#1** is the build brief: repo shape, packaging/CI pitfalls, and the
  neighbour repos to scope.
- **#2** is the product model and UX.

The spec is `docs/specs/2026-10-03-culture-rules-engine-editor.md`; the
operator added **Statistics** as the fourth tab, and the design canvas
(<https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi>, row "Chosen") is the
visual source of truth. When asked about the design, quote or summarise
those, and say what is built (the above) versus what the spec still marks
as later work.

The core vocabulary, as #2 defines it:

- A **rule** says *when* work happens: `Trigger → Condition → Workflow →
  Action`; condition and workflow are optional, the action is required.
- A **condition** is a serialisable predicate, never `eval()`.
- A **workflow** is reusable *how*, with inputs, variables, steps and
  outputs.
- An **action** is a concrete side effect.
- An **actor** is *who/what* can do the work. It is not a stage in the
  chain.

Navigation is exactly five tabs: Rules | Workflows | Actors | Variables |
Statistics.
Runs and history appear only in context, never as a tab. Run state is
persisted in MongoDB.

## Four harnesses, four files, no shared base

This repo's root carries one prompt file per harness, each read by exactly
one of them — there is deliberately no shared `AGENTS.md` base for them to
cascade from:

- **Claude Code** reads [`CLAUDE.md`](CLAUDE.md).
- **Pi / associate** reads this file (`AGENTS.override.md`) for context, plus
  [`.pi/SYSTEM.md`](.pi/SYSTEM.md) for its system prompt.
- **colleague** reads [`AGENTS.colleague.md`](AGENTS.colleague.md) (the start
  of colleague's own cascade — see that file).
- **Qwen Code** reads [`QWEN.md`](QWEN.md).

If you are reading this as a human, `CLAUDE.md` is the fullest write-up of the
repo's conventions and is the one to read first; the other three exist to keep
each non-Claude harness from silently inheriting Claude-specific instructions
it cannot act on the same way.

## Identity

Declared in `culture.yaml`:

```yaml
agents:
- suffix: culture-rules
  backend: claude
```

This repo's *mesh* resident runs on `backend: claude`, so `CLAUDE.md` is
the live resident prompt. A Pi session working in a clone of this repo is a
**local tool session**, not the mesh resident — it reads this file and
`.pi/SYSTEM.md` regardless of what `culture.yaml` declares, and running `pi`
here neither requires nor changes that declaration.

(A clone that wants `associate` as its *mesh* resident declares
`backend: colleague` with `model: associate` — see `docs/skill-sources.md`.
That is a per-clone choice; this repo does not ship it.)

## Layout (what you can read/find/summarize here)

```text
culture_rules/   library + agent-first CLI (CLI cited from teken's python-cli)
  cli/                    parser, error/output contract, _commands/ (verbs)
  engine/ model/ actors/  the rules engine, its model and actor adapters
  events/ machines/ ops/  event ingest, machines, backup and logs
  store/ io/ auth/        MongoDB + memory stores, export/import, access
  server/ node/ mcp/      HTTP API, engine node daemon, MCP server
  explain/                markdown catalog for `explain`
web/                      the React Flow editor (built into culture_rules/web_dist)
api/openapi.json          the pinned API contract
tests/                    pytest suite
.claude/skills/           vendored guildmaster skill kit (cite-don't-import)
docs/                     spec, demo.md, operations/, skill-sources.md
culture.yaml              mesh identity (suffix + backend)
.github/workflows/        tests + deploy (PyPI Trusted Publishing)
```

## Conventions worth knowing before you answer a question about this repo

- The vendored skills under `.claude/skills/` are cited **verbatim** from
  guildmaster — never propose editing their scripts; the fix belongs upstream
  (`docs/skill-sources.md` has the re-sync procedure).
- The CLI is the same surface the MCP server exposes; writes are dry-run
  by default and `--apply` commits them.
- Every PR bumps the version (`version-bump` skill); CI's `version-check` job
  blocks merge otherwise.
- This file describes the repo **as it exists on disk today**. If you are
  asked to update it, keep claims grounded in checked-in reality.
