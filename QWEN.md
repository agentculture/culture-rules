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
Python, and the visual editor will be a Node.js + React Flow app with
exactly three tabs: **Rules | Workflows | Actors**.

**Status: scaffold only.** guildmaster provisioned the repo from
`culture-agent-template`. What exists today is that template's baseline:

- the agent-first CLI;
- the mesh identity;
- the vendored skill kit;
- CI/publish.

The engine, HTTP API and editor are **planned**. GitHub issue #1 is the
build brief, and #2 is the product model and UX. Read both before
designing anything. The build order is fixed: `/scope` → `/think` →
`/spec-to-plan` → code.

## Domain model (planned, from #1 and #2)

- **Rule** says *when* work happens, and reads as `Trigger → Condition →
  Workflow → Action`; stages are optional where that is valid. A rule may
  follow another rule (*must run after* / *may run after*), and that
  relationship defines which upstream outputs it sees.
- **Condition** is a serialisable predicate over the trigger and context.
  It is **never `eval()` of user Python**; JSON-Logic, CEL and a typed AST
  are the candidates. The common cases are edited graphically.
- **Workflow** is reusable *how*: inputs, internal variables, steps
  (sequence, branch, wait, agent/code/human work) and outputs. It does not
  know what triggered it.
- **Action** is a concrete side effect: comment, ticket transition,
  message, service call, run code, ask a human.
- **Actor** is *who/what* can do the work: agent, human, service/daemon,
  runner, robot, each with capabilities. It is **not** a stage in the rule
  chain.

Further settled constraints:

- **Persisted run state.** Human actors make runs long-running and
  asynchronous.
- **Explicit exported variables.** Prefer them over a global mutable bag.
- **One pinned API contract.** A single OpenAPI or JSON-Schema definition
  is shared by frontend and backend.
- **Navigation is limited to those three tabs.** History, runs and debugging
  appear only in context.

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

The contract for every verb:

- Every command supports `--json`.
- Results go to stdout, and errors and diagnostics to stderr, never mixed.
- Exit codes are `0` success, `1` user error, `2` environment error, `3+`
  reserved.
- Writes in future rule/workflow/actor verbs are dry-run by default, with
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
- **Frontend (planned)** lives in a single subdirectory. It needs its own
  CI job, a markdownlint ignore for its build output, and deliberate
  SonarCloud source settings.

## Layout

```text
culture_rules/   agent-first CLI (cited from teken's python-cli reference)
  cli/                    parser, error/output contract, _commands/ (verbs)
  explain/                markdown catalog for `explain`
tests/                    pytest smoke + introspection tests
.claude/skills/           vendored guildmaster skill kit (cite-don't-import)
docs/skill-sources.md     skill provenance ledger
culture.yaml              mesh identity (suffix + backend)
.github/workflows/        tests + deploy (PyPI Trusted Publishing)
```

This file describes the repository **as it exists on disk today**. When
you edit, keep claims grounded in checked-in reality. If a section drifts
ahead of reality, mark it *planned*, as the domain model above is. For the
full workflow conventions (worktree layout, memory discipline,
`ask-colleague` usage), see [`CLAUDE.md`](CLAUDE.md). Those conventions
apply to work in this repo whichever harness is doing it.
