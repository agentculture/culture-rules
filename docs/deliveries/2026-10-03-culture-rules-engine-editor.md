# Delivery Summary — culture-rules engine + editor

plan: `culture-rules-engine-editor` · run: `complete` · date: `2026-10-03`
baseline: `devague summary skeleton`

## Intent

> culture-rules implements issues #1 and #2: a Python rules → condition → workflow → action engine with persisted runs on a MongoDB replica set, placement of rules and workflow steps by machine, actor or requirement across enrolled machines (spark, thor, spark2), a generic Actor registry (agents with db- or repo-based config, harness, model), controlled from a CLI, an MCP server and a graph-first React Flow editor with four tabs (Rules | Workflows | Actors | Statistics), served always-on from several hosts at rules.culture.dev behind cultureflare SSO

After: From rules.culture.dev (SSO) the operator builds a rule as Trigger → Condition → (Workflow) → Action by direct manipulation, places it and each step by machine, actor or requirement, toggles it on, and watches runs light up the graph and the Statistics lanes; agents do the same over CLI/MCP; stopping any one of spark, spark2 or thor does not stop the service

This run executed the converged plan (40 tasks, 8 waves) on the integration branch `rules/build`
through `/assign-to-workforce`, with four approved deviations, then `/validate-delivery`.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Domain model + JSON schemas: Rule, Workflow, Action, Actor, Machine, Placement
- `t2` — Condition predicate tree + deterministic evaluator + text view
- `t3` — Storage port, in-memory adapter and `schema_version` guard
- `t4` — Actor configuration from a DB record or a repo's culture.yaml
- `t5` — Replace template self-description with culture-rules wording
- `t6` — MongoDB replica-set adapter (majority writes, change streams, auth, TLS)
- `t7` — Placement resolver: machine, actor or requirement to one concrete host
- `t8` — Machine enrolment, heartbeats and platform probe
- `t9` — Rule matching: all-fire, exclusive groups, supersede, must/may-after, exports
- `t10` — Exactly-once claims and idempotency keys across hosts
- `t11` — Export/import of rules and workflows, with an optional git repo target
- `t12` — Run executor: persisted runs, pinned versions, timeouts/retries, loops, pause/drain/cancel
- `t13` — Event fabric: Mongo events collection, events-cli ingest, change-stream triggers
- `t14` — Append-only audit log and soft delete / restore / purge
- `t15` — Agent actor adapter: colleague work --json and mesh tasks with correlation
- `t16` — Code runner actor: registered commands, admin-only inline scripts
- `t17` — Human asks: id-bearing ask event and answer action
- `t18` — Per-actor budgets and concurrency caps
- `t19` — Secret references resolved at run time via shushu
- `t20` — Replay a rule against recorded events without side effects
- `t21` — Post run summaries to a mesh channel (optional events)
- `t22` — Health endpoints, structured logs, run-id propagation
- `t23` — HTTP API (optional \[server\] extra): OpenAPI contract, stateless active-active, SSE
- `t24` — Cloudflare Access JWT, two listeners, service tokens, viewer/editor/admin roles
- `t25` — CLI noun groups over the API: rules, workflows, actors, machines, runs
- `t26` — MCP server from the same registry (optional \[mcp\] extra)
- `t27` — Surface parity test across CLI, MCP and OpenAPI
- `t28` — Boundary guard tests: no culture-nodes, workledger, agenda, protocols, callsmith or eidetic coupling
- `t29` — web/ scaffold: Vite + React + @xyflow/react, tokens, four-tab shell, identity, a11y baseline
- `t30` — Rules tab (direction C): rule list + focused vertical rule
- `t31` — Workflows tab (direction B): node editor, typed ports, placement, import/export, run overlay
- `t32` — Actors tab (direction C): large-type roster with inline expand
- `t33` — Statistics tab (direction A): machine lanes
- `t34` — Ship the web build in the wheel; publish paths; ignore/sonar/markdownlint config
- `t35` — CI: web job, webglass, Playwright, palette validation, all four gates
- `t36` — Replica-set deployment across spark, thor and an always-up third voter
- `t37` — Provision rules.culture.dev with cultureflare on every serving host
- `t38` — Encrypted, versioned S3 backups with a restore drill
- `t39` — Multi-host integration and chaos tests
- `t40` — README, demo script, prompts and issue follow-ups

## Actual Delivery

All 40 plan tasks merged into `rules/build` through the TDD-gated merge (tests before and after,
lint, bandit, scan-secrets, teken doctor). Three are `partial`: their merged code meets the
task's own acceptance tests, but a stated piece of the task is not usable end to end yet.

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `model/*` dataclasses + committed `schemas/*.schema.json`; Rule.placement added by d1 — merge `0288883` |
| `t2` | delivered | model/condition.py typed predicate tree, pure evaluator, text view; and/or need 2+ args after review — merge `ace2c40` |
| `t3` | delivered | store/port.py StoragePort contract, MemoryStore, versioning, migrations, contract suite — merge `6c2ed76` |
| `t4` | delivered | actors/config.py: actor config from a DB record or a repo's culture.yaml — merge `509e80c` |
| `t5` | delivered | CLI self-description reworded for culture-rules — merge `0d21ffd` |
| `t6` | delivered | store/mongo.py replica-set adapter (majority writes, change streams, TLS/auth) + docker rig — merge `412126a` |
| `t7` | delivered | engine/placement.py resolver (machine \| actor \| requirement, tailnet address policy) — merge `97398bb` |
| `t8` | delivered | machines/enrol, heartbeat (10 s, 3 missed = offline), probe — merge `811a206` |
| `t9` | delivered | engine/matching.py + ruleset.py (all-fire, exclusive groups, supersede, must/may-after, exports); unknown_rule + unrunnable_relationship added later — merge `8dce601` |
| `t10` | delivered | engine/claims.py exactly-once claims, idempotency keys — merge `8009eab` |
| `t11` | delivered | io/bundle, codec, exchange, gitrepo (export/import, optional repo target) — merge `e9a58a4` |
| `t12` | delivered | engine/actorport.py + runs.py Executor (persisted runs, pinned versions, retries, loops, containment) — merge `fefc340` |
| `t13` | delivered | events/ ingest, triggers, emit, events_cli adapter (events-cli itself not installed here: adapter surface tests skip) — merge `e48184c` |
| `t14` | delivered | engine/audit.py append-only log + lifecycle soft delete/restore/purge — merge `ed27de3` |
| `t15` | partial | actors/agent.py colleague work --json one-shot + correlation-matched mesh path; real agentirc client is async with no sync bridge yet — merge `8282bb0` |
| `t16` | delivered | actors/code.py registered argv commands, admin-only sandboxed inline scripts — merge `1402d82` |
| `t17` | delivered | actors/human.py asks: one human.ask.requested event, exactly-once answer, timeout via retry policy — merge `4f3c10c` |
| `t18` | delivered | actors/limits.py LimitedActor budgets + concurrency caps persisted in actor_usage — merge `ac551ae` |
| `t19` | delivered | actors/secrets.py grant: references (d2), redaction, single resolver shared with ops/backup — merge `85eff9d` |
| `t20` | delivered | engine/replay.py replay without side effects (+ POST /replay and `rules replay` at integration) — merge `382053d` |
| `t21` | partial | engine/reports.py run summaries to a channel via a ChannelPoster protocol; no real mesh poster wired yet — merge `43cfeee` |
| `t22` | delivered | ops/health.py + ops/logs.py JSON logs with run/step/host, run-id propagation — merge `75f8300` |
| `t23` | delivered | server/ FastAPI app under [server], committed api/openapi.json + drift test, stateless, SSE — merge `cb1af03` |
| `t24` | delivered | auth/ Access JWT (stdlib RS256), two listeners, service tokens, viewer/editor/admin roles — merge `b58375e` |
| `t25` | delivered | CLI noun groups over the API from one registry (purge/replay verbs added at integration) — merge `d793a04` |
| `t26` | delivered | mcp/ server from the same registry under [mcp] (official mcp SDK `<`2, not agentfront[mcp]) — merge `0c5bb06` |
| `t27` | delivered | tests/test_surface_parity.py registry vs CLI vs MCP vs OpenAPI — merge `0faddc6` |
| `t28` | delivered | tests/test_boundaries.py sibling-boundary guards — merge `dffd150` |
| `t29` | delivered | web/ Vite + React + @xyflow/react shell, org tokens, four tabs, identity, a11y baseline — merge `c95b9ff` |
| `t30` | delivered | Rules tab editor: list + focused rule, relationships editable, toggles, edit/delete, asks — merge `cb1c222` |
| `t31` | delivered | Workflows tab: node editor, typed ports, placement, import/export, repo picker, run overlay — merge `6eb40a2` |
| `t32` | delivered | Actors tab: roster, filter, inline expand, toggle/edit/delete — merge `1e2aa3f` |
| `t33` | delivered | Statistics tab: machine lanes, ranges, table view, tooltips; live refresh added in fix-validate — merge `3e90c6f` |
| `t34` | delivered | wheel ships culture_rules/web_dist via a hatch hook; API served with the SPA, also under /api — merge `44ce275` |
| `t35` | delivered | tests.yml web job (typecheck, vitest, palette, build, Playwright, webglass), coverage floor, mongo docker — merge `f4cfd1a` |
| `t36` | delivered | deploy/replica-set/* artifacts + docs/operations/replica-set.md (local docker proof only) — merge `0bb3193` |
| `t37` | partial | docs/operations/rules-culture-dev.md + deploy/cloudflared templates (dry-run never executed) — merge `09e63bc` |
| `t38` | delivered | ops/backup.py encrypted versioned S3 backups + restore drill (moto only) — merge `9980aea` |
| `t39` | delivered | tests/multihost placement, timing, chaos (simulated hosts, memory + mongo) — merge `60b43bf` |
| `t40` | delivered | README, docs/demo.md (executed on one host), four-tab harness prompts; issue #2 comment drafted — merge `9b42ffb` |
| `t41` | delivered (added by `d3`) | engine node daemon: heartbeat+probe, ingest, placed/unplaced triggers, executor loop, adapters with limits, reporter; typed Mongo transient errors (d3, c10, h10) — merge `d646428` |
| `t42` | delivered (integration task (not a plan task)) | API gaps from the web tabs: asks list, machine status, run filters/hosts, repo-backed export/import, shared web client cleanups, culture-rules mcp — merge `77b2c13` |
| `fix-reviews` | delivered (Qwen review fixes) | fix verified Qwen review findings: repo save commits only the plan, dangling rule references rejected, compose mongod non-root, import reports misplaced/duplicate files — merge `c407267` |
| `fix-validate` | delivered (validate-delivery fixes + d4) | close validate-delivery failures h33 h61 h78 h48 and d4 44px hit areas — merge `906c30f` |
| `fix-chain` | delivered (rule chaining fix) | node rule chaining: must_after/may_after re-evaluated when predecessors settle (h6, h78); unrunnable relationships rejected at save — merge `38568d5` |

## Mid-work Decisions

Approved deviation records (quoted from `devague deviate --list`):

- `d1` — Add optional Rule.placement (Placement: machine | actor | requirement) as a minor schema change; t1's h7 schema test 'no actor slot' is scoped to the rule's stages (trigger, condition, workflow, action), not its placement — spec c10/c60 require a rule's trigger and condition to evaluate where the rule is placed; t1's criterion omitted the field and t7's resolver needs it
- `d2` — Secrets use grant instead of shushu: references are `grant:<NAME>`, values resolved with 'grant get NAME' (refuses hidden secrets), hidden secrets injected with 'grant run --inject VAR=NAME -- cmd' — operator: grant is the new secrets command replacing shushu (grant list/get/run verified on spark)
- `d3` — add task t41: a 'culture-rules node' engine daemon in `culture_rules`/node that composes heartbeat + platform probe at start, event ingest, per-host and shared trigger consumers with rule placement, the Executor tick loop with real actor adapters (LimitedActor release on deliver) and the run reporter; plus retry/translate Mongo transient transaction errors in MongoStore — t39's multi-host tests proved the merged pieces compose only through a test harness: no plan task owns a production loop, so the shipped product would never fire rules on a host by itself; t39 also found MongoStore.transaction leaks raw pymongo WriteConflict/TransientTransactionError. Operator approved adding t41 (wave 7, before docs/demo).
- `d4` — visual sizes follow the 'Chosen' design canvas (40px filter pills, 46x28 switches, 40px icon buttons, 15px secondary text) instead of h8's 16px body / 44px target clause; every interactive control still gets a >=44x44px hit area via invisible padding/pseudo-element without changing its look; h8 is otherwise kept (keyboard walk, axe 0 serious, reduced motion) — operator chose the canvas as the visual source of truth (c102/o13); the canvas boards use smaller controls than h8 states; operator approved after reviewing the Actors/Rules screenshots

Decisions no deviation record covers:

- Integration commits by the main agent between tasks: the asks answer route and `/health`
  wired to t17/t22 inside t23; CLI tests switched to real service tokens with `POST /replay`
  and the `purge` / `replay` verbs added inside t24; the multihost harness authenticated with
  service tokens (t39); the web client aligned to the real `/whoami` shape (t29).
- `t42` closed API gaps the tabs reported (asks list, machine status, run filters with hosts,
  repo-backed export/import, `culture-rules mcp`) — completing t30/t31/t33's criteria.
- The command registry is cited from agentfront (cite-don't-import) so the core keeps zero
  runtime dependencies; the MCP server uses the official `mcp` SDK (`<2`) instead of
  `agentfront[mcp]`.
- Browser navigations to UI paths get the SPA; API clients get the API, also under `/api`.
- Every API request needs a credential, including `/health`.
- Qwen cortex and worker seats reviewed each merged task; 9 real findings were fixed
  (`fix-reviews`, t35, t41); about 15 claims were false positives (`reviews/triage.md` in the run
  scratchpad).
- Qwen live testing (operator request) against a running local stack found three CLI/API
  defects, fixed in `3facacd`.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t1` (`d1`) | spec c10/c60 require a rule's trigger and condition to evaluate where the rule is placed; t1's criterion omitted the field and t7's resolver needs it | `acceptable` |
| `t19` (`d2`) | operator: grant is the new secrets command replacing shushu (grant list/get/run verified on spark) | `acceptable` |
| `t39` (`d3`) | t39's multi-host tests proved the merged pieces compose only through a test harness: no plan task owns a production loop, so the shipped product would never fire rules on a host by itself; t39 also found MongoStore.transaction leaks raw pymongo WriteConflict/TransientTransactionError. Operator approved adding t41 (wave 7, before docs/demo). | `needs-follow-up` |
| `t29` (`d4`) | operator chose the canvas as the visual source of truth (c102/o13); the canvas boards use smaller controls than h8 states; operator approved after reviewing the Actors/Rules screenshots | `acceptable` |

Drift no deviation record covers:

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t15` | the mesh path is built against an `AgentClient` protocol; the real agentirc client is async and no sync bridge exists, so mesh agent tasks are not usable end to end | needs-follow-up |
| `t21` | run summaries post through a `ChannelPoster` protocol; no real mesh poster is wired into the node | needs-follow-up |
| `t37` | restricted to dry-run, yet even the cultureflare dry-run reads live Cloudflare state, so it was never executed; the runbook's id table is placeholders | needs-follow-up |
| `t36`, `t38` | deploy artifacts and backups proven locally only (docker replica set, moto S3); real rollout and the real-S3 drill are operator hand-turns | needs-follow-up |
| `t9` / `t41` | `must_after` dependants never fired on a real node until `fix-chain`; matching was correct, the node never supplied run facts or re-evaluated | acceptable (fixed before PR) |
| `t25` | `purge` and `replay` verbs landed in the t24 integration commit, not in t25 itself | acceptable |
| `t35` | the CI workflow has never run on GitHub Actions | risky |

## Evidence

- tests: full suite `uv run pytest -n auto` at `3facacd` — 1337 passed, 3 skipped (events-cli not
  installed ×2, one report-only cross-repo gap); docker Mongo, multihost and chaos tests ran
- web at `3facacd`: `npm test` 164 passed · `npm run check` (tokens + palette light/dark) PASS ·
  `npm run build` OK · `npx playwright test` 46 passed
- lint: black, isort, flake8, `bandit -c pyproject.toml -r culture_rules`, `teken cli doctor . --strict`
  (26/26), `scripts/scan-secrets.py` (clean), `markdownlint-cli2` (0 errors),
  `scripts/harness-smoke.py --stage config` (6 passed)
- validate-delivery: 80 obligations filed (`o1`–`o80`), evidence `e1`–`e79`, deltas `b1`–`b21`
  (all `llm`-origin, pending operator adjudication); first pass at `9b42ffb`: 67 pass, 6 fail, 7
  unchecked; re-validation at `38568d5` closed 6 (`e74`–`e79`)
- measured: chaos 200/200 requests ok with one host down, 100 events → 100 effects, 0
  duplicates, 0 lost (memory and mongo); fire latency 0.011 s (memory) / 0.361 s (mongo) vs a 5 s
  budget; worst SSE lag 0.358 s vs 2 s
- commits: `0a96f4f..3facacd` on `rules/build` (125 commits, 45 merges)
- issues: #1 (build brief), #2 (product model + UX)

## Delivery Claims

Confidence: a passing test at `execution` strength = high; `fidelity` (simulated hosts, mocked
API) = medium; `coverage`/`observation` = low; FAIL or UNCHECKED = unverified. Every evidence id
below is an `llm`-origin record still pending operator adjudication, and lapses `l2`–`l10` are
proposed (not yet evidence); approved lapse `l1` concerns spec-claim confirmation during /think,
not delivered behavior. Status now: 72 of 80 obligations pass, 1 fail, 7 unchecked.

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `c1` — culture-rules implements issues #1 and #2: a Python rules → condition → workflow → action engine with persisted runs on a MongoDB replica set, placement of r… | unverified | `e2` (h1: FAILED) |
| `c2` — The editor has four primary tabs — Rules \| Workflows \| Actors \| Statistics (operator decision 2026-10-03 adds Statistics to issue #2's three); runs/history/l… | high | `e1` (h2) |
| `c3` — Conditions are never eval() of user Python; they are a serialisable predicate the frontend edits graphically and the backend evaluates deterministically (iss… | high | `e3` (h3) |
| `c4` — Workflows declare inputs, internal variables, steps and outputs and do not know which trigger fired them; a rule binds a trigger (+ optional condition) to an… | high | `e4` (h4) |
| `c5` — Run state is persisted (not an in-memory call stack) because human/remote actors make runs long-running and asynchronous (issue #1 honesty condition) | medium | `e5` (h5) |
| `c6` — Rules can depend on a predecessor rule via a relationship (must run after / may run after) that also scopes which exported outputs are visible; exported outp… | medium | `e74` (h6) |
| `c7` — Actor is a generic model with concrete types (agent, human, daemon/service, runner/code, robot) plus capabilities, and is referenced from rules/workflows — n… | high | `e7` (h7) |
| `c8` — Default UX is graphical with large type/targets, minimal prose and chrome, smooth overview↔edit transitions, keyboard access and prefers-reduced-motion; raw … | high | `e75` (h8) |
| `c9` — The HTTP API contract is pinned in one place (OpenAPI or JSON Schema) so the web editor and the Python backend cannot drift (issue #1 'Shape the operator ask… | high | `e9` (h9) |
| `c10` — A rule's trigger and condition evaluate where the rule is placed (a named machine, an actor's machine, or a machine resolved from a requirement — see c60), s… | medium | `e10` (h10) |
| `c11` — Each workflow step declares its kind (logic, AI call, code run, actor task), typed input/output ports, and its own placement (machine, actor, or requirement … | medium | `e11` (h11) |
| `c12` — Agent actors are configured either from a DB record or from a repo's root config (loaded from the repo), and carry harness and model among their fields (oper… | high | `e12` (h12) |
| `c13` — The editor is reachable remotely at rules.culture.dev with SSO provided by cultureflare (operator, this session) | unverified | h13: UNCHECKED (operator hand-turn / no test) |
| `c14` — Before building the UI, three distinct graph-first UI directions are presented as visual mock-ups and the operator picks one (operator, this session) | low | `e13` (h14) |
| `c19` — The editor exposes an #agent-state element and CI runs a webglass job asserting the built SPA reports ready with no errors (culture-nodes/web.yml, webglass-c… | high | `e14` (h15) |
| `c20` — agentfront, irc-lens, league-of-agents-platform and webglass-cli are not React precedents and are not reused as the editor stack | low | `e15` (h16) |
| `c28` — Repo-based agent actors load from the repo's culture.yaml (suffix, backend, model, thinking, channels, system_prompt, tags, extras such as engine/base_url/ac… | high | `e16` (h17) |
| `c31` — callsmith is not modelled as a structured-call actor yet: it is a scaffold with only introspection verbs; at most a future capability | high | `e17` (h18) |
| `c32` — events-cli is an external trigger source: each host's engine node holds a durable events-cli subscription on configured type patterns (e.g. task.* → events/t… | medium | `e18` (h19) |
| `c33` — culture-rules does not build an event transport, broker or event history store — that is events-cli's; but events-cli's planned 'pipelines' (#8, contract.md:… | high | `e19` (h20) |
| `c34` — No integration is claimed with workledger-cli, agenda or protocols-cli: all three are unbuilt scaffolds with only introspection verbs; culture-rules keeps a … | high | `e20` (h21) |
| `c35` — eidetic is not a run-state or event store (no transactional resume, no monotonic cursor); at most an optional subprocess sink for run summaries | high | `e21` (h22) |
| `c36` — rules.culture.dev is provisioned with `cultureflare remote-login setup --hostname rules.culture.dev --service http://127.0.0.1:`<`port`>` --allow `<`email`>` --with-… | unverified | h23: UNCHECKED (operator hand-turn / no test) |
| `c37` — The API validates the Cf-Access-Jwt-Assertion JWT (team domain agentculture.cloudflareaccess.com + the app's AUD) itself, honoured only on a loopback listene… | high | `e22` (h24) |
| `c39` — Machine-to-machine traffic (rules/steps dispatching to thor, spark2) does not ride the public rules.culture.dev hostname; one hostname = one tunnel = one ser… | high | `e23` (h25) |
| `c40` — The HTTP server lives in an optional extra (e.g. culture-rules[server]) and is imported lazily inside the serve handler, so the core library + CLI keep depen… | high | `e24` (h26) |
| `c41` — The built web bundle ships inside the wheel as package data (e.g. culture_rules/web_dist) served by the API same-origin; publish.yml's paths filter gains web… | high | `e25` (h27) |
| `c42` — CI gains a web job (setup-node pinned to the SHA already used in lint, npm ci, typecheck, vitest, build, webglass); .gitignore gains node_modules/, web/dist,… | high | `e26` (h28) |
| `c43` — No committed JSON file carries a non-localhost url/host/endpoint/base_url key (e.g. a rules.culture.dev base URL in package.json or a config JSON); the publi… | high | `e27` (h29) |
| `c44` — Template self-description leftovers are replaced with culture-rules wording alongside the first real verbs: cli/__init__.py:74, learn.py:58-65,98, explain/ca… | high | `e28` (h30) |
| `c45` — The project exposes a CLI and an MCP server that control rules, workflows and actors (list/show/create/update/enable/disable/run, dry-run by default with --a… | high | `e29` (h31) |
| `c48` — Rules, workflow steps and actors each have an enable/disable toggle, edit and delete — in the editor and mirrored as CLI/MCP verbs (enable, disable, edit/upd… | high | `e30` (h32) |
| `c49` — A Statistics tab shows each enrolled machine as a lane (direction A) with its live state (online, load: CPU/GPU/memory), what it is working on now (running s… | medium | `e76` (h33) |
| `c50` — Chosen UI composition: Rules tab = direction C (rule list + focused vertical rule), Workflows tab = direction B (free-form node editor with typed ports, mach… | medium | `e32` (h34) |
| `c51` — Replay: a rule can be dry-run against recorded events (events-cli history) to show which runs would have fired, before it is enabled (operator, this session) | high | `e33` (h35) |
| `c52` — Run overlay: a run lights up its path on the workflow graph (which steps ran, on which machine, with outcome), so history is shown in context, not as a tab (… | medium | `e34` (h36) |
| `c53` — Capability placement: a step may request a capability (e.g. gpu) instead of a named machine, and the engine places it on an enrolled machine/actor that offer… | high | `e35` (h37) |
| `c54` — Workflows and rules export to and import from files (JSON/YAML, round-tripping with the graphical editor; that view is the advanced two-way mode); a git repo… | high | `e36` (h38) |
| `c55` — Per-actor limits: budgets (tokens/cost, aligned with culture.yaml token_budget) and concurrency caps per actor, enforced by the engine and visible on the act… | high | `e37` (h39) |
| `c56` — Mesh reports: run results can be posted to a Culture mesh channel (and optionally emitted as events-cli events) so agents and humans see outcomes (operator, … | high | `e38` (h40) |
| `c57` — Per-actor secrets are referenced, never stored in definitions — sealed via shushu (as cultureflare --shushu does) and resolved at run time on the executing m… | high | `e39` (h41) |
| `c58` — Machine identity colors are a validated categorical palette: light #0a8a78 (spark) / #b4531f (thor) / #3b4fb0 (spark2) passes all six dataviz checks (org --a… | high | `e40` (h42) |
| `c59` — culture-rules is a separate engine from culture-nodes for now (operator decision); it owns rules, workflows, actors and runs itself | high | `e41` (h43) |
| `c60` — Placement of a rule or workflow step is one of: a named machine, a named actor (runs where that actor lives), or a requirement/capability (e.g. gpu) resolved… | high | `e42` (h44) |
| `c61` — Action is a distinct concept from Workflow. A rule's shape is Trigger → [Condition] → [Workflow] → Action: condition and workflow are optional, the action is… | high | `e43` (h45) |
| `c62` — Always-on multi-machine service: spark is primary, spark2 and thor are redundant; if one host falters, the editor/API keeps serving and rules keep firing wit… | medium | `e44` (h46) |
| `c63` — Multi-host safety: a rule firing or a run step is claimed by exactly one host (lease/leader or idempotent claim keyed on event id), so redundancy never doubl… | high | `e45` (h47) |
| `c66` — The operator (Ori) composing and supervising automation across spark, thor and spark2 from a browser at rules.culture.dev; and mesh agents that drive the sam… | high | `e77` (h48) |
| `c67` — Automation across the machines is scattered: culture-nodes declarations authored as YAML/CEL on thor, cron jobs and ad-hoc scripts per host, agents nudged by… | low | `e47` (h49) |
| `c68` — One graphical, agent-operable place to decide when work happens, how it flows across machines and who does it — and it keeps working when one machine falters… | low | `e48` (h50) |
| `c69` — From rules.culture.dev (SSO) the operator builds a rule as Trigger → Condition → (Workflow) → Action by direct manipulation, places it and each step by machi… | unverified | h51: UNCHECKED (operator hand-turn / no test) |
| `c70` — A rule authored in the editor fires within 5 s of a matching events-cli event and its run appears in the run overlay and Statistics within 2 s of each step c… | medium | `e49` (h52) |
| `c71` — With any one of spark, spark2 or thor stopped, rules.culture.dev keeps serving (`>`= 99% of 200 requests succeed during failover) and 100 replayed events produ… | medium | `e50` (h53) |
| `c72` — 100% parity: every rule/workflow/actor operation (list, show, create, update, enable, disable, delete, run, export, import) is reachable from the editor, the… | high | `e51` (h54) |
| `c73` — Repo gates stay green: teken cli doctor --strict 26/26 (or more), coverage `>`= 60%, SonarCloud quality gate passed, webglass reports ready with 0 errors | low | `e52` (h55) |
| `c74` — Persisted state (rules, workflows, actors, machines, run state and history) lives in MongoDB or Postgres, not in local files (operator, this session) | high | `e53` (h56) |
| `c76` — State is backed up to AWS S3: the full culture-rules configuration (rules, workflows, actors, machines and settings) and run history (operator, this session) | medium | `e54` (h57) |
| `c77` — S3 backups are encrypted (SSE-S3 or SSE-KMS), versioned with a retention policy, scheduled (e.g. daily snapshot + hourly run-history increments), and the buc… | unverified | h58: UNCHECKED (operator hand-turn / no test) |
| `c78` — Quorum must survive spark2 being routinely offline: the replica set has 3 voting members that stay up (e.g. spark + thor + an arbiter or a 4th/5th member suc… | high | `e55` (h59) |
| `c79` — The engine's replica set is a dedicated mongod per host on a non-default port (spark's 27017 is already taken by a standalone weather-mongodb), with member a… | high | `e56` (h60) |
| `c80` — The HTTP API is stateless and active-active on every serving host: any host can serve any request (Cloudflare tunnel replicas route to any healthy connector,… | medium | `e78` (h61) |
| `c81` — Machines are explicit enrolled records (name, tailnet address, platform, capabilities such as gpu, roles: store member / engine node / runner), sourced from … | high | `e58` (h62) |
| `c82` — Every host runs an engine node that heartbeats liveness and load (CPU, memory, GPU when available) every 10 s into the store; a node missing 3 heartbeats is … | high | `e59` (h63) |
| `c83` — Every step and action declares a timeout, a retry policy (max attempts, backoff) and an idempotency key derived from (run id, step id, attempt-independent); … | high | `e60` (h64) |
| `c84` — Published workflow and rule versions are immutable; a run pins the exact versions it started with, so editing a workflow never changes an in-flight run | high | `e61` (h65) |
| `c85` — Stored documents carry a schema_version; a node refuses to write documents of a newer major version than it understands, migrations are forward-only and run … | unverified | h66: UNCHECKED (operator hand-turn / no test) |
| `c86` — Every change (create, update, enable, disable, delete, import, run) is recorded in an append-only audit log with who (SSO identity, service token, or agent n… | high | `e62` (h67) |
| `c87` — Delete is a soft delete: items are tombstoned and restorable for 30 days with their run history intact; a separate purge verb (dry-run unless --apply, admin … | high | `e63` (h68) |
| `c88` — Containment: a global pause stops all rule firing in one action (UI, CLI, MCP), a machine can be drained (no new placements, running steps finish), and any r… | high | `e64` (h69) |
| `c89` — Runs are observable: every node exposes a health endpoint, logs are structured with run_id/step_id/host, and run_id/correlationId propagate onto events-cli e… | medium | `e65` (h70) |
| `c90` — Backups meet RPO `<`= 1 h for run history and `<`= 24 h for configuration snapshots (or `<`= 1 h if config changed), and a full restore into an empty replica set c… | unverified | h71: UNCHECKED (operator hand-turn / no test) |
| `c91` — A rule's triggers consume the MongoDB events collection (change streams on the replica set), so a rule that fails over to another host resumes from its store… | medium | `e66` (h72) |
| `c92` — Code steps run either a command/script registered on a runner actor (default; arguments bound from typed inputs) or, for admins only, inline script text exec… | high | `e67` (h73) |
| `c93` — Asking a human creates an ask with an id and raises an event (e.g. human.ask.requested, carrying ask id, question, options, run/step) that rules can trigger … | high | `e68` (h74) |
| `c94` — Workflows are DAGs plus bounded loops — for-each and retry-until — each requiring a max count (operator, challenge q3) | high | `e69` (h75) |
| `c95` — All enabled rules matching an event fire independently by default; optional exclusive groups fire only their highest-priority match (operator, challenge q4) | high | `e70` (h76) |
| `c96` — Authorization: roles viewer / editor / admin (admin: purge, inline scripts, enrol, global pause) mapped from SSO identities and service tokens; the CLI and M… | high | `e71` (h77) |
| `c97` — Rules may supersede other rules: when a rule A that supersedes rule B matches an event (trigger and condition both hold), B does not fire for that event and … | high | `e79` (h78) |
| `c98` — The Rules tab shows supersession as a relationship (like must/may run after): a 'supersedes' badge naming the generic rule on the specific one, and a 'supers… | medium | `e73` (h79) |
| `c102` — The design canvas (design canvas artifact Jgm3JPnAhKWpeiCxFXvNBi) (row 'Chosen': Rules C, Workflows B, Actors C, Statistics A) is the visual source of truth … | unverified | h80: UNCHECKED (operator hand-turn / no test) |

## Remaining Work / Follow-up

- `h1` / `c1`: the announcement is not fully delivered until SSO at rules.culture.dev and the
  real three-host rollout exist — operator hand-turns below.
- Operator hand-turns (runbooks in `docs/operations/`): replica-set rollout on spark, thor and
  orin with spark2 non-voting (`replica-set.md`); `cultureflare remote-login setup` dry-run then
  `--apply`, filling the id table (`rules-culture-dev.md`); the real-S3 backup drill and the
  RPO/RTO results table (`backup.md`); a rolling upgrade across three hosts (`h66`); the
  multi-host demo (`h51`).
- First real CI run on GitHub Actions (`t35`, lapse `l9`) — happens on this PR.
- `t15`: a sync bridge for the async agentirc client so mesh agent tasks run end to end.
- `t21`: wire a real mesh `ChannelPoster` into the node for run summaries.
- The node CLI does not wire human asks (`HumanAdapter` needs an emitter) and the production agent
  and code adapters have no end-to-end test (lapse `l8`).
- A placed predecessor with a fatal placement leaves its `must_after` dependants waiting
  forever (`fix-chain` limitation).
- A superseding rule that is itself blocked by its predecessor still suppresses the rule it
  supersedes (matches c97's literal "matched"; operator to confirm the intent).
- `rule_decisions` grows without a cap or TTL.
- Steps have no CLI/MCP verbs of their own; they change through `workflows update` (`h32`).
- Adjudicate the pending records: evidence `e1`–`e79`, deltas `b1`–`b21`, obligations
  `o1`–`o80`, lapses `l2`–`l10`.
- Upstream devague bug: `devague summary` resolves delivery evidence `oN` against the plan's own
  obligations (`o1`–`o13`), so its skeleton's Delivery Claims rows cite the wrong tests; this
  artifact builds that table from the filed records instead.
- Post the drafted issue #2 comment (four-tab decision) with the PR, as the operator chose.
- Remaining Qwen reviews (both seats) continue after the PR opens; triage any new findings.
