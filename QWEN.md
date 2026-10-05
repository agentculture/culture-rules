# QWEN.md

This file provides guidance to Qwen Code when working with code in this
repository. Qwen Code's context loader reads exactly `QWEN.md` and
`AGENTS.md` in a directory. This repo deliberately ships only `QWEN.md`;
there is no `AGENTS.md` here, because each harness gets its own file (see
"Prompt files by harness" below). This file is therefore the sole source of
project guidance for a Qwen Code session.

## What this project is

`culture-rules` is the **rules engine for the AgentCulture mesh**: rules →
conditions → workflows → actions, carried out by actors (agents, humans,
code, services, robots, …). It ships as the PyPI distribution
`culture-rules`, whose import package is `culture_rules`. The backend is
Python, and the visual editor in `web/` is a Node.js + React Flow app with
four tabs: **Rules | Workflows | Actors | Statistics**.

**Status: the first mile is shipped on `main` (PR #4); the second
mile (issues #5–#7) is built on `rules/second-mile`.** guildmaster provisioned the
repo from `culture-agent-template`, and the build followed the devague plan.
On disk today:

- the library `culture_rules` (model, engine, actors, events, machines, ops,
  store, io, auth);
- the engine node daemon, `culture-rules node run`;
- the HTTP API (`culture_rules/server`, the `server` extra), whose contract
  is pinned in `api/openapi.json`;
- the CLI noun groups `rules`, `workflows`, `actors`, `machines` and `runs`,
  plus `serve`, `node` and `mcp`;
- the MCP server, `culture-rules mcp` (the `mcp` extra), with the same verbs
  as the CLI;
- the web editor in `web/`, shipped in the wheel as `culture_rules/web_dist`;
- ops docs in `docs/operations/` and the executed walkthrough
  `docs/demo.md`;
- the second mile (spec `docs/specs/2026-10-03-culture-rules-second-mile.md`):
  typed `event`/`schedule`/`probe` triggers, `app` actors for GitHub, Jira
  and Discord (webhooks at `POST /hooks/github` and `POST /hooks/jira`, a
  Discord Gateway listener), real action kinds dispatched through the actor
  in `params.actor` (a mesh `message` is separate from `discord.message`),
  direct workflow runs, and the editor's pickers and
  Workflows-tab editors.

GitHub issue #1 was the build brief, and #2 the product model and UX. The
spec is `docs/specs/2026-10-03-culture-rules-engine-editor.md`; the operator
added **Statistics** as the fourth tab. The design canvas
(<https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi>, row "Chosen") is the
visual source of truth. For new work the order is `/scope` → `/think` →
`/spec-to-plan` → code.

## Domain model

- **Rule** says *when* work happens, and reads as `Trigger → Condition →
  Workflow → Action`; condition and workflow are optional, the action is
  required. A rule may follow another rule (*must run after* / *may run
  after*) or supersede one, and that relationship defines which upstream
  outputs it sees. A rule is placed on a machine, an actor or a capability
  requirement, which decides where its trigger and condition evaluate.
- **Condition** is a typed JSON predicate over the trigger and context,
  evaluated deterministically. It is **never `eval()` of user Python**. The
  common cases are edited graphically; a CEL-style text form is advanced.
- **Workflow** is reusable *how*: inputs, internal variables, steps (logic,
  AI call, code run, actor task, loops), typed ports, per-step placement
  and outputs. It does not know what triggered it.
- **Action** is a concrete side effect: comment, ticket transition,
  message, service call, run code, ask a human.
- **Actor** is *who/what* can do the work: agent, human, service/daemon,
  runner, robot, each with capabilities. It is **not** a stage in the rule
  chain.

Further settled constraints:

- **Persisted run state** in MongoDB. Human actors make runs long-running
  and asynchronous.
- **Explicit exported variables.** Prefer them over a global mutable bag.
- **One pinned API contract**, `api/openapi.json`, shared by frontend and
  backend.
- **Navigation is exactly four tabs: Rules | Workflows | Actors |
  Statistics.** Statistics is per-machine state and work. Runs, history and
  debugging appear only in context, never as a tab.

## Prompt files by harness

This repo's root carries one prompt file per agent harness. Each is read by
exactly one harness, and there is no shared base file to inherit from:

- **Claude Code** → [`CLAUDE.md`](CLAUDE.md), the fullest write-up. Read it
  first if you are new to the repo.
- **Pi / associate** → [`AGENTS.override.md`](AGENTS.override.md) for
  context, plus [`.pi/SYSTEM.md`](.pi/SYSTEM.md) for its system prompt.
- **colleague** → [`AGENTS.colleague.md`](AGENTS.colleague.md).
- **Qwen Code** → this file.

## Identity

Declared in `culture.yaml`:

```yaml
agents:
- suffix: culture-rules
  backend: claude
```

`backend: claude` fixes the *mesh resident* prompt file to `CLAUDE.md`. The
mesh runtime reads that file, not this one. A Qwen Code session working in
this repo is a separate, local tool session: it reads `QWEN.md` whatever
`culture.yaml` declares, and running Qwen Code here neither requires nor
changes that declaration. The declaration and the resident prompt together
satisfy the two invariants `steward doctor` verifies:

- **prompt-file-present**;
- **backend-consistency** (`claude` ↔ `CLAUDE.md`).

## The CLI

The CLI is cited (cite-don't-import) from teken's `python-cli` reference
(`teken cli cite`), so the runtime package has **no third-party
dependencies**. teken (a.k.a. `afi-cli`) is a dev dependency only. The
agent-first verbs:

- `culture-rules whoami` — identity from `culture.yaml`.
- `culture-rules learn` — structured self-teaching prompt.
- `culture-rules explain <path>` — markdown docs for any noun/verb.
- `culture-rules overview` — descriptive snapshot of the agent.
- `culture-rules doctor` — check the agent-identity invariants.
- `culture-rules cli overview` — describe the CLI surface itself.

The engine verbs, over the HTTP API:

- `culture-rules rules|workflows|actors|machines` — `list`, `show`,
  `create`, `update`, `enable`, `disable`, `delete`, `restore`, `purge`
  (plus `export`/`import`, `run`/`replay` and `drain`/`undrain` where they
  apply).
- `culture-rules runs` — `list`, `show`, `cancel`, `pause`, `resume`.
- `culture-rules serve` — the HTTP API (`server` extra).
- `culture-rules node run` — this host's engine node (`--once` for one
  cycle).
- `culture-rules mcp` — the same verbs as MCP tools over stdio (`mcp`
  extra).

The contract for every verb:

- Every command supports `--json`.
- Results go to stdout, and errors and diagnostics to stderr, never mixed.
- Exit codes are `0` success, `1` user error, `2` environment error, `3+`
  reserved.
- Writes (rule/workflow/actor/machine verbs) are dry-run by default, with
  `--apply` to commit them.

CI enforces the agent-first rubric with `teken cli doctor . --strict`.

## Skills

`.claude/skills/` vendors the guildmaster skill kit: 19 skills,
cite-don't-import, including the devague workflow skills. Provenance and
the re-sync procedure live in `docs/skill-sources.md`. Do not reformat or
edit vendored scripts; re-sync from guildmaster instead.

## Conventions

- **Every PR bumps the version**, even docs/config/CI ones. Use the
  `version-bump` skill; the `version-check` CI job blocks merge otherwise.
- **Tests**: `uv run pytest -n auto`. Run a single test with
  `uv run pytest tests/test_cli.py::test_whoami_json`.
- **Lint**: black, isort, flake8 (line length 100), bandit, markdownlint,
  `scripts/scan-secrets.py`.
- **Deploy**: pushing to `main` publishes to PyPI via Trusted Publishing
  (`.github/workflows/publish.yml`); PRs do a TestPyPI dry-run.
- **Frontend** lives in `web/`. CI has a `web` job, markdownlint ignores
  its build output, and SonarCloud covers `web/src`.

## Layout

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
docs/                     spec, demo.md, operations/, harness docs
tests/                    pytest suite
.claude/skills/           vendored guildmaster skill kit (cite-don't-import)
docs/skill-sources.md     skill provenance ledger
culture.yaml              mesh identity (suffix + backend)
.github/workflows/        tests + deploy (PyPI Trusted Publishing)
```

This file describes the repository **as it exists on disk today**. When
you edit, keep claims grounded in checked-in reality. If a section drifts
ahead of reality, mark it *planned*. For the
full workflow conventions (worktree layout, memory discipline,
`ask-colleague` usage), see [`CLAUDE.md`](CLAUDE.md). Those conventions
apply to work in this repo whichever harness is doing it.
