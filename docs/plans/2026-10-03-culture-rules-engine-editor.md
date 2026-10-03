# Build Plan — culture-rules engine + editor

slug: `culture-rules-engine-editor` · status: `exported` · from frame: `culture-rules-engine-editor`

> culture-rules implements issues #1 and #2: a Python rules → condition → workflow → action engine with persisted runs on a MongoDB replica set, placement of rules and workflow steps by machine, actor or requirement across enrolled machines (spark, thor, spark2), a generic Actor registry (agents with db- or repo-based config, harness, model), controlled from a CLI, an MCP server and a graph-first React Flow editor with four tabs (Rules | Workflows | Actors | Statistics), served always-on from several hosts at rules.culture.dev behind cultureflare SSO

## Tasks

### t1 — Domain model + JSON schemas: Rule, Workflow, Action, Actor, Machine, Placement

- instruction: Stdlib only (dataclasses + typing). Put validation in model/validate.py returning structured errors, not exceptions from deep inside. Bounded loops are step kinds `for_each` and `retry_until` with a required max. Keep relationship edges on the Rule, not as separate stages.
- covers: c4, h4, c7, h7, c61, h45, c94, h75
- acceptance:
  - `culture_rules`/model/ defines frozen dataclasses for Rule (trigger, optional condition, optional workflow ref, required action, relationships `must_after`/`may_after`/supersedes, `exclusive_group`+priority, enabled), Workflow (inputs, variables, steps, outputs, version), Step (kind logic|ai|code|`actor_task`, typed in/out ports, placement, timeout, retry), Action, Actor (kind agent|human|service|daemon|runner|robot, capabilities), Machine and Placement (machine|actor|requirement)
  - validation rejects: a rule without an action; a workflow containing any trigger reference; a loop without a max count; an Actor kind outside the six; a placement not of exactly one form
  - a rule with no condition and no workflow validates; every model round-trips `to_json` -> `from_json` identically (tests/model/)
  - schemas/ holds one JSON Schema per model generated from the dataclasses and a test asserts the committed files match
- obligation: `o1` (criterion 1) [model JSON + schemas/ (consumed by io, server, web, CLI)] `to_json`/`from_json` are stable and lossless; field names `snake_case`; adding an optional field is a minor change, renaming or removing one is a major change that bumps `schema_version`

### t2 — Condition predicate tree + deterministic evaluator + text view

- instruction: Write the evaluator as a small recursive interpreter. 'matches' uses re.fullmatch with a length cap. The text form is the advanced view only; the tree is the stored form.
- covers: c3, h3
- acceptance:
  - `culture_rules`/model/condition.py defines a typed JSON predicate tree (compare, and, or, not, exists, in, matches) over trigger fields, variables and literals
  - evaluate(tree, context) is pure: 1000 evaluations of the same input give identical results; no eval/exec/compile anywhere under `culture_rules`/ (grep test + bandit B307)
  - `to_text`/`from_text` round-trip a CEL-style subset, and malformed text returns a structured parse error

### t3 — Storage port, in-memory adapter and `schema_version` guard

- instruction: The contract suite must be importable by the Mongo adapter task so both adapters run the same tests. No third-party imports in store/port.py or memory.py.
- covers: c85, h66
- acceptance:
  - `culture_rules`/store/port.py defines the StoragePort protocol (documents per collection, conditional update for claims, change-feed iterator, transactions)
  - `culture_rules`/store/memory.py implements it for tests and passes a shared contract test suite tests/store/contract.py
  - every stored document carries `schema_version`; writing a document of a newer major version than the node supports raises VersionSkewError; a forward-only migration registry runs only after a backup hook returns ok
- obligation: `o2` (criterion 1) [StoragePort (`culture_rules`/store/port.py; consumed by mongo, claims, executor, events, audit, machines)] conditional update reports won/lost atomically; the change feed yields every committed write exactly once in commit order per collection and resumes from a persisted token
- obligation: `o3` (criterion 3) [stored document envelope] every document carries id, `schema_version` and `updated_at`; readers ignore unknown fields; a writer never downgrades a document's `schema_version`

### t4 — Actor configuration from a DB record or a repo's culture.yaml

- instruction: Cite (do not import) the ~20-line shape logic from culture/`culture_core`/config.py:301-330; YAML parsing goes behind the optional extra or a minimal safe parser, because core has no runtime deps.
- covers: c12, h12, c28, h17
- acceptance:
  - `culture_rules`/actors/config.py loads culture.yaml in both shapes (top-level single agent and agents: list), keeping unknown keys as extras
  - loading this repo's culture.yaml and an equivalent DB record produce equal Actor models with harness (= backend) and model fields
  - fixtures from culture, steward and culture-rules culture.yaml files load without error

### t5 — Replace template self-description with culture-rules wording

- instruction: Only wording; no new verbs here (the surface task adds them). Update the CLAUDE.md paragraph that lists the leftovers.
- covers: c44, h30
- acceptance:
  - no 'clonable template' or 'Clone it, rename the package' wording remains in `culture_rules`/cli/`__init__.py`, cli/`_commands`/learn.py, explain/catalog.py, overview.py, whoami.py (grep test in tests/`test_wording.py`)
  - uv run teken cli doctor . --strict stays 26/26

### t6 — MongoDB replica-set adapter (majority writes, change streams, auth, TLS)

- instruction: pymongo lives behind an optional extra (culture-rules\[store\]). Default port is not 27017 (spark's 27017 is taken by unrelated standalone containers); read the URI from config/env only.
- depends on: t3
- covers: c74, h56, c79, h60
- acceptance:
  - `culture_rules`/store/mongo.py passes tests/store/contract.py against a disposable single-node replica set container
  - writes use w=majority; change feeds use change streams with resume tokens persisted per consumer
  - connections require authentication and TLS (configurable CA); the application user has no admin role; unauthenticated connect fails in the integration test
  - the engine writes no state to local files (assert empty data dir after a run)

### t7 — Placement resolver: machine, actor or requirement to one concrete host

- instruction: Pure function over (placement, machines snapshot, actors snapshot); no I/O.
- depends on: t1
- covers: c60, h44, c53, h37, c39, h25
- acceptance:
  - `culture_rules`/engine/placement.py resolves each of the three placement forms to exactly one enrolled, non-drained, online host or returns a structured error
  - a requirement such as gpu resolves only to a host/actor advertising it; none enrolled -> validation error
  - peer addresses must be LAN/tailnet; the public hostname rules.culture.dev is refused as a dispatch address

### t8 — Machine enrolment, heartbeats and platform probe

- instruction: Probes run subprocess with argv lists and short timeouts; missing tools are recorded as absent, never fatal.
- depends on: t3
- covers: c81, h62, c82, h63
- acceptance:
  - `culture_rules`/machines/ provides enrol/unenrol (dry-run unless apply) and stores Machine records (name, tailnet address, platform, capabilities, roles)
  - an engine node heartbeats every 10 s with liveness and load; 3 missed heartbeats mark it offline and placement skips it (fake-clock test)
  - at start the node probes its platform for load/GPU tools (nvidia-smi, tegrastats) and records which exist; USB-attached requirements are probed only when a step requests them
- obligation: `o9` (criterion 2) [heartbeat document (consumed by placement, Statistics)] a heartbeat carries machine, ts, load (cpu, mem, optional gpu), probed tools and engine version; absence for 30 s means offline

### t9 — Rule matching: all-fire, exclusive groups, supersede, must/may-after, exports

- instruction: Keep matching pure (event + rule snapshot + run facts -> decisions with reasons) so replay can reuse it.
- depends on: t1, t2
- covers: c6, h6, c95, h76, c97, h78
- acceptance:
  - `culture_rules`/engine/matching.py returns the set of rules to fire for an event: every enabled match by default; within an exclusive group only the highest priority
  - supersede is per event: B is skipped only when a superseding A matched; A's condition false -> B fires; chains are transitive; a cycle is rejected at save; B's history records 'superseded by A'
  - `must_after` blocks until the predecessor run succeeded; a downstream rule sees only explicitly exported outputs (unexported reference fails validation)
  - disabled rules and a global pause flag fire nothing
- obligation: `o4` (criterion 2) [match decision (consumed by executor, replay, contextual history)] matching returns every candidate rule with fire or skip and a reason code (`superseded_by`, `group_lost`, disabled, paused, `blocked_by_predecessor`, `condition_false`) and is pure for a given snapshot

### t10 — Exactly-once claims and idempotency keys across hosts

- instruction: Leases carry an expiry so a crashed claimant's work is reclaimable; reclaim never re-runs a step whose completion was recorded.
- depends on: t3
- covers: c63, h47
- acceptance:
  - `culture_rules`/engine/claims.py claims a firing or step via a conditional update keyed by an idempotency key; two engine instances racing on the same key -> exactly one wins (contract test on memory and mongo adapters)
  - idempotency keys derive from (run id, step id) independent of attempt
- obligation: `o5` (criterion 2) [idempotency key (consumed by executor, actors, audit)] the key is a stable hash of `run_id` + `step_id`, identical across retries and hosts, passed to every actor invocation and recorded on the audit entry

### t11 — Export/import of rules and workflows, with an optional git repo target

- instruction: Repo layout: `rules/<name>.yaml` and `workflows/<name>.yaml` under a configurable directory.
- depends on: t1
- covers: c54, h38
- acceptance:
  - `culture_rules`/io/ exports rules/workflows (and actors, secrets as references only) to JSON/YAML and imports them; export -> import yields identical definitions
  - save to and load from a second git repo works against a temp repo (git CLI via subprocess argv)
  - import is a write: dry-run diff unless apply

### t12 — Run executor: persisted runs, pinned versions, timeouts/retries, loops, pause/drain/cancel

- instruction: Model the run as an explicit state machine document, never an in-memory call stack. Steps are dispatched through an ActorPort protocol so adapters land separately.
- depends on: t1, t7, t10, t3
- covers: c5, h5, c84, h65, c83, h64, c88, h69, c11, h11
- acceptance:
  - `culture_rules`/engine/runs.py persists every run transition; killing and restarting the engine resumes a run without re-executing completed steps
  - a run pins the exact rule/workflow versions it started with; editing a workflow mid-run leaves the run on the old version
  - each step honours timeout and retry policy; an action whose ack was lost is not executed twice (fake actor test)
  - `for_each`/`retry_until` never exceed max; global pause, machine drain and run cancel work and are audited
  - each step runs on its own placement; typed outputs flow to the next step's typed inputs (mismatch fails validation)
- obligation: `o6` (criterion 3) [ActorPort (consumed by agent, code, human, service adapters)] invoke(input, `idempotency_key`, deadline) returns accepted, completed, failed or blocked; adapters are idempotent on the key; long work returns accepted and completes later via an event

### t13 — Event fabric: Mongo events collection, events-cli ingest, change-stream triggers

- instruction: Import `events_cli` lazily behind an optional extra; propagate correlationId/causationId/runId on emitted envelopes.
- depends on: t6
- covers: c32, h19, c91, h72, c33, h20
- acceptance:
  - `culture_rules`/events/ingest.py holds a durable events-cli subscription per host, drains bounded batches and inserts envelopes into the events collection exactly once (unique index on envelope id)
  - `culture_rules`/events/triggers.py consumes the collection via change streams with a persisted resume token; after failover to another host every later event fires exactly once
  - culture-rules ships no broker, MQTT server or event-history store of its own (dependency + module test)
- obligation: `o7` (criterion 1) [events collection document (consumed by triggers, replay, human asks)] an envelope is stored verbatim with a unique index on its id plus `received_at` and host; nothing mutates a stored event

### t14 — Append-only audit log and soft delete / restore / purge

- instruction: Audit entries are insert-only; no update/delete API exists for them.
- depends on: t3
- covers: c86, h67, c87, h68
- acceptance:
  - `culture_rules`/engine/audit.py writes one entry per mutation with identity, host, time and diff; a registry-wide test asserts each mutating verb writes exactly one entry with a non-empty identity
  - `culture_rules`/engine/lifecycle.py soft-deletes (tombstone, restorable 30 days, history kept, never fires while deleted) and purges only for admins with apply

### t15 — Agent actor adapter: colleague work --json and mesh tasks with correlation

- instruction: agentirc is an optional extra; subprocess argv only, no shell.
- depends on: t12
- acceptance:
  - `culture_rules`/actors/agent.py runs a one-shot agent via `colleague work --repo --engine --model --json` and parses TaskResult
  - a mesh task to a `<machine>-<agent>` nick carries a correlation id and the reply is matched by it (fake AgentClient test)

### t16 — Code runner actor: registered commands, admin-only inline scripts

- depends on: t12
- covers: c92, h73
- acceptance:
  - `culture_rules`/actors/code.py runs commands registered on a runner actor with arguments bound from typed inputs as argv (never shell=True)
  - inline script text is accepted only from admins and runs sandboxed on the runner host (temp dir, timeout, cleaned up)
  - a non-admin cannot save or run inline script text (API-level test lives in the auth task)

### t17 — Human asks: id-bearing ask event and answer action

- depends on: t12, t13
- covers: c93, h74
- acceptance:
  - `culture_rules`/actors/human.py creates an ask with an id and emits exactly one human.ask.requested event (ask id, question, options, run/step)
  - an 'answer ask' action or the API answer endpoint resumes the waiting run exactly once; a second answer is rejected with a clear error
  - an ask timeout follows the step's timeout/retry policy
- obligation: `o8` (criterion 1) [human.ask.requested event + answer action] the event carries `ask_id`, question, options, `run_id`, `step_id` and deadline; the answer action takes `ask_id` and answer; both are schema-versioned

### t18 — Per-actor budgets and concurrency caps

- depends on: t12
- covers: c55, h39
- acceptance:
  - an actor at its concurrency cap queues further tasks; an actor over budget refuses new tasks with a structured error (`culture_rules`/actors/limits.py tests)
  - budget fields align with culture.yaml `token_budget`

### t19 — Secret references resolved at run time via shushu

- depends on: t12
- covers: c57, h41
- acceptance:
  - definitions, exports and logs only ever contain secret references; resolution happens on the executing host (`culture_rules`/actors/secrets.py)
  - exporting an actor with a secret yields only the reference; scan-secrets passes

### t20 — Replay a rule against recorded events without side effects

- depends on: t9, t13
- covers: c51, h35
- acceptance:
  - `culture_rules`/engine/replay.py replays N recorded envelopes through matching and reports would-fire runs with reasons
  - replay executes 0 actions (fake actor asserts no calls)

### t21 — Post run summaries to a mesh channel (optional events)

- depends on: t12
- covers: c56, h40
- acceptance:
  - a finished run posts a summary to a configured mesh channel; a failing post never fails the run (fake client that raises)

### t22 — Health endpoints, structured logs, run-id propagation

- depends on: t12
- covers: c89, h70
- acceptance:
  - every node exposes a health status; logs are JSON with `run_id`/`step_id`/host; given a run id its step trail across two nodes is retrievable
  - `run_id`/correlationId propagate onto emitted events-cli envelopes

### t23 — HTTP API (optional \[server\] extra): OpenAPI contract, stateless active-active, SSE

- instruction: FastAPI + uvicorn behind culture-rules\[server\]. No session state in process memory.
- depends on: t12, t14, t13
- covers: c40, h26, c9, h9, c80, h61
- acceptance:
  - `culture_rules`/server/ serves the API from an optional extra imported lazily inside the serve handler; pip install without extras keeps zero runtime deps and teken doctor stays green
  - the served OpenAPI equals the committed api/openapi.json (CI diff test)
  - the API is stateless: two instances on one store both serve any request; SSE live updates fan out via store change feeds within 2 s
- obligation: `o10` (criterion 2) [api/openapi.json (consumed by web types, CLI/MCP API client, parity test)] it is the single HTTP contract; any route or schema change updates the committed file in the same PR and the drift test fails otherwise

### t24 — Cloudflare Access JWT, two listeners, service tokens, viewer/editor/admin roles

- instruction: Follow culture-nodes/internal/api/principal.go and docs/operations/nodes-culture-dev.md (two-listener split, all-or-nothing team-domain + AUD config).
- depends on: t23
- covers: c37, h24, c96, h77
- acceptance:
  - `culture_rules`/auth/ validates Cf-Access-Jwt-Assertion signature (team JWKS), aud and exp; forged/expired/wrong-aud tokens are rejected
  - a loopback listener honours Access headers; a LAN listener ignores them and requires a service token
  - authz matrix test: viewer cannot mutate, editor cannot purge or run inline scripts, admin can; CLI/MCP modules never import a store driver (import test)
- obligation: `o11` (criterion 1) [request principal (consumed by audit, authz, every mutating route)] every request resolves to a principal {identity, kind sso|service|agent, roles}; an unresolvable request is rejected before any handler runs

### t25 — CLI noun groups over the API: rules, workflows, actors, machines, runs

- instruction: Build one command registry (agentfront App) that the MCP task reuses; one module per noun under `culture_rules`/cli/`_commands`/.
- depends on: t23, t5
- covers: c45, h31, c48, h32
- acceptance:
  - culture-rules {rules,workflows,actors,machines,runs} expose list/show/create/update/enable/disable/delete/restore/purge/run/export/import/replay/pause/drain as applicable, each with --json and an overview verb
  - every write is dry-run unless --apply; a write without --apply changes nothing (test)
  - every verb has an explain catalog entry and appears in learn; teken cli doctor --strict passes
  - the CLI talks only to the HTTP API client
- obligation: `o12` (criterion 1) [command registry (consumed by CLI, MCP, parity test)] each verb is registered once with name, params schema, mutating flag and required role; CLI, MCP and the parity test enumerate this same registry

### t26 — MCP server from the same registry (optional \[mcp\] extra)

- depends on: t25
- acceptance:
  - `culture_rules`/mcp/ exposes every registry verb as an MCP tool via agentfront\[mcp\]; writes require an explicit apply argument
  - the MCP server talks only to the HTTP API client

### t27 — Surface parity test across CLI, MCP and OpenAPI

- depends on: t25, t26, t23
- covers: c72, h54
- acceptance:
  - tests/`test_surface_parity.py` enumerates the registry and asserts each operation exists on the CLI, in the MCP tool list and in the OpenAPI paths
  - removing one MCP tool makes the test fail (mutation check)

### t28 — Boundary guard tests: no culture-nodes, workledger, agenda, protocols, callsmith or eidetic coupling

- depends on: t1
- covers: c31, h18, c34, h21, c35, h22, c59, h43
- acceptance:
  - tests/`test_boundaries.py` fails if `culture_rules`/ imports `culture_nodes`, workledger, agenda, protocols, callsmith or eidetic, or uses eidetic for run state
  - the test suite passes with culture-nodes absent

### t29 — web/ scaffold: Vite + React + @xyflow/react, tokens, four-tab shell, identity, a11y baseline

- instruction: The design canvas <https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi> (row 'Chosen') is the visual source of truth; copy its tokens, shapes and spacing. Mirror culture-nodes/web (src/culture-design, api/client.ts, hooks/useWhoami.ts, agent-state/). Types hand-maintained against api/openapi.json.
- depends on: t23
- covers: c2, h2, c20, h16, c58, h42, c8, h8, c19, h15, c102, h80
- acceptance:
  - matches the 'Chosen — Rules' board on the design canvas <https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi> (layout, stage shapes, machine colors, type scale, controls) — verified by a Playwright screenshot reviewed against the board
  - web/ builds with Vite 6, React 18, TypeScript, @xyflow/react 12 and elkjs; npm with tracked lockfile; no dependency on agentfront/irc-lens templates
  - exactly four top-level tabs (Rules, Workflows, Actors, Statistics) and no top-level runs/history route (Playwright)
  - tokens.css is a pinned copy of org's theme with a byte-identity check script; chart palette light #0a8a78/#b4531f/#3b4fb0 and a dark set both pass `validate_palette.js`
  - \#agent-state reports ready with no errors; identity comes from GET /whoami; keyboard walk + axe 0 serious; prefers-reduced-motion disables transitions
- obligation: `o13` (criterion 1) [design canvas row 'Chosen'] every tab implements its 'Chosen' board; any deliberate departure is recorded on the PR with a screenshot

### t30 — Rules tab (direction C): rule list + focused vertical rule

- depends on: t29
- covers: c50, h34, c98, h79
- acceptance:
  - matches the 'Chosen — Rules' board on the design canvas <https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi> (layout, stage shapes, machine colors, type scale, controls) — verified by a Playwright screenshot reviewed against the board
  - web/src/rules/ renders the list with toggles and the focused rule as must-after ghost -> trigger -> condition -> workflow -> action -> '+', matching the canvas 'Chosen — Rules' board
  - supersede, must-after and may-after show as relationship badges on both ends and are editable by direct manipulation (Playwright)
  - toggle, edit and delete work; pending human asks are answerable in context

### t31 — Workflows tab (direction B): node editor, typed ports, placement, import/export, run overlay

- depends on: t29
- covers: c52, h36
- acceptance:
  - matches the 'Chosen — Workflows' board on the design canvas <https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi> (layout, stage shapes, machine colors, type scale, controls) — verified by a Playwright screenshot reviewed against the board
  - web/src/workflows/ edits steps with typed ports and per-step placement (machine, actor, requirement); dashed edges mark cross-machine hops
  - Import, Export and the repo picker call the io endpoints
  - a run lights up its path with each step's host and outcome from persisted run state (Playwright fixture)

### t32 — Actors tab (direction C): large-type roster with inline expand

- depends on: t29
- acceptance:
  - matches the 'Chosen — Actors' board on the design canvas <https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi> (layout, stage shapes, machine colors, type scale, controls) — verified by a Playwright screenshot reviewed against the board
  - web/src/actors/ lists actors with kind filter, machine, toggle; the selected actor expands with repo/db config source, harness, model, capabilities, edit and delete

### t33 — Statistics tab (direction A): machine lanes

- depends on: t29
- covers: c49, h33
- acceptance:
  - matches the 'Chosen — Statistics' board on the design canvas <https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi> (layout, stage shapes, machine colors, type scale, controls) — verified by a Playwright screenshot reviewed against the board
  - web/src/statistics/ shows every enrolled machine including offline ones with load, running steps, queue depth, 24 h runs-per-hour bars and ok/failed with icon + label
  - time range 1h/24h/7d, a table view, and hover tooltips on bars; offline host renders offline (Playwright)

### t34 — Ship the web build in the wheel; publish paths; ignore/sonar/markdownlint config

- depends on: t29
- covers: c41, h27, c42, h28, c43, h29
- acceptance:
  - the built wheel contains `culture_rules`/`web_dist`/index.html (unzip -l check) and serving needs no Node at runtime
  - publish.yml paths include web/\*\* and the npm build runs before uv build
  - .gitignore covers `node_modules`/, web/dist, \*.tsbuildinfo without ignoring web/src/lib (git check-ignore test); sonar.sources includes web/src; markdownlint ignores web build output; scan-secrets passes

### t35 — CI: web job, webglass, Playwright, palette validation, all four gates

- depends on: t34, t30, t31, t32, t33
- covers: c73, h55
- acceptance:
  - .github/workflows/tests.yml gains a web job (pinned setup-node, npm ci, typecheck, vitest, build, Playwright, webglass) and the palette validator
  - teken doctor --strict, coverage >= 60%, SonarCloud gate and webglass ready are all enforced in CI

### t36 — Replica-set deployment across spark, thor and an always-up third voter

- instruction: Third always-up voter: orin or an arbiter — record the choice in the ops doc.
- depends on: t6
- covers: c78, h59, c62, h46
- acceptance:
  - deploy/ + docs/operations/replica-set.md bring up a dedicated mongod per host on a non-default port with keyfile/x509 and TLS
  - the voting topology keeps a primary with spark2 offline plus any one other member down (chaos check recorded)

### t37 — Provision rules.culture.dev with cultureflare on every serving host

- depends on: t24
- covers: c36, h23, c13, h13
- acceptance:
  - docs/operations/rules-culture-dev.md records the dry-run then --apply `cultureflare remote-login setup` and the resulting tunnel/app ids
  - each serving host runs cloudflared (token sealed via shushu) to its loopback Access listener; unauthenticated requests get the Access redirect

### t38 — Encrypted, versioned S3 backups with a restore drill

- depends on: t6
- covers: c76, h57, c77, h58, c90, h71
- acceptance:
  - `culture_rules`/ops/backup.py writes a consistent snapshot of config + run history to S3 with server-side encryption; schedule daily snapshot + hourly run-history increments
  - restore into an empty replica set reproduces every rule, workflow, actor and run (moto/minio test) and a real-S3 drill records RPO <= 1 h and RTO <= 30 min
  - bucket, region and credentials come from config/env/shushu only; scan-secrets passes

### t39 — Multi-host integration and chaos tests

- depends on: t12, t13, t23, t36
- covers: c10, h10, c70, h52, c71, h53
- acceptance:
  - a rule placed on node B evaluates only on B; a spark -> thor -> spark2 workflow runs each step on its host
  - a rule fires within 5 s of a matching event and SSE shows each step within 2 s (timed)
  - with one host stopped: >= 99% of 200 requests succeed and 100 events produce exactly 100 action executions, 0 duplicates, 0 lost

### t40 — README, demo script, prompts and issue follow-ups

- depends on: t35
- covers: c1, h1, c66, h48, c67, h49, c68, h50, c69, h51, c14, h14
- acceptance:
  - README names both audiences and the one-sentence why; the spec background cites the scope entries; docs/demo.md reproduces the after-state end to end
  - CLAUDE.md, QWEN.md, AGENTS.override.md, AGENTS.colleague.md and .pi/SYSTEM.md describe the four-tab design consistently and harness-smoke passes
  - a comment on issue #2 records the four-tab decision and links the design canvas (q6)

## Risks

- [unknown_nonblocking] t22 (observability) and t23 (server) share wave 3 and both touch HTTP surfaces: t22 owns `culture_rules`/ops/health.py and logging only; t23 mounts the health route — keep files disjoint or serialise at merge (task t22)
- [unknown_nonblocking] Jetson load/GPU probing (tegrastats/jtop output formats) is unverified on thor/orin; the probe must degrade to 'unknown' rather than fail (task t8)
- [follow_up] events-cli plans its own 'pipelines' (#8); file a boundary note on events-cli so the two do not converge on competing workflow models
