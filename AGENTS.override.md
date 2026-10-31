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
- a Node.js + React Flow visual editor in `web/` with four tabs:
  **Workflows | Actors | Variables | Statistics**. There is no Rules tab:
  each rule is shown and edited as an entry point of the workflow it
  starts. A workflow opens in a Simple (its description and a
  plain-language paragraph), Detailed (notes, the steps canvas and the
  entry-point editors) or Debug (ports and references) view.

**Status: the first mile is shipped on `main` (PR #4); the second
mile (issues #5–#7: typed and scheduled triggers, app actors with GitHub/Jira webhooks and
a Discord listener, real actions (a mesh `message` and a separate
`discord.message`), direct workflow runs, Workflows-tab
editing) is built on `rules/second-mile`, spec
`docs/specs/2026-10-03-culture-rules-second-mile.md`.** guildmaster provisioned the
repo from `culture-agent-template`, and the build followed the devague plan.
The engine, HTTP API (contract pinned in `api/openapi.json`), node daemon,
CLI noun groups (`rules`, `workflows`, `actors`, `machines`, `runs`, `rollout`, plus
`serve`, `node` and `mcp`), MCP server, web editor and ops docs
(`docs/operations/`) exist. `docs/demo.md` is the executed end-to-end
walkthrough.

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

**Discord surfaces, Releases 1-3** (guide `docs/actors/discord.md`). Any number of
Discord bots, one app actor each; the delivery id is per actor and
`connection.guild_id` is one id or a list. `discord.*` triggers filter on
`include_bots`, `channels`, `users` and `roles` (users OR roles); the gateway stores
only channels an enabled rule selects, for 30 days. The action kind `discord.read`
and the builtin `markdown.render` take a message into `library.push`; the bundle
`docs/rules/discord-library` ships disabled. Release 2 adds the event
`discord.reaction.added` with an `emoji` filter, the action kind `discord.react`,
`user_roles` on events, and the builtin `attachment.fetch`, which saves pdf/epub/Markdown
attachments as files on the node for `library.push`'s `content_path` (d7).
Release 3 adds application commands, configured per actor in `params.commands`
(d12; none built in, a rule picks one with the `command` filter; the interaction
token kept apart, answered privately by `discord.reply`), and `discord.sweep`
with the generic `cursor.advance`; a builtin's `config.with` reads
`inputs.item.*` in a loop (d8).

**State between runs** (guide `docs/operations/keysets.md`). Generic named key sets:
`keyset.filter` drops the items whose normalised key a set already holds, `keyset.add`
remembers items' keys for `window_days` (insert-if-absent, `max_keys` cap); the set name,
key field, normalisation and window are step config. One document per key in `keysets`,
under a TTL index; admin verbs `keysets list|show|clear`. Flows using them are configured
live.

**Green PR merge prompt** (spec `docs/specs/2026-10-31-green-pr-merge-prompt.md`, guide
`docs/operations/green-pr-merge.md`). There is one merge kind, fenced by an operator click
and a one-time button code: `github.merge` squash-merges at a required `sha` (optional
`delete_branch`), and refuses before any network call an actor that is not an `app`
(`actor_not_app`), a run that is not its rule firing on a `discord.button.clicked` event
(`not_started_by_click`), and a click whose stored `data.code` is missing
(`code_not_found`), bound to another button, repo, number or sha (`code_mismatch`) or used by
another run (`code_used`); the code is used up by compare-and-set (deviation d3). No
workflow is pinned for it: the prompt and merge workflows are configuration. `github.pr`
reads one PR's facts.
`discord.message` takes `buttons`, the gateway emits `discord.button.clicked`, the trigger
filter `custom_id` is a prefix match, and `discord.reply` answers a click privately. A
button's optional `value` is kept in the store (`discord_buttons`, posted as
`<custom_id>:<token>`, with `posted_by` and, with `code: true`, a one-time code) and carried
by its click as `data.value`, `data.posted_by` and `data.code`, so no id is parsed. The
prompt and merge rules are configured live, never committed.

**Repo documents** (#124, guide `docs/operations/repodocs.md`). The action kind
`repodocs.sync` pushes every top-level repo's specs, plans, deliveries, designs and
`.devague` files under its service actor's `repos_root` to culture-library: each file's
default-branch version at the head sha, its working draft when the disk differs, tombstones
for vanished files and spec links. A state file keeps unchanged cycles silent; outputs are
counts only. The bundle `docs/rules/repodocs` runs it every 15 minutes on spark (disabled).

Two GitHub issues carried the design:

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

Exactly four primary tabs: Workflows | Actors | Variables | Statistics.
There is no Rules tab; rules keep their stored shape in the engine, API, CLI
and MCP, and the editor shows each as a workflow's entry point.
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
