# Colleague Resident — `culture-rules`

You are a colleague session working in this repo — reading
this file because colleague's prompt cascade resolves it here, not because
`culture.yaml` selected you. That declaration says `backend: claude`, so
`CLAUDE.md` is this repo's *mesh resident* prompt; colleague remains fully
usable interactively over the same clone, and this file is what it loads when
you run it. A clone that declares `backend: colleague` promotes this file to
its resident prompt as well — the guidance below holds either way.

Your job is to assist with scoped tasks delegated by the operator or peer
agents, using the colleague tool-loop (`read_file` / `write_file` /
`edit_file` / `list_dir` / `run_command` / `finish`).

## The prompt cascade (and what this repo actually ships)

colleague concatenates up to three files, in order, as its prompt cascade:

1. `AGENTS.md` — a shared base, if present.
2. `AGENTS.colleague.md` — this file.
3. `AGENTS.colleague.<sanitized-model>.md` — a model-specific override, if
   present.

**This repo ships only layer 2.** There is deliberately no `AGENTS.md` at the
root (a shared base across the four harness files was proposed and rejected —
each harness gets its own, unrelated file; see `CLAUDE.md`'s "Identity and
the four harnesses"), so the cascade for colleague in this repo starts and ends at this
file. There is also no `AGENTS.colleague.<sanitized-model>.md` — this repo
doesn't need per-model overrides today. If you add one of those files later,
update this section so the docs keep matching what's actually on disk.

## What this project is

`culture-rules` is the **rules engine for the AgentCulture mesh**: rules →
conditions → workflows → actions, carried out by actors (agents, humans,
code, services, robots, …). It is built and on disk:

- a Python library, `culture_rules`, holding the model and the engine with
  persisted run state (MongoDB), plus the engine node daemon
  (`culture-rules node run`);
- a thin `culture-rules` CLI (`rules`, `workflows`, `actors`, `machines`,
  `runs`, `serve`, `node`, `mcp`), an HTTP API with its contract pinned in
  `api/openapi.json`, and an MCP server exposing the same verbs as tools;
- a Node.js + React Flow editor in `web/` with four tabs: **Rules |
  Workflows | Actors | Statistics**. Runs and history appear in context,
  never as a tab.

**Status: the first mile is shipped on `main` (PR #4); the second
mile (issues #5–#7: typed and scheduled triggers, app actors with GitHub/Jira webhooks and
a Discord listener, real actions (a mesh `message` and a separate
`discord.message`), direct workflow runs, Workflows-tab
editing) is built on `rules/second-mile`, spec
`docs/specs/2026-10-03-culture-rules-second-mile.md`.** The first-mile spec is
`docs/specs/2026-10-03-culture-rules-engine-editor.md`, the walkthrough is
`docs/demo.md`, and the ops docs are in `docs/operations/`. GitHub issue #1
was the build brief, and #2 the product model and UX; the operator added
Statistics as the fourth tab, and the design canvas
(<https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi>, row "Chosen") is the
visual source of truth. CLI writes are dry-run by default; `--apply`
commits them.

`CLAUDE.md` is written for a Claude Code session working *on* the repo. It
is not your runtime prompt, but it is the fullest write-up of the repo's
conventions if you need more context than fits here. It covers:

- the domain model and the constraints already settled in #1/#2;
- worktree layout and memory discipline;
- `ask-colleague` usage.

If a delegated task touches the domain, hold to these constraints:

- Conditions are never `eval()` of user Python.
- Actors are not a stage in the rule chain.
- Workflows don't know what triggered them.
- Variables flow through explicit exported outputs.

## How you work

- Prefer small, reversible steps; hand off via `finish` when done.
- Follow the operator's instructions and any skills loaded from
  `.colleague/skills/` when present.
- The vendored skills under `.claude/skills/` are cited **verbatim** from
  guildmaster — don't reformat or edit their scripts; a fix belongs upstream
  (see `docs/skill-sources.md` for the re-sync procedure).
- Every PR bumps the version (`version-bump` skill) — CI's `version-check` job
  blocks merge otherwise.
