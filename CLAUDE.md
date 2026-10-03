# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`culture-rules` is the **rules engine for the AgentCulture mesh**: rules →
conditions → workflows → actions, carried out by actors (agents, humans,
code, …). It ships as one PyPI distribution, `culture-rules`, whose import
package is `culture_rules`. Its backend is Python, and its visual editor is
a Node.js + React Flow (`@xyflow/react`) app with exactly three tabs:
**Rules | Workflows | Actors**.

**Status: scaffold only.** guildmaster provisioned this repo with
`guild create` from `culture-agent-template`, as a plain rename. What exists
on disk today is the template baseline:

- the agent-first CLI;
- the mesh identity;
- the vendored skill kit;
- CI/publish. Version 0.9.0, published to PyPI by the genesis run.

No rules engine, HTTP API or frontend exists yet. Two GitHub issues drive
the build:

- [#1](https://github.com/agentculture/culture-rules/issues/1) is the
  **build brief**: repo shape, packaging/CI pitfalls, and the neighbours to
  scope.
- [#2](https://github.com/agentculture/culture-rules/issues/2) is the
  **product model + UX**: the mental model and the interaction design the
  implementation must converge on.

Read both before designing anything (`gh issue view 1`; if `gh` errors on
the deprecated Projects-classic field, use
`gh issue view 1 --json title,body,comments`).

**This agent owns the build, and the order is fixed:**
`/scope` → `/think` → `/challenge` (optional) → `/spec-to-plan` → code.
Issue #1 is a brief, not a spec. Its tables are readings to pressure-test, not
decisions.

## Domain model (from #1 and #2, not yet implemented)

Where #2 refines #1, #2 wins. It is the product-model issue.

| Concept | Meaning | Constraints already set |
|---------|---------|-------------------------|
| **Rule** | *When* work should happen. It reads visually as `Trigger → Condition → Workflow → Action`; stages are optional where that is semantically valid. | Rules may chain to a predecessor through a **relationship**, not an extra box in the flow: at least *must run after* and *may run after*. The relationship also defines which upstream outputs are visible. |
| **Condition** | A predicate over the trigger plus variables/context. | **Never `eval()` user Python.** It must be serialisable, so the frontend can edit it graphically and the backend can evaluate it deterministically. JSON-Logic, CEL and a typed AST are the candidates. The common cases are graphical, and the expression form is advanced/secondary. |
| **Workflow** | *How* reusable work is done: steps, branching, waits, and agent/code/human work. | Has **inputs, internal variables, steps, outputs**. It must not know which external event triggered it. This is the graph React Flow edits. DAG vs. loops, versioning and run resume are still open. |
| **Action** | A concrete side effect or terminal operation: comment on a PR, transition Jira, send a message, call a service, run code, ask a human, invoke an actor capability. | Retry, timeout and idempotency semantics are open. |
| **Actor** | *Who/what can perform work*: agent, human, service/daemon, code/runner, robot, … | **Not a stage in the rule chain.** It is a generic Actor model with concrete types and capabilities. Agent actors are reached via the Culture mesh. |

Settled constraints that shape the architecture:

- **Human actors make runs long-running and asynchronous.** The engine needs
  **persisted run state**, not an in-memory call stack. Settle this early.
- **Variables are scoped:** trigger/rule context, workflow inputs,
  workflow-local, workflow outputs, and exported values for downstream rules.
  Prefer **explicit exported outputs** over a global mutable bag. Mappings
  (output → action input / downstream rule input) should be graphical, with
  the textual reference form inspectable but never required.
- **Exactly three primary tabs.** Runs, history, ledger, stats and inbox are
  never top-level navigation. They appear contextually, as details of a rule
  or workflow.
- **UX is visual composition, not an admin dashboard:**
  - large type and large targets, minimal chrome and prose;
  - progressive disclosure: rule creation starts from "When does this
    happen?" and grows through a `+` affordance;
  - smooth overview↔edit transitions;
  - keyboard accessibility and `prefers-reduced-motion` support despite the
    animation.
  - Raw YAML/JSON is an advanced mode, never the default.

Open tension to resolve in `/think`: issue #1 treats actions as workflow
*nodes*, while #2 also puts an Action stage *after* the Workflow in the rule flow. Decide
whether a rule's trailing Action is sugar for a terminal workflow step or a
distinct concept.

## Planned shape (from #1)

- **`culture_rules` is the library**: model plus engine, importable on its
  own.
- **The `culture-rules` CLI is a thin layer over the library**, following
  the agent-first conventions below. Writes are **dry-run by default**,
  `--apply` commits them, and every verb takes `--json`.
- **An HTTP API serves the editor.** The framework is our choice; FastAPI
  is the obvious candidate.
  - **Pin the API contract in one place** (OpenAPI or JSON Schema), so the
    frontend and backend cannot drift.
  - Adding a framework ends the zero-runtime-dependency property. Keep the
    CLI core dependency-free if practical, e.g. by putting the server in an
    optional extra.
- **The frontend lives in one subdirectory** (e.g. `web/`).

Pitfalls the brief calls out:

- **CI is Python-only today.** A frontend needs a Node job (install, build,
  lint/typecheck) in `.github/workflows/tests.yml`. It also needs a decision
  on how the build ships: as static assets inside the wheel, served by the
  Python API, or separately. That choice changes `pyproject.toml`
  (`[tool.hatch.build.targets.wheel]`) and `publish.yml`, whose `paths:`
  filter currently only watches `pyproject.toml` and `culture_rules/**`.
- **The `lint` job runs `markdownlint-cli2 "**/*.md"` over the whole tree.**
  `node_modules/**` is already ignored in `.markdownlint-cli2.yaml`. Add the
  frontend's build-output directory there too.
- **SonarCloud will analyse JS/TS.** `sonar-project.properties` currently
  sets `sonar.sources=culture_rules`. Widen `sonar.sources`, `sonar.tests`
  and the exclusions deliberately, and wire JS coverage if the quality gate
  should count it.
- **`scripts/scan-secrets.py` runs in CI** and rejects non-localhost
  endpoints in committed files. Keep API base URLs configurable and default
  to localhost.
- **Keep `teken cli doctor . --strict` green.**

Neighbours to scope before claiming any boundary or integration. All are
**unverified**, and all are checked out as siblings under `../`:

- `events-cli`: MQTT bus, a possible trigger source;
- `workledger-cli`;
- `agenda`: task tracking;
- `callsmith`: structured calls, a possible actor;
- `protocols-cli`;
- `culture`: the mesh, i.e. how agent actors are reached.

Read their READMEs during `/scope`.

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

## The CLI today

The CLI is cited (cite-don't-import) from teken's `python-cli` reference,
so the runtime package has **no third-party dependencies**. teken is a dev
dependency only. The verbs are `whoami`, `learn`, `explain <path>`,
`overview`, `doctor` and `cli overview`.

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
  marked *planned*, as the domain/shape sections above are.
