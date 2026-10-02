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
code, services, robots, …). The planned shape:

- a Python library, `culture_rules`, holding the model and the engine with
  persisted run state;
- a thin `culture-rules` CLI and an HTTP API over that library;
- a Node.js + React Flow editor with exactly three tabs: **Rules |
  Workflows | Actors**.

**Status: scaffold only.** Today the repo holds the
`culture-agent-template` baseline: the agent-first CLI, the mesh identity,
the skill kit, and CI/publish. The engine, API and editor are planned.
GitHub issue #1 is the build brief, and #2 is the product model and UX.

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
