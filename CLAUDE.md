# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`culture-rules` is the **rules engine for the AgentCulture mesh**: rules →
conditions → workflows → actions, carried out by actors (agents, humans,
code, …). It ships as one PyPI distribution, `culture-rules`, whose import
package is `culture_rules`. Its backend is Python, and its visual editor is
a Node.js + React Flow (`@xyflow/react`) app in `web/` with five tabs:
**Rules | Workflows | Actors | Variables | Statistics**.

**Status: the first mile is built and shipped** (PR #4, merged to `main`: the
engine, API, node, CLI, MCP server, editor and ops docs exist on disk), and
the **second mile** (issues #5–#7) is built on `rules/second-mile`. guildmaster provisioned the
repo from `culture-agent-template`; the build then followed the devague
plan. What exists today:

- **`culture_rules`** is the library: `model`, `engine`, `actors`, `events`,
  `machines`, `ops`, `store`, `io`, plus `auth` and the `client`.
- **The engine node daemon** (`culture-rules node run`, `culture_rules/node`,
  approved deviation d3): heartbeat and probe, event ingest, placed and
  unplaced triggers, the executor loop, actor adapters with limits, and the
  run reporter. It talks to the store directly.
- **The HTTP API** (`culture_rules/server`, the `server` extra). The
  contract is pinned in `api/openapi.json`. Auth is Cloudflare Access on a
  loopback listener, service tokens on a LAN listener, and roles.
- **The CLI** over the API: the noun groups `rules`, `workflows`, `actors`,
  `machines` and `runs`, plus `serve`, `node` and `mcp`, alongside the
  identity verbs. Writes are dry-run by default, `--apply` commits.
- **The MCP server** (`culture-rules mcp`, the `mcp` extra) serves the same
  verbs as tools. A parity test keeps CLI and MCP identical.
- **The web editor** in `web/`, built into the wheel as
  `culture_rules/web_dist` and served by the API. CI has a `web` job.
- **Ops docs** in `docs/operations/` (replica set, backup, rules.culture.dev)
  and the executed walkthrough in `docs/demo.md`.
- **Second mile** (spec `docs/specs/2026-10-03-culture-rules-second-mile.md`):
  - typed triggers: `event` needs `params.type`; `schedule` (stdlib cron,
    UTC or an IANA tz); `probe` (a runner command on a cron, fires on
    change or a condition);
  - `app` actors for GitHub, Jira and Discord with webhook receivers at
    exactly `POST /hooks/github` and `POST /hooks/jira`, plus a Discord
    Gateway listener held by one node under a named lease;
  - action kinds `message` (on the mesh), `discord.message` (server and
    channel picked from what the bot can see,
    `GET /actors/{id}/discord/targets`), `github.comment` (as a GitHub App),
    `jira.comment`, `http.call` (destination allowlist) and
    `machine.command`, dispatched through the actor in `params.actor`;
  - direct workflow runs (`workflows run`), a human actor on first sign-in,
    `actors enrol-agents`, `rules migrate-typeless`, `runs backfill-ids`;
  - the editor's trigger and action pickers, Actors app and command
    editors, and the Workflows run form, step panel and in/out editors.

Approved deviations from the plan: **d1** `Rule.placement` (where a rule's
trigger and condition evaluate), **d2** the `grant` tool replaces shushu for
secret references, **d3** the engine node daemon. The second-mile plan has
its own deviations d1–d5 (in `.devague/plans/culture-rules-second-mile.json`).
Persisted state lives in a
MongoDB replica set; only `MemoryStore` (tests) is in-process.

Two GitHub issues drove the build:

- [#1](https://github.com/agentculture/culture-rules/issues/1) is the
  **build brief**: repo shape, packaging/CI pitfalls, and the neighbours to
  scope.
- [#2](https://github.com/agentculture/culture-rules/issues/2) is the
  **product model + UX**: the mental model and the interaction design the
  implementation converges on.

The spec is `docs/specs/2026-10-03-culture-rules-engine-editor.md`. Where
the spec and #2 differ, the spec wins: the operator added **Statistics** as
a fourth tab. The design canvas
(<https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi>, row "Chosen") is the
visual source of truth for the editor. For new work the order is still
`/scope` → `/think` → `/challenge` (optional) → `/spec-to-plan` → code. To
read the issues use `gh issue view 1`; if `gh` errors on the deprecated
Projects-classic field, use `gh issue view 1 --json title,body,comments`.

## Domain model

Where #2 refines #1, #2 wins. It is the product-model issue.

| Concept | Meaning | Constraints already set |
|---------|---------|-------------------------|
| **Rule** | *When* work should happen. It reads visually as `Trigger → Condition → Workflow → Action`; condition and workflow are optional, the action is required. | Rules may chain to a predecessor through a **relationship**, not an extra box in the flow: *must run after* and *may run after*, and a rule may supersede another. The relationship also defines which upstream outputs are visible. A rule is placed on a machine, an actor or a capability requirement (d1). |
| **Condition** | A predicate over the trigger plus variables/context. | **Never `eval()` user Python.** It is a typed JSON predicate tree, evaluated by a dependency-free evaluator, so the frontend edits it graphically and the backend evaluates it deterministically. A CEL-style text form is advanced/secondary. |
| **Workflow** | *How* reusable work is done: steps, branching, waits, and agent/code/human work. | Has **inputs, internal variables, steps, outputs**. It must not know which external event triggered it. Each step has typed ports and its own placement. This is the graph React Flow edits. |
| **Action** | A concrete side effect or terminal operation: comment on a PR, send a message, call a service, run code, ask a human. Distinct from a workflow step. | Retry, timeout and idempotency are explicit fields. |
| **Actor** | *Who/what can perform work*: agent, human, service/daemon, code/runner, robot, … | **Not a stage in the rule chain.** It is a generic Actor model with concrete types, capabilities, limits and secret references. Agent actors are reached via the Culture mesh. |

Settled constraints that shape the architecture:

- **Human actors make runs long-running and asynchronous.** Run state is
  **persisted** (MongoDB), not an in-memory call stack.
- **Variables are scoped:** trigger/rule context, workflow inputs,
  workflow-local, workflow outputs, and exported values for downstream rules.
  Prefer **explicit exported outputs** over a global mutable bag. Mappings
  (output → action input / downstream rule input) are graphical, with
  the textual reference form inspectable but never required.
- **Exactly five primary tabs: Rules | Workflows | Actors | Variables |
  Statistics.** Variables are the shared values rules read as `vars.<name>`.
  Statistics is per-machine state and work (one lane per enrolled machine).
  Runs, history, ledger and inbox are never top-level navigation. They
  appear contextually, as details of a rule or workflow. Raw YAML/JSON is an
  advanced mode, never the default.
- **UX is visual composition, not an admin dashboard:**
  - large type and large targets, minimal chrome and prose;
  - progressive disclosure: rule creation starts from "When does this
    happen?" and grows through a `+` affordance;
  - smooth overview↔edit transitions;
  - keyboard accessibility and `prefers-reduced-motion` support despite the
    animation.

## Shape and packaging

- **`culture_rules` is the library**: model plus engine, importable on its
  own, with **zero third-party runtime dependencies**. Everything third-party
  sits behind an optional extra and is imported lazily: `server`, `store`,
  `mcp`, `events`, `yaml`, `backup`, `agent`.
- **The `culture-rules` CLI is a thin layer** over the HTTP API (except
  `node run`, which is the engine and uses the store directly). Writes are
  **dry-run by default**, `--apply` commits them, and every verb takes
  `--json`.
- **The API contract is pinned in one place**, `api/openapi.json`. A test
  (`tests/server/test_openapi_contract.py`) diffs the served schema against
  it, and the web client's types compile against it.
- **The frontend lives in `web/`.** The wheel ships the build as
  `culture_rules/web_dist` (`hatch_build.py`), so serving it needs no Node.

CI notes:

- **The `web` job** in `.github/workflows/tests.yml` runs install,
  typecheck, vitest, Playwright and the webglass agent-state gate.
  `publish.yml` watches `web/**` and builds the bundle before `uv build`.
- **The `lint` job runs `markdownlint-cli2 "**/*.md"` over the whole tree.**
  Build output and `node_modules` are ignored in `.markdownlint-cli2.yaml`.
- **SonarCloud analyses JS/TS** (`sonar.sources` covers `web/src`).
- **`scripts/scan-secrets.py` runs in CI** and rejects non-localhost
  endpoints in committed files. Keep API base URLs configurable and default
  to localhost.
- **Keep `teken cli doctor . --strict` green.**

Neighbours (checked out as siblings under `../`):

- `events-cli`: the MQTT bus, the external trigger source (the `events`
  extra);
- `culture`: the mesh, i.e. how agent actors are reached;
- `callsmith`: structured calls; no callsmith actor type ships;
- `workledger-cli`, `agenda`, `protocols-cli`: scoped, not integrated.

## Commands

```bash
uv sync                                    # install (dev group included)
uv run pytest -n auto                      # full suite (xdist)
uv run pytest tests/test_cli.py::test_whoami_json -v   # a single test
uv run pytest -n auto --cov=culture_rules --cov-report=term   # coverage as CI runs it (fail_under=60)

# Lint, exactly as the CI `lint` job runs it:
uv run black --check culture_rules tests
uv run isort --check-only culture_rules tests
uv run flake8 culture_rules tests
uv run bandit -c pyproject.toml -r culture_rules
markdownlint-cli2 "**/*.md" "#node_modules" "#.local" "#.claude/skills" "#.teken"
python3 scripts/scan-secrets.py
uv run teken cli doctor . --strict         # agent-first rubric gate

uv run python scripts/harness-smoke.py --stage config   # per-harness config check (CI job harness-smoke)
uv run culture-rules doctor                # identity invariants
```

Black, isort and flake8 all use line length 100. Python is `>=3.12`.

## The CLI

The CLI is cited (cite-don't-import) from teken's `python-cli` reference,
so the runtime package has **no third-party dependencies**. teken is a dev
dependency only. The verbs are the identity verbs (`whoami`, `learn`,
`explain <path>`, `overview`, `doctor`, `cli overview`), the noun groups over
the API (`rules`, `workflows`, `actors`, `machines`, `runs`), and `serve`,
`node run` and `mcp`. Every CLI verb has an MCP tool (`culture_rules/mcp`).

The contract for every new verb:

- **Output:** each verb supports `--json`. Results go to stdout and
  errors/diagnostics to stderr, never mixed.
- **Exit codes:** `0` ok, `1` user error, `2` environment error, `3+`
  reserved.
- **Failures:** handlers raise `CliError` (`culture_rules/cli/_errors.py`)
  rather than printing. `_dispatch` in `culture_rules/cli/__init__.py` wraps
  any other exception so no traceback leaks. Argparse errors also route
  through the structured format, honouring a `--json` anywhere in argv.
- **Registration:** a new noun group is a module under
  `culture_rules/cli/_commands/` with a `register(sub)` function, wired into
  `_build_parser()`.
- **Documentation:** every verb needs an entry in
  `culture_rules/explain/catalog.py`, and a mention in `learn`. The teken
  rubric gate checks this coverage.

The CLI self-description strings (argparse description, `learn`, the `explain`
root entry) use culture-rules wording; keep them in step with this file when
verbs land.

## Identity and the four harnesses

`culture.yaml` declares `suffix: culture-rules`, `backend: claude`. That
makes **this file the mesh-resident prompt**. `doctor` and `steward doctor`
check **prompt-file-present** and **backend-consistency** (`claude` ↔
`CLAUDE.md`).

The root also carries one prompt file per interactive harness. Each file is
read by exactly one harness, and there is **deliberately no `AGENTS.md`**:

| Harness | File |
|---------|------|
| Claude Code | `CLAUDE.md` |
| Pi / associate | `AGENTS.override.md` (context) + `.pi/SYSTEM.md` (system prompt) |
| colleague | `AGENTS.colleague.md` |
| Qwen Code | `QWEN.md` |

Rules for editing these files:

- **Keep all four in step** when the project's description changes. They
  must not drift from this file.
- **Some phrases are load-bearing.** The live probes in
  `docs/harness-invocations.yaml` depend on them:
  - `CLAUDE.md` and `QWEN.md` must describe the project as `culture-rules`.
  - The lobes-cli `associate` attribution must stay in `.pi/SYSTEM.md` and
    **only** there. It must never appear in `AGENTS.override.md`.
- A bare `AGENTS.md` fails the smoke check. It would shadow the Pi/colleague
  cascades.
- `.qwen/skills`, `.pi/skills` and `.colleague/skills` are relative symlinks
  onto `.claude/skills`. There is one skill tree.

`docs/harness-selection.md` explains why the interactive harness and the
mesh resident are two separate selections. `docs/automation-contract.md`
covers force-selecting a harness without touching tracked files.

## Skills

`.claude/skills/` vendors 19 skills cite-don't-import, mostly from
guildmaster. The devague workflow skills (`scope`, `think`, `challenge`,
`spec-to-plan`, `assign-to-workforce`, `deviate`, `validate-delivery`,
`summarize-delivery`) originate in devague. `ask-colleague` comes straight
from colleague. **Never edit vendored skill files.** Fixes go upstream, then
re-sync per `docs/skill-sources.md`.

PATH prerequisites:

- `devex` for `cicd`;
- `agtag` for `communicate`;
- `devague` for the workflow skills;
- `colleague` (optional) for `ask-colleague`.

## Conventions

- **Every PR bumps the version**, even docs-only ones. Use the
  `version-bump` skill, which updates `pyproject.toml` and `CHANGELOG.md`.
  CI's `version-check` blocks merge otherwise. Pushing to `main` publishes
  to PyPI through Trusted Publishing, and same-repo PRs publish a
  `.devN` build to TestPyPI.
- **PRs go through the `cicd` skill** (`devex pr` plus SonarCloud gating).
  Its scripts sign posts as `- culture-rules (Claude)` automatically, so
  don't sign bodies by hand there.
- **Reach for `ask-colleague` reflexively.** Run `review` before opening a
  PR on a non-trivial diff, and `explore` for a fresh read of an unfamiliar
  area. Both are read-only, in a throwaway worktree. `write --apply` /
  `--pr` needs the user's go-ahead. Treat the output as a second opinion to
  verify, not as authority.
- **Persistent worktrees live in `../.worktrees.culture-rules/<name>/`**,
  with branch prefixes scoped to the work (e.g. `rules/t3`, not
  `agent/t3`):
  - This overrides the shared `../worktrees/` path and the `agent/<id>`
    branches in the vendored `assign-to-workforce` example.
  - `ask-colleague`'s self-deleting `${TMPDIR}` worktrees are exempt.
  - Remove a worktree with `git worktree remove`, never `rm -rf`.
- **Memory: `/recall` before non-trivial work, `/remember` when something
  non-obvious surfaces.** The wrapper scripts default to
  `--scope culture-rules --visibility public`, which lands records in the
  committed, in-repo `.eidetic/memory/`. Pass `--visibility private` to keep
  a record in `$HOME/.eidetic/memory`.
  - The vendored SKILL.md descriptions still say "private, home-dir". The
    scripts are authoritative (see `remember.sh`'s POLICY OVERRIDE).
  - Don't store what the repo already records.
- **Docs describe what is on disk today.** Anything ahead of reality is
  marked *planned*.
