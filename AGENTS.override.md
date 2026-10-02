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
code, services, robots, …). The planned shape has two parts:

- a Python backend library, `culture_rules`, with a thin `culture-rules` CLI
  and an HTTP API over it;
- a Node.js + React Flow visual editor with exactly three tabs: **Rules |
  Workflows | Actors**.

**Status: scaffold only.** guildmaster provisioned the repo from
`culture-agent-template`. Today it holds that template's baseline: the
agent-first CLI, the mesh identity, the vendored skill kit, and CI/publish.
None of the engine, the API or the editor exists yet. Two GitHub issues
carry the design:

- **#1** is the build brief: repo shape, packaging/CI pitfalls, and the
  neighbour repos to scope.
- **#2** is the product model and UX.

When asked about the design, quote or summarise those issues, and make clear
that nothing in them is built yet.

The core vocabulary, as #2 defines it:

- A **rule** says *when* work happens: `Trigger → Condition → Workflow →
  Action`.
- A **condition** is a serialisable predicate, never `eval()`.
- A **workflow** is reusable *how*, with inputs, variables, steps and
  outputs.
- An **action** is a concrete side effect.
- An **actor** is *who/what* can do the work. It is not a stage in the
  chain.

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
culture_rules/   agent-first CLI (cited from teken's python-cli reference)
  cli/                    parser, error/output contract, _commands/ (verbs)
  explain/                markdown catalog for `explain`
tests/                    pytest smoke + introspection tests
.claude/skills/           vendored guildmaster skill kit (cite-don't-import)
docs/skill-sources.md     skill provenance ledger
culture.yaml              mesh identity (suffix + backend)
.github/workflows/        tests + deploy (PyPI Trusted Publishing)
```

Planned, not yet on disk: the engine and models inside `culture_rules/`, an
HTTP API with a pinned OpenAPI/JSON-Schema contract, and a frontend in a
single subdirectory (for example `web/`).

## Conventions worth knowing before you answer a question about this repo

- The vendored skills under `.claude/skills/` are cited **verbatim** from
  guildmaster — never propose editing their scripts; the fix belongs upstream
  (`docs/skill-sources.md` has the re-sync procedure).
- Some CLI self-description strings (`learn`, the `explain` root entry, and
  the argparse description) still use the template wording, "a clonable
  template". This file and `CLAUDE.md` are the current description.
- Every PR bumps the version (`version-bump` skill); CI's `version-check` job
  blocks merge otherwise.
- This file describes the repo **as it exists on disk today**. If you are
  asked to update it, keep claims grounded in checked-in reality.
