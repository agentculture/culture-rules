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
  `runs`, `rollout`, `serve`, `node`, `mcp`), an HTTP API with its contract pinned in
  `api/openapi.json`, and an MCP server exposing the same verbs as tools;
- a Node.js + React Flow editor in `web/` with four tabs: **Workflows |
  Actors | Variables | Statistics**. There is no Rules tab: each rule is
  shown and edited as an entry point of the workflow it starts. A
  workflow opens in a Simple (its description and a plain-language
  paragraph), Detailed (notes, the steps canvas and the entry-point
  editors) or Debug (ports and references) view. Runs and history appear in context, never
  as a tab.

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

The **daily newspaper** (#53, spec
`docs/specs/2026-10-11-daily-newspaper.md`, setup
`docs/operations/newspaper.md`, actor guides in `docs/actors/`) adds `email`
and `search` app surfaces and four action kinds: `email.send` (Resend),
`search.web` (Brave), `llm.complete` (an OpenAI-compatible endpoint, a
locked brief, no tools) and `library.push` (an EPUB to the library its actor describes). It also adds
the step option `config.on_error: "skip"` and the disabled bundle
`docs/rules/newspaper`.

The **workflow phrases** work (#69, spec
`docs/specs/2026-10-17-workflow-connecting-phrases.md`) makes a workflow's
and a step's `description` plain language and adds an optional `notes` for
technical text; `culture-rules workflows migrate-notes [--apply]` moves old
description text into notes, and the committed `docs/rules` workflows are
migrated. `culture-rules workflows describe` (and `GET
/workflows/{id}/describe`) returns the description and a paragraph built
from one phrase table (`culture_rules/model/paragraph.py`, no AI), with the
rule sentences and the Then side. Workflow saves carry a server-owned
`revision` (a stale one gets 409), trust accepts a v1 or v2 workflow digest
(v2 ignores description and notes), and `culture-rules rules
migrate-orphans [--apply]` gives each workflow-less rule its own stepless
workflow.

The **model actors** work (spec
`docs/specs/2026-10-19-model-actors-in-the-cli-and-mcp.md`, setup
`docs/operations/model-actors.md`) adds four verbs, also MCP tools and
Actors-page features. `culture-rules actors enrol-models --gateway MACHINE
--match GLOB` (repeatable; admin) plans, then with `--apply` writes, one
endpoint actor per matched model served by the gateways in the shared variable
`model_gateways` (a list of `"machine=URL"` strings, the allowlist; the grant
is resolved only after it passes). `actors references ID` lists what names an
actor, `actors disable ID [--force]` is refused with `actor_in_use` while an
enabled workflow or rule uses it, and `workflows repoint-actor WF --from A
--to B` moves a workflow (a trusted one needs `--force`). The model rule: one
enabled endpoint actor (one with `params.base_url`) per model (409
`model_taken`, and again at run time in `llm.complete`); mesh agents without
`base_url` are outside it. An older server answers exit 2. The newspaper
bundle summarises on `cortex-spark2`.

**File-in transcription** (#73 part A, spec
`docs/specs/2026-10-22-file-in-transcription.md`, setup
`docs/operations/pocket-transcribe.md`, guide `docs/actors/stt.md`): the action
kind `audio.transcribe` sends a given audio file to an agent actor's
OpenAI-compatible `/v1/audio/transcriptions`, reading only under the actor's
`audio_root` and writing only under its `output_root`; ffmpeg cuts windows of
at most 480 s at silences, and a retry resumes. The result is
`<output_dir>/<audio_sha256>/transcript.<model-slug>.json`, the outputs are
scalars, and the same audio and model again is a no-op. The bundle
`docs/rules/pocket-transcribe` (workflow `transcribe-file`, fired by
`pocketsync.recording.landed`) ships disabled.

**Transcripts on the reader** (spec
`docs/specs/2026-10-23-transcripts-on-the-reader.md`, setup
`docs/operations/transcripts-on-the-reader.md`): the action kind
`transcript.render` builds one deterministic EPUB from a transcript file,
titled `<YYYY-MM-DD> <HHMM> <word>`, a 1-2 line summary then the transcript, right to
left unless the language is `en`. `llm.complete` takes `brief: transcript` and
`content_path` + `content_root` (the transcript text never enters run state),
and `library.push` takes `kind`. The workflow `transcript-to-reader` and the
disabled rule `pocket-to-reader` (after `transcribe-file-he`, or after
`transcribe-file` for an `en` recording) put each finished transcript in the
library; the editor's fold reads an OR of one predecessor per branch as
continuing from either.

**The decider step** (spec `docs/specs/2026-10-24-decider-step-in-workflows.md`,
guide `docs/actors/decider.md`): the action kind `decider.assess` asks the mesh's
decider (`/v1/systemone` on the lobes gateway) for probabilities over a step's
`choice`, `noul` and `score` questions and outputs `answers`, `picks` (the top pick
per question), `host`, `domain` and `usage` for a later step's `when`. The domain is
the actor's; the bundle `docs/rules/decider` holds the `decider` actor. The model rule
holds a decider by model and domain (`clef@general`), so there is one actor per domain.

**The library is configuration** (culture-rules#86, guide `docs/actors/library.md`).
`library.push`'s actor describes its library: `upload_path`, the `kinds` and
`visibilities` it accepts, and an optional `queue_path` (unset, the push only uploads).
Each push step names its `kind`. The bundles push to culture-library
(library.culture.dev): the newspaper as `newspaper`, `public`; transcripts as
`transcript`, `private`.

**Library items** (guide `docs/operations/library-items.md`). `library.push` takes a
`format` and replaces an item by `item_id`; `llm.complete`'s `recording` brief returns
summary, key facts and action items, with an answer cache; `transcript.render` writes
Markdown; the builtins `json.read` and `checklist.merge|set|render|bind` keep a stored
to-do list with stable ids; `audio.transcribe` hashes the audio itself. The flows that
use them are configured live, never committed.

**Discord surfaces, Release 1** (guide `docs/actors/discord.md`). Any number of
Discord bots, one app actor each; the delivery id is per actor and
`connection.guild_id` is one id or a list. `discord.*` triggers filter on
`include_bots`, `channels`, `users` and `roles` (users OR roles); the gateway stores
only channels an enabled rule selects, for 30 days. The action kind `discord.read`
and the builtin `markdown.render` take a message into `library.push`; the bundle
`docs/rules/discord-library` ships disabled. Reactions, commands and the schedule
are planned.

`CLAUDE.md` is written for a Claude Code session working *on* the repo. It
is not your runtime prompt, but it is the fullest write-up of the repo's
conventions if you need more context than fits here. It covers:

- the domain model and the constraints already settled in #1/#2;
- worktree layout and memory discipline;
- `ask-colleague` usage.

If a delegated task touches the domain, hold to these constraints:

- **Exactly four primary tabs: Workflows | Actors | Variables | Statistics.**
  There is no Rules tab; a rule is edited as a workflow's entry point.
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
