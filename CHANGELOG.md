# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). This project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.13.0] - 2026-10-07

### Added

- PR fixer (plan pr-fixer-rule): four disabled-by-default rules sharing one `pr-fixer` workflow, shipped as importable data in docs/rules/pr-fixer/ with a variable seed script. Settled red checks or a trusted comment or review starts a wait, then an agent on the cultureagent Qwen bridge, a test gate with a diff guard, and a GitHub App push. Review threads are replied to and resolved, and the run link is posted. The rules fire only for repos in vars.fixer_repos.
- Shared variables: a fifth editor tab (Variables), `vars.<name>` in conditions and `$var` inputs, the CLI/API/MCP `variables` noun with list `add`/`remove`, and fail-closed evaluation. Saving, enabling, importing or restoring a variable rule is refused while an online node lacks the capability.
- Engine: `wait` steps (quiet period, head_unchanged guard, superseded runs), `retry_until` with `carry`, built-in code steps (`gate`, `action`, `github.threads`, `github.threads_addressed`), the rule `on_failure` action and `run.id`/`run.error.*` references.
- Per-PR concurrency keys with a shared attempt budget (max_attempts, reset on a human push or green checks, coalescing of deduplicated events). Runs can be cancelled when a rule is disabled: `rules stop-runs` and an editor prompt.
- GitHub: once-per-SHA checks settle (`github.pr.checks_settled`, recovered from stored completions, polled only on a node that can serve the App), PR facts (head/base repo, branch, SHA, draft, author) on every fixer trigger, PR-comment enrichment, and the `github.push` and `github.review_reply` action kinds.
- Bridge actors (cultureagent bridge protocol) with callbacks on the API, plus the test gate run as a separate OS user and the spark2 fixer install scripts and ops recipe (docs/operations/pr-fixer.md).
- Plain descriptions (d19): `rules describe` / `workflows describe` (CLI, MCP, `GET /rules/{id}/describe`, `GET /workflows/{id}/describe`) render a rule as `When / If / Run / On / Then / On failure / Key` lines and a workflow as numbered steps, built only from the config by a fixed vocabulary (`culture_rules/model/describe.py`, no AI). The editor shows them behind an (i) "About" button on every rule and workflow (list rows and the open rule's or workflow's title): a non-modal, keyboard-operable panel with the same lines in mono, and Copy.
- Workflow canvas zoom (d19): pinch or ctrl/cmd + wheel, Zoom out / Zoom in / Fit to width buttons and `+` / `-` / `0` keys, 25%–200%. A plain wheel still scrolls the page, and button zooms do not animate under prefers-reduced-motion.

### Changed

- Replay previews evaluate shared variables like live matching.
- Resumed action steps keep the placement derived from their actor.

### Fixed

- Engine: time queued behind a busy actor no longer eats a step's working time. A `blocked` step (actor at its concurrency cap) gets its full `timeout_s` from the dispatch the actor accepts, not from the first, blocked dispatch (live: the pr-fixer agent accepted at 17:40 timed out at 17:52 on a deadline set at 16:47). Waiting has its own bound, twice `timeout_s` from the first `blocked` (`QUEUE_LIMIT_FACTOR`), after which the step fails `queue_timeout` (replaces `blocked_timeout`, which consumed an attempt and queued again). The bound also holds while a re-poll left the step `pending` on a drained or unavailable node, never once it is `dispatching` (that work may have started).
- Engine: a queued step is re-asked with a backoff (5, 10, 20, 40, then every 60 s) instead of every 5 s from any node, and its run history records the spell once (`dispatched`, `blocked`, then its outcome); repeat polls only move `rev` and update the step's `queue` record (`since`, `deadline`, `polls`, `last_at`, `left_at`), so a long queue cannot grow the run document toward MongoDB's 16 MB cap. A guarded wake whose head lookup is `blocked` backs off the same way, records `wait_blocked` once, and fails `queue_timeout` past the same bound (twice the wait step's `timeout_s`, default 3600 s), checked in housekeeping on any node, drained included, so an expired wait never looks up the head again.

- The node install's default extras include `yaml`, so the test gate can read a repo's `culture.yaml` (the first live gate failed with `extra_missing`).

- The node unit no longer blocks a sudo gate run-as: `deploy/node/install.sh --gate-run-as PREFIX` writes `CULTURE_RULES_GATE_RUN_AS` into node.env (kept across reinstalls) and, for a sudo prefix, `NoNewPrivileges=false`. A node that starts with a sudo prefix under no_new_privs logs an error naming the fix, and the gate refuses `run_as_blocked`.
- The gate no longer hides run-as errors: a failed worktree pack carries a bounded stderr tail, and a probe tells `run_as_failed`/`run_as_blocked` (the prefix itself cannot run) from `source_unavailable` (a missing commit).

## [0.12.1] - 2026-10-07

### Added

- `culture.yaml` declares the PR fixer test gate (`uv sync`, then `uv run pytest -n auto`). The gate is read from a PR's base commit, so it must be on `main` before the fixer can push to culture-rules PRs.

## [0.12.0] - 2026-10-05

### Added

- `discord.message` action ("Post a message on Discord"): actor, server and channel picked from what the bot can see; the channel can be mapped from the trigger to reply in place
- `GET /actors/{id}/discord/targets` and `culture-rules actors discord-targets <id>` (CLI and MCP): the servers and text channels a Discord app actor can post to, with private channels the bot was not added to marked

### Changed

- `message` is "Send a message on the mesh" only and takes no actor in the editor; a stored `message` naming a Discord actor still posts to Discord and is shown as a Discord message
- Action picker mapping fields use the real event data names (`repository`, `key`, `project`, `channel_id`, `content`, ...)
- docs/actors/discord.md: posting a message; the API needs the bot token injected for the channel list

## [0.11.4] - 2026-10-05

### Changed

- Delivery validation for the second mile filed in devague: 13 obligations on c32, c35, c41-c46; evidence e1-e14 (13 pass, 1 fail: one typeless event rule remains after migrate-typeless); behavioral deltas b1-b4 (injected hidden secrets, actor-machine placement, Access bypass as path apps, migrate-typeless disables rather than reaching zero)

## [0.11.3] - 2026-10-05

### Added

- `deploy/node/install.sh --secret NAME` (repeatable) injects grant secrets into the node unit
- `docs/actors/`: one setup guide per app actor (GitHub, Discord, Jira): creating or inviting the app, sealing secrets, injecting them, the actor definition, events, verification and troubleshooting
- Ops doc: injected app secrets, actor pinning, the two path-scoped Access bypass apps, the Jira gateway base

### Fixed

- App secrets work when sealed `--hidden`: a `grant:NAME` reference resolves first to `CULTURE_RULES_SECRET_<NAME>`, which the service unit injects with `grant run --inject` (as it already does for the Mongo URI); `grant get` refuses hidden secrets, so GitHub, Discord and Jira app actors could not authenticate
- A rule action through an actor that lives on a machine runs on that machine (where its secrets are injected), and only that machine's node holds the actor's Discord gateway lease
- `deploy/node/install.sh` keeps the installed MongoDB CA on an upgrade without `--ca-file` (it used to drop the line from node.env)
- CLI: a server-side dry-run (the migrations) no longer prints `would None None`

## [0.11.2] - 2026-10-04

### Fixed

- Node: one malformed rule document (or a stored `cron: null`) no longer stops the schedule, probe, firing or chain stage for every other rule on the host; it is logged and skipped
- Events: a non-JSON or legacy fan-in cursor no longer wedges ingest when events are waiting, and an idle reset is persisted so the warning is logged once
- GitHub App actions: a revoked installation token is re-exchanged once on 401; repo shape and App ids are checked before the private key is read; a deleted or disabled actor drops its cached key
- Message action: a Discord or mesh timeout is an unknown outcome and is not retried (a retry could post twice); a single-string `connection.channels` is one channel
- Editor: guided messages for the trigger/action validation codes, `not_implemented`, `replay_invalid` and `not_json`

## [0.11.1] - 2026-10-04

### Changed

- Ops doc: the migrations (rules migrate-typeless, runs backfill-ids) are merged and part of the rollout; the Jira webhook token is sealed as RULES_JIRA_WEBHOOK_TOKEN

## [0.11.0] - 2026-10-04

### Added

- Typed triggers (#5): event triggers require params.type (trigger_type_required); schedule triggers (stdlib 5-field cron, UTC or an IANA tz, DST-safe, one run per slot on the placed host, no backfill); probe triggers run an allow-listed runner command on a cron and fire on change or on a condition over the output
- App actors (kind app) for GitHub, Jira and Discord: declared events, probes and actions, connection secrets as grant:NAME references only
- Webhook receivers POST /hooks/github (X-Hub-Signature-256) and POST /hooks/jira (HMAC or URL token, issues refetched by key), exempt from Access/auth by exact path only, deduped on delivery id, answering before any rule runs; query strings are stripped from access logs
- Discord Gateway listener held by one node mesh-wide under a named lease (`discord-gateway:<actor>`), writing discord.message.created events; discord extra
- Action kinds message (Discord or mesh), github.comment (as a GitHub App; github extra), jira.comment, http.call (destination allowlist, private/tailnet ranges refused, pinned address, no redirects) and machine.command (runner CodeRunner with typed args), registered as node action ports
- Rule actions dispatch through the actor named in params.actor with that actor's limits; an unknown or disabled actor fails the run with actor_unavailable
- Self-authored events (our App, bot or service account) do not fire rules unless the trigger sets include_self; per-rule fire-rate cap trigger.params.max_fires_per_hour (default 60, schedule triggers uncapped) records rate_capped skips
- Direct workflow runs: POST /workflows/{id}/run and `workflows run` (CLI and MCP) with typed inputs validated against the declared ports, pinning a synthetic `adhoc:<workflow>` rule
- A human actor is created on first Access sign-in (id from the email, or linked to an existing human with the same params.email)
- `actors enrol-agents`: enrols the mesh agents listed in the Culture server manifest (server.yaml in the per-user Culture directory) as agent actors for this machine, disabling (never deleting) ones no longer listed
- `rules migrate-typeless` and `runs backfill-ids` (admin, dry-run by default)
- /health reports webhook delivery outcomes per actor and the Discord gateway holder and state
- Editor: typed trigger picker and action picker with mapping chips; Actors tab app connection, declarations and runner command editors; Workflows tab run form with typed inputs and outputs in place, full step properties, selectable in/out nodes with inputs, outputs, variables and description editors, deleted-workflow view with admin purge, guided errors with fix options
- deploy/node/install.sh (dry-run by default, offline wheelhouse) and ops docs for the cache rule, hook Bypass paths, GitHub App, Discord bot, Jira webhook, kill switches and the four-node upgrade order
- Optional extras github (cryptography) and discord (discord.py); every module imports with them absent

### Changed

- Run docs carry top-level rule_id and workflow_id; rule history and run lists filter in the store
- Heartbeat online threshold derives from the beat cadence (offline_after); a missing heartbeat counts as offline, an unreadable one as online
- WorkflowRef.inputs accepts {"$ref"} and {"$literal"} forms
- The chain feed skips its shared transaction when no rule depends on the finished rule; a legacy events cursor starts fresh; event subscription depth is configurable (CULTURE_RULES_EVENTS_DEPTH)
- Switches are disabled while a toggle is in flight

### Fixed

- An event trigger with no type no longer matches every event
- A predecessor decision written final in one step on another host now cascades to its dependants
- Tall workflows no longer overflow the canvas; the workflow list dot resolves actor placement to its machine

## [0.10.5] - 2026-10-03

### Added

- Action params accept explicit {"$ref": path} and {"$literal": value} forms; a $ref that can never resolve is refused at save (422 invalid_reference)
- Each node cycle redelivers human ask answers that were recorded but not delivered (e.g. a crash in between); `node run --once --json` reports a `redelivered` count
- docs/operations/pause.md: what a pause holds back and what it drops

### Changed

- Colleague, mesh and runner actors no longer claim cross-process idempotency: an attempt whose outcome is unknown (crash, resume, lost ack, timeout) now fails with unsafe_retry instead of running again; declare the step idempotent to retry automatically
- Adding, changing or removing a runner actor's params.commands is admin-only on create, update and import (403 runner_commands_admin_only)
- Registered runner commands that evaluate inline code (sh -c, python -c, node -e, perl -e, ... also behind env/sudo wrappers) are refused at run time
- `matches` refuses catastrophic-backtracking patterns at save (422) and treats a stored one as a recorded non-match; its input cap drops from 10,000 to 2,000 characters
- `!=` on a missing field is now false like every other comparison; write !(a == b) to match when the field is absent
- A retry after a failed attempt counts against the actor's concurrency cap and token budget
- Plain strings resolve as references only when their path fits a namespace (trigger envelope fields or event keys, `workflow.outputs.*`, `rules.<id>.outputs.*`); others, like `rules.yaml` or `trigger.sh`, stay literal

### Fixed

- A step's claim lease is renewed while its actor runs, and a lapsed claim is taken over only when the holder's heartbeat is stale or the step's deadline passed, so a long step no longer runs twice
- Engine nodes beat from their own thread, so a long step no longer makes its host look offline
- Answering a human ask frees the human actor's concurrency slot at once (it stayed held until the step deadline)
- Actor adapters no longer cache failed results, so retry policies re-run the work
- A predecessor settling during an engine pause no longer turns a waiting must/may-run-after dependant into a final skip; it is re-evaluated on resume
- Literal strings such as trigger.sh in workflow step config are no longer reported as trigger references; {"$ref": "trigger..."} in a workflow is
- The inline-code guard for runner commands strips interpreter version suffixes without a regex, so a hostile 10k-character argument is classified in linear time

## [0.10.4] - 2026-10-03

### Added

- Workflows tab: a left-pane list of every workflow (New workflow, machine dot, name, enable switch), aligned with the Rules list
- Rules tab: the create button reads "New rule" (it opens the "When does this happen?" form), like "New workflow"

### Changed

- Cleared every SonarCloud maintainability issue on the PR (508) SonarCloud maintainability issues: split composite test assertions and pytest.raises blocks, read-only React props, native fieldset/output/dialog elements instead of ARIA roles, and 33 cognitive-complexity splits in culture_rules and web with no behaviour change
- `Node` takes its probe/load-reader/engine-version/beat-interval settings as one `HeartbeatOptions` (14 -> 11 parameters)
- The workflow import reads files with `Blob#text` only (the FileReader fallback was dead code for every supported browser); the logo SVG is decorative and named by visually hidden text

### Fixed

- The multi-host chaos test no longer times out when the killed host never wins an action race (harness holds the survivors per run advance until the victim dies); exactly-once and no-loss held throughout
- A WorkflowsCreate test that raced the router on slower CI runners

## [0.10.3] - 2026-10-03

### Added

- Workflows tab: New workflow, in the head next to Import and as the empty state's primary action. It asks only for a name (Enter creates, Escape cancels), creates the workflow with POST /workflows (no steps; an id held by a live or soft-deleted workflow moves on to -2, -3, ...) and opens it on the canvas with the step + focused. API errors show inline in the form.

### Fixed

- Editor parity for workflows (spec: every workflow operation reachable from the editor, the CLI and the MCP server): the Workflows tab had no create, rename, enable/disable or delete. It now renames the workflow (a draft edit written by Save), enables and disables it (POST /workflows/{id}/enable|disable), and deletes it softly with Undo (DELETE, then POST /workflows/{id}/restore); deleting a workflow a rule still uses keeps it and names the conflict.

## [0.10.2] - 2026-10-03

### Fixed

- Every API response now carries Cache-Control: no-store (API reads, 401/403 envelopes, unknown API paths, the event stream); the HTML shell is private, no-cache and content-hashed /assets are private, immutable. Cloudflare was serving a 15-minute-old /api/machines from its edge cache, so newly enrolled machines were missing from Statistics, and a shared cache could hand one principal's answer to another.

## [0.10.1] - 2026-10-03

### Fixed

- `culture-rules node run` no longer dies at startup against the real events-cli: events-cli rejects the raw MQTT filter `#` and has no catch-all pattern, so the node now registers one durable subscription per event-type depth (`*`, `*.*`, ... up to 4 segments) and drains them as one source. Any events-cli setup failure (rejected subscription, unreachable broker) now degrades the node to running without ingest instead of crashing it, and broker drains use a positive timeout (events-cli rejects 0).
- `/health` reports the node named by the new `CULTURE_RULES_NODE_NAME` (or `serve --node-name`) instead of `socket.gethostname()`; `node run` defaults `--host` to the same variable before the short hostname.
- `culture-rules mcp` without the `mcp` extra exits 2 with the install hint instead of 1 (anyio/mcp.server.stdio imports now raise `ServerExtraMissing`).
- `culture-rules serve` with a missing or unreachable store exits 2 naming `CULTURE_RULES_MONGO_URI` and `pip install 'culture-rules[store]'` instead of 1; under `--json` stderr holds only the JSON error.
- The web Statistics `#agent-state` test asserts `status` reaches `ready` instead of an always-true check.

## [0.10.0] - 2026-10-03

### Added

- Rules engine library `culture_rules`: typed model + JSON Schemas (rule, workflow, action, actor, machine, placement), a dependency-free condition evaluator, rule matching (all-fire, exclusive groups, supersede, must/may-after with explicit exports), placement by machine, actor or requirement, exactly-once claims, a persisted run executor with pinned versions, retries, bounded loops and pause/drain/cancel, append-only audit and soft delete/restore/purge.
- Storage port with an in-memory adapter and a MongoDB replica-set adapter (majority writes, change streams, TLS/auth, typed transient errors); deploy artifacts and runbook for a spark/thor/orin replica set with spark2 non-voting.
- Actors: agent (colleague work --json, correlation-matched mesh tasks), code runner (registered argv commands, admin-only sandboxed inline scripts), human asks (id-bearing event, exactly-once answer), per-actor budgets and concurrency caps, grant: secret references.
- Engine node daemon `culture-rules node run`: heartbeat with platform probe, events ingest, placed and shared trigger consumers, rule chaining re-evaluation, executor loop, run summaries.
- HTTP API under the optional `server` extra with a committed `api/openapi.json`, Cloudflare Access JWT on a loopback listener plus service tokens on the LAN listener, viewer/editor/admin roles, SSE live updates, replay, rule history, repo-backed export/import.
- CLI noun groups (rules, workflows, actors, machines, runs) over the API from one command registry, dry-run by default with --apply; `serve`, `node` and `mcp` verbs; MCP server under the optional `mcp` extra.
- Web editor (Vite + React + @xyflow/react) with four tabs — Rules, Workflows, Actors, Statistics — following the chosen design canvas; shipped inside the wheel and served by the API.
- Encrypted, versioned S3 backups with a restore drill (`backup` extra); cloudflared/rules.culture.dev runbook; observability (health, JSON logs, run-id propagation).
- Delivery artifacts: spec, plan, split plan and delivery summary under docs/; multi-host chaos tests; surface parity test; CI web job (typecheck, vitest, palette validation, Playwright, webglass) and a 60% coverage floor.

### Changed

- README, CLAUDE.md and the harness prompt files describe the shipped four-tab design and the CLI/MCP/API/node surface instead of a scaffold.

## [0.9.1] - 2026-10-02

### Changed

- `CLAUDE.md` expanded from the bootstrap seed into the full runtime prompt via `/init`, grounded in build brief #1 and product-model/UX issue #2: domain model (rule/condition/workflow/action/actor), settled constraints, planned shape, build pitfalls, exact CI commands, CLI contract, harness editing rules, worktree and memory conventions.
- `README.md` rewritten to describe the rules engine and its three-tab editor (planned) instead of the template; skill count corrected (19, not 11); template-only "Make it your own" section removed.
- `QWEN.md`, `AGENTS.override.md` and `AGENTS.colleague.md` now describe culture-rules (status: scaffold) rather than "a clonable template", and no longer point at the removed "Cloning this template" section of `CLAUDE.md`.

## [0.9.0] - 2026-09-06

### Added

- **Four agent harnesses, each reading exactly one root file.** Claude
  Code→`CLAUDE.md`; Pi/`associate`→`AGENTS.override.md` (context) plus
  `.pi/SYSTEM.md` (system prompt, which *replaces* Pi's default);
  colleague→`AGENTS.colleague.md`; Qwen Code→`QWEN.md`. Deliberately **no**
  `AGENTS.md` — `AGENTS.override.md` is what stops Pi inheriting `CLAUDE.md`.
- One skill tree, four loaders: `.qwen/skills`, `.colleague/skills` and
  `.pi/skills` are relative symlinks onto `.claude/skills`. No forked scripts,
  no duplicated docs.
- `docs/harness-selection.md` (the two selections), plus
  `docs/automation-contract.md` and `docs/harness-invocations.yaml` (the four
  forced invocations as a machine-readable contract), and
  `docs/harness-verification.md` (the instrumented run).
- `scripts/harness-smoke.py` — a per-harness CI check that fails when **any one**
  of the four configs is broken, so three-quarters of a clone cannot rot
  unnoticed. A skipped check is reported as not-verified, never as a pass.
- `scripts/scan-secrets.py` — CI gate against committed credentials and
  non-localhost endpoints, with planted-secret tests proving it catches.

### Changed

- `culture.yaml` declares `backend: claude`. Because no code path rewrites that
  key, the template's declaration is what every clone inherits — the previous
  `colleague` value is why ~30 siblings carry a backend disagreeing with their
  seeded prompt file.
- `backend-fingerprints.yaml` re-synced from steward: list-valued prompts, so
  `acp` accepts `QWEN.md` and `colleague` accepts `AGENTS.override.md` and
  `.pi/SYSTEM.md`.

### Fixed

- `CLAUDE.md`, `README.md`, `QWEN.md`, `AGENTS.override.md`,
  `AGENTS.colleague.md` and `docs/skill-sources.md` had claimed this repo was a
  colleague resident, contradicting `culture.yaml`. Every harness prompt now
  names `backend: claude` / `CLAUDE.md` as the mesh resident, while stating
  that its own harness stays interactively available regardless.
- `.pi/settings.json`'s `skills` key is **inert** — that key belongs to a
  `package.json` manifest, not `settings.json`. Syscall instrumentation on a
  fresh clone: settings alone opened **0** `SKILL.md`; the `.pi/skills` symlink
  opened **19**. Replaced with the symlink, settings file removed.
- `doctor` no longer accepts an interactive harness's prompt file as the mesh
  resident's. Four harnesses ride three backend names, so `AGENTS.override.md`,
  `.pi/SYSTEM.md` and `QWEN.md` are *recognized* under `colleague`/`acp` — but
  the Culture daemon reads exactly one file per backend, and a clone carrying
  only Pi's files under `backend: colleague` used to report healthy with no
  resident prompt at all. `prompt_file_present` now requires the resident
  prompt; the others are reported by a new `harness_prompts` info check.
- `scan-secrets.py` closes three evasions: values containing `@`/`:`/`%`/`=`/`?`
  are now matched in full instead of stopping at a base64-ish alphabet (a
  quoted JSON key is matched too, which it never was); the placeholder
  exemption is a whole-value judgement, so a high-entropy literal merely
  *containing* `fake`/`example` is still reported; and endpoint hosts are
  parsed with `urlsplit`, so a bracketed IPv6 authority such as
  `http://[2001:db8::1]:8080` can no longer slip past the localhost allowlist.
- `harness-smoke.py`: a live probe is satisfied by **stdout only** (stderr
  noise mentioning `yes` or a prompt filename no longer counts as an answer,
  and a yes/no probe must answer exactly `yes`); a nonzero exit from `steward
  doctor` or `guild create` can no longer reach a passing branch on
  success-shaped JSON; failure/skip/waiver diagnostics go to stderr, leaving
  stdout to results; and an unknown `--stage` exits 1 (user error) rather than
  argparse's 2 (reserved for environment failures).

Known gaps carried into this release (not a changelog category — recorded
here so the release is not read as claiming more than it delivers): colleague
loads **0 of 19** skills from the nested tree, blocked on two upstream defects
filed with a reproduction as
[`agentculture/colleague#494`](https://github.com/agentculture/colleague/issues/494)
(the template ships the correct shape regardless); and retrofitting
already-provisioned siblings is an explicit **non-goal**, so
`culture-rules#25` stays open for the existing fleet.

## [0.8.0] - 2026-09-05

### Added

- **`validate-delivery` skill** (origin `devague`, re-broadcast by
  `guildmaster`) — the validation leg between `/assign-to-workforce` and
  `/summarize-delivery`: run the confirmed plan's behavioral tests agent-side,
  then file obligations, evidence, and behavioral deltas. Record-only; the
  `devague` CLI never runs a test.
- **`scripts/` wrappers for the five prompt-only workflow skills** — `scope`,
  `challenge`, `deviate`, `validate-delivery`, `summarize-delivery`. Each
  forwards its arguments to the `devague` CLI verbatim, so upstream owns the
  surface. Every clone now ships a complete, convention-clean skill directory
  instead of inheriting the script-less shape.

### Changed

- **Re-synced all eight `devague`-origin workflow skills** — `scope`, `think`,
  `challenge`, `spec-to-plan`, `assign-to-workforce`, `deviate`,
  `validate-delivery`, `summarize-delivery` — from devague `0.24.1`
  (`ec15362`), matching guildmaster's canonical copies. Notable upstream
  content: `/scope` fans out to read-only exploration subagents at 5+ candidate
  surfaces, and `assign-to-workforce split-plan --write` persists a durable
  gate-2 record.
- **All eight `SKILL.md` files are now byte-verbatim with upstream.** devague
  ships `type: command` on all eight itself, so nothing is added to the
  frontmatter here. The only divergence is the five wrapper scripts, which are
  additions, not edits.
- **`docs/skill-sources.md` updated for the re-sync** — count corrected from
  seven skills to eight (`validate-delivery` had no row), all eight rows
  repointed to `../guildmaster/.claude/skills/`, and pins refreshed to devague
  `0.24.1`.
- **The 2026-07-15 "vendor directly from devague" divergence is superseded.**
  That decision existed to keep guildmaster's `scripts/*.sh` wrappers out of
  this repo. The wrappers are now wanted: without them a clone ships a
  `SKILL.md` with no sibling `scripts/` and fails a
  `test_skills_convention`-style gate (guildmaster#95). The old section is
  retained, marked superseded, with its stale re-sync recipe replaced — that
  recipe would have deleted the wrappers this release adds and skipped
  `validate-delivery` entirely.

### Fixed

- **`scope.sh` usage advertised an invalid claim kind** — `--kind non-goal`
  (hyphen) is rejected by `devague capture`, which accepts `non_goal`. Anyone
  copying the wrapper's usage line got `invalid choice: 'non-goal'`. Corrected
  in both this repo and guildmaster.

## [0.7.0] - 2026-08-24

### Added

- **`resume <task-id|last> [--detach]` verb** in `ask-colleague` — pick a cut / timed-out / SIGTERM'd run back up from its persisted artifact, continuing on the original `colleague/<id>` work branch.
- **Per-seat thinking effort** in `ask-colleague` — `--effort` (acting seat), `--seat-effort S=R` (any seat), `--role NAME` (colleague#416). Rule of thumb: `--effort off` for small well-specified briefs, default for ordinary work, `xhigh` for open-ended judgement.
- **Review diff front-loading** — `ask-colleague review` embeds a filtered, bounded diff directly in the prompt instead of relying on the colleague run to fetch it.

### Changed

- **`ask-colleague` re-vendored byte-verbatim from `agentculture/colleague` @ 1.63.0** (cite-don't-import) — all five files (`SKILL.md`, `scripts/ask-colleague.sh`, `prompts/{explore,review,write}.md`). Every repo scaffolded from this template (`guild create` instantiates it) shipped the Qwen3.6-era wrapper until now.
- **Default colleague model is `unsloth/Qwen3.8-27B-NVFP4`** (was the Qwen3.6 pin). The lobes gateway on `:8001` no longer serves 3.6, so the previous default only worked via colleague's auto-refresh warning path.
- **`docs/skill-sources.md` ledger row** for `ask-colleague` updated to the 1.63.0 sync (was `2026-06-12 (colleague 1.7.0, direct)`) and its verb list extended with `plan` / `resume` / the pilot verbs.

## [0.6.1] - 2026-07-20

### Added

- **Worktree location convention** in `CLAUDE.md` — every worktree you create
  by hand (workforce fan-out lanes, scratch checkouts) lives in
  `../.worktrees.culture-rules/<name>/`, one
  repo-named directory beside the checkout, replacing a shared `../worktrees/`
  folder. This workspace holds many sibling projects, so a generic shared
  folder accumulates orphaned trees from several repos at once with nothing
  indicating ownership — a stale-tree sweep can't tell a live lane from junk.
  Matches the convention already documented in sibling repo `reachy-mini-cli`.
  Adds branch-prefix guidance (scope the prefix to the work; plain `agent/*`
  collides with leftovers from earlier fan-outs and fails `git worktree add
  -b`), and notes that the vendored `assign-to-workforce` skill uses both the
  shared path *and* `agent/<task-id>` branches in its fan-out example — it is
  cited verbatim and must not be edited, so both are overridden when following
  it. Teardown guidance names `git worktree remove <path>` as the verb that
  actually deletes a worktree; `git worktree prune` only clears metadata for
  directories that are already gone. Tool-managed throwaways are explicitly
  out of scope: `ask-colleague`'s read-only verbs create a detached worktree
  under `${TMPDIR:-/tmp}` and reap it on an EXIT trap, so they never persist
  to need an owner.

## [0.6.0] - 2026-07-18

### Added

- **Four devague-origin skills re-vendored into `.claude/skills/`**
  (cite-don't-import), synced to the fixed devague source
  (devague#74/#75/#76):
  - `challenge` — a risk-scaled blind-spot discovery pass that runs between
    `/think` and `/spec-to-plan`, routing findings back through the existing
    deterministic moves as human-adjudicated proposals.
  - `scope` — the idea→scope leg that surveys the surfaces an idea touches
    before framing, seeding the Announcement Frame with provenance-backed
    boundary/non-goal/assumption claims.
  - `deviate` — stops an in-flight `assign-to-workforce` run when execution
    must diverge from the confirmed plan and records the divergence as a
    first-class, append-only deviation record.
  - `summarize-delivery` — closes the loop after an `assign-to-workforce`
    run with a planned-vs-actual accountability artifact.

  These four originate in `devague` and are re-broadcast via guildmaster; see
  `docs/skill-sources.md` for provenance.

## [0.5.0] - 2026-06-24

### Added

- **Memory-discipline "Conventions and workflow" section in `CLAUDE.md`** — a
  per-task *recall-before / remember-after* convention (scope localized to this
  repo's nick) so the vendored `remember` / `recall` skills are actually used,
  not just present: `/recall` before non-trivial work to build on prior
  decisions instead of re-deriving them, and `/remember` when a non-obvious
  decision, constraint, fix-and-why, or hard-won gotcha surfaces. The section
  documents this repo's memory as **in-repo and public** — records resolve to
  `<repo-root>/.eidetic/memory` (committed, team- and mesh-shared). Inserted
  idempotently (skipped if already present), slotted under an existing
  "Conventions and workflow" heading when one exists, else appended.

### Changed

- **Refreshed the `remember` + `recall` wrappers from eidetic-cli 0.10.0**
  (cite-don't-import) — picks up eidetic's **project-local store default**: the
  files backend now resolves per record by visibility — PUBLIC records inside a
  git repo go to `<repo-root>/.eidetic/memory` (committed, team-shared), PRIVATE
  records (or any record outside a repo) go to `$HOME/.eidetic/memory` (never
  committed), an explicit `EIDETIC_DATA_DIR` still wins, and recall reads both
  stores and merges. Also carries the 0.9.3 hardening (interactive-stdin guard,
  `help` as a search term, SIGPIPE-safe suffix parsing). **Recipe policy
  override (the wrappers here are NOT byte-verbatim):** the injected default
  visibility is flipped from eidetic's `private` to **`public`**, so a plain
  `/remember` lands the note in `./.eidetic/memory` in this repo, kept as part
  of the repo — pass `--visibility private` to route a record to `$HOME`
  instead. `remember` drives `eidetic remember` (idempotent upsert of one JSON
  record or an NDJSON batch on stdin); `recall` drives `eidetic recall` with
  four search modes (exact / approximate / keyword / hybrid). Each `SKILL.md` is
  localized only in the illustrative `--scope <nick>` examples (Provenance keeps
  "First-party to eidetic-cli"). Runtime dep: the `eidetic` CLI on PATH (else a
  local eidetic-cli checkout with `uv`) — **`eidetic >= 0.10.0`** for the
  in-repo routing; on an older CLI the public records still work but are stored
  in `$HOME/.eidetic/memory` instead of in-repo. Propagated by rollout-cli's
  `eidetic-memory` recipe.

## [0.4.0] - 2026-06-23

### Added

- **Vendored the `remember` + `recall` memory skills from eidetic-cli**
  (cite-don't-import) — the write/read halves of eidetic's shared
  `$HOME/.eidetic/memory` surface, so this agent (Claude and its colleague
  backend) can persist facts across sessions and recall them later, sharing
  one store.
  `remember` drives `eidetic remember` (idempotent upsert of one JSON record or
  an NDJSON batch on stdin, dedup by id + content hash); `recall` drives
  `eidetic recall` with four search modes — exact / approximate / keyword /
  hybrid — each hit carrying text, full provenance metadata, a relevance score,
  and a freshness signal. The `.sh` wrappers are byte-verbatim from eidetic-cli
  (their first-party origin); each `SKILL.md` is localized only in the
  illustrative `--scope <nick>` examples (Provenance keeps "First-party to
  eidetic-cli"). Both default to this agent's PRIVATE scope, reading the suffix
  from `culture.yaml`. Runtime dep: the `eidetic` CLI on PATH (else a local
  eidetic-cli checkout with `uv`). Propagated by rollout-cli's `eidetic-memory`
  recipe.

## [0.3.4] - 2026-06-20

### Fixed

- Identity docs and self-description strings still claimed `backend: claude`
  (prompt file `CLAUDE.md`), but this template was promoted to a colleague
  resident in #14/#15: `culture.yaml` declares `backend: colleague` (Qwen) with
  `AGENTS.colleague.md` as the resident prompt. Corrected the stale claim in
  `CLAUDE.md` (Identity section), `README.md`, `docs/skill-sources.md`, and the
  two CLI description strings (`overview` artifacts and `explain doctor`). The
  `doctor` backend→prompt-file mapping and the tests were already on
  `colleague`; this aligns the prose and self-description with them.

## [0.3.3] - 2026-06-20

### Fixed

- pyproject.toml: correct the `license` field and PyPI classifier from MIT to
  Apache-2.0 to match the `LICENSE` file. The README License section was already
  corrected in 0.3.2, but the package metadata was missed; the built wheel now
  reports `License-Expression: Apache-2.0`.

## [0.3.2] - 2026-06-18

### Added

- ask-colleague skill: `monitor`/`guide`/`stop` pilot verbs plus a `--watch`
  flag to dispatch, watch the live feed of, send mid-flight guidance to, and
  cooperatively stop a running colleague flight (re-vendored from colleague).

### Changed

- README: correct the License section from MIT to Apache 2.0 to match the
  `LICENSE` file.

## [0.3.1] - 2026-06-13

### Changed

- CLAUDE.md: add a convention to reach for the `ask-colleague` skill reflexively
  for explore/review/write/grade — read-only `review`/`explore` are always safe;
  side-effecting `write` needs the user's go-ahead.

## [0.3.0] - 2026-06-13

### Added

- AGENTS.colleague.md resident prompt file (backend colleague <-> AGENTS.colleague.md)

### Changed

- Promote agent identity to a colleague resident: culture.yaml backend
  claude -> colleague with a pinned model. The `doctor` backend-consistency
  map gains `colleague` -> AGENTS.colleague.md.

## [0.2.1] - 2026-06-12

### Changed

- **Re-vendored the `ask-colleague` skill from colleague (now 1.7.0, up from the
  0.39.2 sync)** — the wrapper had drifted multiple releases behind origin. Picks
  up the `clean` verb (reap stale/corrupt `colleague/*` branches + orphaned
  `.colleague/` artifacts a crashed run left behind), the `--json` flag on every
  verb (result JSON on stdout, diagnostics/digest on stderr), the
  `_colleague_via_uv` local-dev resolution that honors `--repo`, and the
  tri-state (0/1/2) exit-code contract. `scripts/ask-colleague.sh` + `prompts/`
  are byte-identical to the origin; `SKILL.md` diverges only in the one
  consumer-identifying Provenance clause (`culture-rules vendors from
  guildmaster`). `docs/skill-sources.md` sync row updated to
  `2026-06-12 (colleague 1.7.0, direct)`. Refs: colleague#183, #186.

## [0.2.0] - 2026-06-06

### Added

- **`ask-colleague` skill** (`.claude/skills/ask-colleague/`) — the first-party front door to the `colleague` CLI (the renamed `convertible`). On top of `explore` / `review` / `write` it adds a `feedback` verb (grade a finished work item — the ROI loop), and `write` now **previews by default** in a throwaway worktree (no side effects) unless `--apply` / `--pr` is given. Reach for it reflexively — `review` for a diverse second opinion on a committed diff before opening a PR, `explore` for a fresh read of an unfamiliar area.

### Changed

- **Replaced the `outsource` skill with `ask-colleague`.** `outsource` was renamed to `ask-colleague` upstream ([colleague#148](https://github.com/agentculture/colleague/pull/148)). Because guildmaster has not re-broadcast the rename yet (its kit still ships the old `outsource`), `ask-colleague` is vendored **directly from the sibling `colleague` checkout** rather than from guildmaster — a tracked local divergence recorded in `docs/skill-sources.md`, parallel to the `agex` → `devex` one. Vendored verbatim except one consumer-identifying clause in the Provenance paragraph.
- **Ledger + CLAUDE.md + `.gitignore`:** point `docs/skill-sources.md` and the CLAUDE.md Skills section at `colleague` / `ask-colleague`, swap the *optional* runtime prerequisite `convertible` → `colleague` (env prefix `CONVERTIBLE_*` → `COLLEAGUE_*`, with the legacy names kept as a deprecated fallback), and gitignore the `.colleague/` run-artifact dir the skill writes (plus the stale `.agex/`).

## [0.1.4] - 2026-05-31

### Added

- **Vendor the `outsource` skill** (`.claude/skills/outsource/`) from
  guildmaster's canonical copy (origin
  [`agentculture/convertible`](https://github.com/agentculture/convertible),
  re-broadcast via guildmaster — guildmaster
  [#51](https://github.com/agentculture/guildmaster/pull/51)). Every agent
  cloned from this template now inherits the ability to hand a scoped task to a
  *different* engine/mind: `explore` (read-only investigation), `review` (a
  diverse second opinion on the committed diff), and `write` (delegate a small
  implementation). `explore`/`review` run isolated in a throwaway `git worktree`;
  `write` refuses a dirty tree. Fulfils
  [#8](https://github.com/agentculture/culture-rules/issues/8).
- **Ledger + CLAUDE.md:** record `outsource` in `docs/skill-sources.md`
  (origin = convertible, re-broadcast via guildmaster; vendored verbatim — it
  already carries `type: command`) and document its *optional* runtime
  dependency on the `convertible` CLI (the skill exits with an install hint if
  absent, so a clone that never uses it is unaffected).

### Changed

### Fixed

## [0.1.3] - 2026-05-31

### Changed

- Expanded the clone-and-rename instructions in `CLAUDE.md`: added `README.md` to
  the rename targets and a portable `git grep` discovery command so a cloner can
  find every occurrence of the template name (hard-coded in ~100 places across the
  package, including the CLI command files and `_ISSUES_URL` in
  `culture_rules/cli/__init__.py`) rather than renaming by hand.
- Synced `README.md`'s "Make it your own" checklist with `CLAUDE.md`: it now lists
  `README.md` itself as a rename target and points to `CLAUDE.md`'s discovery
  command as the authoritative procedure, so the two onboarding checklists no
  longer drift.

## [0.1.2] - 2026-05-30

### Changed

- Renamed the PR-lifecycle CLI references `agex` / `agex-cli` to `devex` (same
  tool, new name) across `CLAUDE.md`, `docs/skill-sources.md`, `.gitignore`, and
  the vendored `cicd`, `assign-to-workforce`, and `communicate` skills — the
  `cicd` scripts now invoke `devex pr`.
- Logged the vendored-skill in-place patch as a local divergence in
  `docs/skill-sources.md`; the matching canonical rename is tracked upstream for
  guildmaster in
  [agentculture/guildmaster#48](https://github.com/agentculture/guildmaster/issues/48)
  so a future re-sync reconciles cleanly.
- Aligned the documented `devex` version floor to `>=0.21` across the vendored
  `cicd` `SKILL.md` and `workflow.sh` install hint (were `>=0.1`), matching
  `docs/skill-sources.md` and the `await`-era feature set; flagged upstream on
  guildmaster#48.

### Fixed

- SonarCloud now reports code coverage — added `relative_files = true` to
  `[tool.coverage.run]` so `coverage.xml` emits repo-relative paths that map to
  `sonar.sources=culture_rules` (absolute / `.venv` paths were dropped
  as unmappable). Mirrors the sibling `convertible` setup.

## [0.1.1] - 2026-05-26

### Changed

- **CI gates on the SonarCloud quality gate**
  ([issue #3](https://github.com/agentculture/culture-rules/issues/3)) —
  added `sonar.qualitygate.wait=true` to `sonar-project.properties` so a failing
  gate fails the `test` job when `SONAR_TOKEN` is set. Token-less repos and fork
  PRs remain green (the scan step is guarded by `if: env.SONAR_TOKEN != ''`).

## [0.1.0] - 2026-05-26

### Added

- **Onboarded into the AgentCulture mesh** ([issue #1](https://github.com/agentculture/culture-rules/issues/1)).
- **Agent-first CLI** cited from teken's (`afi-cli`) `python-cli` reference
  (`teken cli cite`) — verbs `whoami`, `learn`, `explain`, `overview`, `doctor`,
  and the `cli` noun group. Runtime is self-contained (`dependencies = []`);
  `teken>=0.8` is a dev dependency only. Passes the seven-bundle agent-first
  rubric (`teken cli doctor . --strict`). `doctor` checks the agent-identity
  invariants (prompt-file-present, backend-consistency, skills-present).
- **Mesh identity**: `culture.yaml` (`suffix: culture-rules`,
  `backend: claude`) and the matching `CLAUDE.md` prompt file.
- **Canonical guildmaster skill kit** (11 skills) vendored under
  `.claude/skills/` (cite-don't-import): `agent-config`, `assign-to-workforce`,
  `cicd`, `communicate`, `doc-test-alignment`, `pypi-maintainer`, `run-tests`,
  `sonarclaude`, `spec-to-plan`, `think`, `version-bump`. Every `SKILL.md`
  carries `type: command` (load-bearing for the culture/claude backend);
  `cicd` / `communicate` consumer-identifying prose adapted, all script bodies
  verbatim. Provenance in `docs/skill-sources.md`. Three skills (`think`,
  `spec-to-plan`, `assign-to-workforce`) originate in `devague`, re-broadcast
  via guildmaster.
- **Build + deploy baseline**: `pyproject.toml` (hatchling), `tests/` (pytest,
  xdist, coverage), `.github/workflows/{tests,publish}.yml` (CI rubric/lint gate,
  PyPI Trusted Publishing), `.flake8`, `.markdownlint-cli2.yaml`,
  `sonar-project.properties`, and `.claude/skills.local.yaml.example`.

### Changed

### Fixed
