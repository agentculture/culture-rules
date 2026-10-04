# culture-rules second mile

> rules.culture.dev rules point at the real world: typed and scheduled triggers, actions that act through app and server actors, humans and agents as actors, and a Workflows tab where you build and test a workflow on the canvas alone, with PR #4 review follow-ups closed

## Audience

- The operator (Ori) building rules on rules.culture.dev, and the mesh agents and teammates whose GitHub/Discord/Jira activity and machines those rules react to and act on
  - instruction: check the exported spec names the operator, mesh agents and connected surfaces as readers/users

## Before → After

- Before: Today a rule cannot be pointed at anything real: an event trigger without a type matches every event, schedule does nothing, the only action handler is noop, actions never reach an actor, and a workflow can be tested only by writing a rule for it
  - instruction: see scope entries s1, s4, s9 (matching.py:145, runner.py:189, runs.py:909)
- After: An operator builds, from the editor alone, a rule triggered by a real GitHub, Jira, Discord, schedule or probe event, whose action comments, messages, calls HTTP or runs a machine command through an actor; and builds and test-runs a workflow with typed inputs on the canvas without writing a rule
  - instruction: walk docs/demo.md end to end on rules.culture.dev with no API/CLI calls for the editor parts

## Why it matters

- rules.culture.dev is live on four machines but inert; it replaces the stopped culture-nodes bridges (GitHub inbox, jira-bridge, Discord notify) only once real events trigger rules and real actions run
  - instruction: issue #5 'Why' section

## Requirements

- Saving an event trigger with no params.type is refused by validation (model/validate.py `_check_trigger`) in API, CLI, MCP and editor; today engine/matching.py:145-151 makes a typeless event trigger match every event
  - honesty: Validation rejects {kind:event, params:{}} and {kind:event, params:{type:''}} with a path-coded error (trigger.params.type); existing typeless event rules are reported by a migration/doctor check, not silently kept
    - instruction: tests/model/`test_validate.py`: typeless event trigger -> Invalid with path trigger.params.type; doctor/migration lists offenders
- The Rules tab trigger editor is typed: pick a surface (app actor or machine), then an event type from the actor's declared events, writing params.type; optional filter condition; no free-text trigger
  - honesty: The trigger editor never offers free text for the type: options come from enabled app actors' declared events (and schedule/probe/manual), and saving writes params.type; params.label stays display-only
    - instruction: vitest RulesEditor + Playwright rules.spec.ts: pick surface->type, assert PUT body has params.type
- A schedule trigger (cron expression, stdlib-only parser) fires from a node scheduler stage, evaluated only on the host the rule is placed on, exactly once per slot
  - honesty: The cron parser is stdlib-only and covers 5-field cron (\*, lists, ranges, steps); each slot fires at most once even across node restart or two hosts (dedupe marker rule/slot in a transaction); a missed slot while the placed host is offline is not backfilled unless stated
    - instruction: tests/engine/`test_cron.py` property tests + tests/node/`test_schedule.py` with fake clock and two hosts
- A probe trigger runs an allow-listed command on a chosen machine or app actor on a schedule and fires when its output changes or a condition over the output holds (e.g. trigger.data.temp > 30)
  - honesty: A probe runs only a command allow-listed on the target actor (CodeRunner rules: argv, no shell, timeout), its output becomes event data readable by conditions, 'on change' compares to persisted previous output, and a failed probe run emits no trigger
    - instruction: tests/node/`test_probe_trigger.py`: change/no-change/condition/failure cases
- A new actor kind 'app' (integration surface) declares the events it emits, the probes it offers and the actions it performs, with connection config whose secrets are `grant:<NAME>` refs only
  - honesty: An app actor's declared events/probes/actions are typed and validated on save, and any secret-looking param that is not a grant: ref is refused (`assert_refs_only`)
    - instruction: tests/model/`test_actor.py` app kind cases + tests/auth/`test_guards` `secret_literal`
- Signing in through Cloudflare Access creates a human actor (id and name from the Access identity, kind human, enabled, editable after); human actors are the default target for asks
  - honesty: First authenticated SSO request for an identity creates exactly one human actor (id slug of email, kind human, enabled), concurrent first requests do not duplicate it, service tokens never create one, and an operator edit is not overwritten by later sign-ins
    - instruction: tests/server/`test_human_actor_on_signin.py` incl. race and service-token cases
- Mesh agents on each machine are enrolled as agent actors (harness and model from culture.yaml, machine set), so workflow steps can be handed to them
  - honesty: Enrolment reads each machine's culture agent list and culture.yaml (harness, model) and upserts agent actors with machine set, idempotently; agents removed from the machine are disabled, not deleted
    - instruction: tests/actors/`test_enrol_agents.py` on fixture server.yaml + culture.yaml; dry-run by default, --apply commits
- Operators register allow-listed commands on a server (runner) actor from the Actors tab: typed args, no shell, as CodeRunner enforces
  - honesty: Commands added from the Actors tab are stored as CodeRunner commands {argv, params, timeout}; argv with sh -c / python -c is refused by the API, and args are typed fields in the UI
    - instruction: Playwright actors.spec.ts + tests/server inline-eval refusal
- A rule's action can name an actor in params and is dispatched through that actor's adapter with its limits; action-kind handlers (message, github.comment, jira.comment, http.call, machine.command) are registered as `action:<kind>` ports
  - honesty: A rule action naming params.actor runs through that stored actor's adapter wrapped by LimitedActor (limits apply); an unknown or disabled actor fails the run with a clear code instead of falling back to noop; each of the 5 action kinds has a handler with idempotency honoured
    - instruction: tests/engine/`test_runs.py` action-with-actor cases + tests/node/`test_action_handlers.py` per kind
- The Rules tab action editor picks an action kind offered by connected app and server actors, with typed params and graphical mappings from trigger data and workflow outputs (textual refs inspectable, never required)
  - honesty: The action picker offers only kinds that some enabled actor supports, renders typed param fields per kind, and mappings from trigger./workflow.outputs. refs are chosen graphically while the textual ref stays visible
    - instruction: vitest + Playwright rules.spec.ts action picker
- Each app surface delivers typed events (github.pr.opened, discord.message.created, jira.issue.updated ...) into the events collection, replacing the stopped culture-nodes bridges
  - honesty: Each surface event lands once in the events collection with a stable dotted type (<=4 segments or the depth limit is lifted) and a dedupe id from the surface's delivery id; malformed or unsigned input never lands
    - instruction: tests/events/`test_``<surface>``_ingest.py` per surface with recorded payload fixtures
- A workflow can be run directly with typed input values, without a rule: an API route plus a workflows run verb in CLI and MCP (parity test passes)
  - honesty: POST /workflows/{id}/run (or equivalent) validates inputs against declared typed ports, pins a synthetic rule, and the CLI and MCP 'workflows run' verbs pass tests/`test_surface_parity.py`; history and run filters show the run under the workflow
    - instruction: tests/server/`test_workflow_run.py` + `test_surface_parity` + api/openapi.json regenerated
- Run on the Workflows tab opens a typed input form built from the workflow's declared inputs (defaults applied), starts a direct run, lights steps as they run and shows the run's outputs
  - honesty: The input form is generated from workflow.inputs (type-appropriate controls, required marked, defaults filled), submission starts a direct run, steps light up as they run and outputs appear when it completes
    - instruction: Playwright workflows-run.spec.ts with stubbed API
- Selecting a step opens a properties panel with name, description, kind, actor/placement, config (runner command and args), typed ports, `timeout_s` and retry
  - honesty: Every Step field in model/workflow.py (name, description, kind, placement, config, ports, `timeout_s`, retry, `max_iterations`) is editable from the properties panel and round-trips through PUT /workflows/{id} unchanged
    - instruction: vitest StepEditor round-trip test over a fixture with all fields set
- The in and out nodes are click-selectable and open editors for the workflow's inputs, outputs (with source mapping to a step output port), variables and description
  - honesty: Clicking the in or out node selects it (mouse and keyboard) and opens the inputs or outputs/variables/description editor; an output's source can be set by wiring a step output port, and the textual source stays inspectable
    - instruction: Playwright workflows.spec.ts io-node click + axe stays clean
- Blocked actions explain what is wrong and offer fixes as options (add an input, pick an actor, wire this port, create a rule using this workflow); no raw error text; an empty workflow canvas says what to do next
  - honesty: Every API error code the Workflows and Rules tabs can receive maps to a plain-language message plus at least one fix option, and an unmapped code falls back to a generic guided message, never raw text
    - instruction: vitest table test: every code in RunError/ServiceError vocab has a mapping
- The Workflows tab can show soft-deleted workflows and purge them (admin)
  - honesty: The Workflows tab can list soft-deleted workflows (`include_deleted`) and purge one as admin with a confirmation step; non-admins don't see purge
    - instruction: Playwright + vitest role gating
- PR #4 review follow-ups land as fixes: legacy events cursor reads as start-fresh, fan-in docstring corrected, deep-event-type limit documented or configurable, `_machine_online` semantics pinned by tests, shared-chain transaction skipped when nothing depends on the rule, online threshold aligned with beat cadence, `record_decision` extra read removed, cross-host `_settled_skip` cascade, run-history filter in the store, Switch in-flight disabled, canvas height from real card heights, actor->machine dot, test premises strengthened, cache rule and node installer documented
  - honesty: Each #7 item is either fixed with a test that fails on the old code, or closed with a written reason (e.g. refuted items c23/c24)
    - instruction: issue #7 checklist cross-referenced in the PR description
- GitHub and Jira webhook receivers live on rules.culture.dev under one fixed path each (e.g. /hooks/github, /hooks/jira) covered by a path-scoped Cloudflare Access Bypass; each receiver authenticates the request itself (GitHub X-Hub-Signature-256 HMAC, Jira HMAC or URL token) and writes typed events into the shared events collection, deduped on the surface's delivery id
  - honesty: Only the exact receiver paths bypass Access; an unsigned or wrongly signed request returns 401 and writes nothing; a redelivered webhook (same delivery id) writes no second event
    - instruction: tests/server/`test_hooks_github.py`, `test_hooks_jira.py`; ops doc lists the Bypass policy paths
- A Discord app actor runs a Gateway bot listener as a long-lived, placed service (one holder at a time, like a lease) that turns gateway events into typed events (discord.message.created, ...) in the shared events collection; the same bot token sends the Discord message action
  - honesty: Exactly one Discord gateway connection is held mesh-wide at a time (lease), it reconnects and resumes after drop, every message event lands once with type discord.message.created, and the bot token is resolved only via grant
    - instruction: tests/actors/`test_discord_listener.py` with a fake gateway; live check on spark
- github.comment authenticates as the new culture-rules GitHub App: mint an RS256 JWT from its grant-held private key, exchange it for an installation token, call the REST API via urllib
  - honesty: Comments are authored by the GitHub App, installation tokens are cached until near expiry and never logged, and only allow-listed repos can be commented on
    - instruction: tests/actors/`test_github_app.py` with a fake API + live comment on a test PR
- Webhook receiver paths are exempt from the app's own auth middleware by exact path match (a Cloudflare Bypass request carries no Access JWT); every other path still requires a principal, pinned by a test that walks all routes
  - honesty: A test enumerates app.routes and asserts only /health and the exact hook paths answer without a principal; /hooks/github/x and /hooks/githubx still 401
    - instruction: tests/server/`test_auth_exemptions.py`
- Rules do not fire on events authored by our own GitHub App, Discord bot or Jira service account unless the rule opts in, and each rule has a fire-rate cap, so a comment action cannot re-trigger itself in a loop
  - honesty: An event whose author is the configured App/bot/service account does not fire a rule without opt-in, and a rule exceeding its fire-rate cap records a skip decision instead of firing
    - instruction: tests/engine/`test_self_authored.py` + rate-cap test
- http.call sends only to destinations allow-listed on the bound actor and refuses loopback, link-local and private/tailnet ranges unless explicitly allow-listed; no endpoint is hard-coded in committed files
  - honesty: http.call to 127.0.0.1, 169.254.x, 10/8, 100.64/10 or a non-allow-listed host fails the action with a clear code and makes no request
    - instruction: tests/node/`test_http_call.py` with a fake transport
- Existing typeless event rules are handled by a dry-run-by-default migration that disables them with an audit entry and lists them (never deletes), and the rollout upgrades every node before typed triggers are relied on, since nodes load rules with strict=False and an old node keeps match-all
  - honesty: The migration lists and (with --apply) disables typeless event rules with an audit record and deletes none; the ops doc states the node upgrade order
    - instruction: tests/store/`test_migrations.py` + docs/operations/rules-culture-dev.md
- Cron expressions evaluate in UTC unless the rule names an IANA timezone (stdlib zoneinfo); DST gaps and repeats fire each wall-clock slot at most once
  - honesty: Cron tests cover UTC default, an explicit tz, a DST gap and a DST repeat, each slot firing at most once
    - instruction: tests/engine/`test_cron.py`
- Every webhook delivery outcome (accepted, duplicate, bad signature, ignored type) and the Discord gateway state (holder host, connected, last event) are counted and visible in the editor or /health, and logged without payloads or secrets
  - honesty: Counters for each delivery outcome and the gateway state appear in /health (or Statistics) and log lines never contain payload bodies or secrets
    - instruction: tests/server/`test_hooks_metrics.py` + log capture assertion
- Disabling an app actor stops both its ingest (receiver answers 2xx and drops, listener disconnects) and its actions; the ops doc names the kill switches (actor disable, engine pause)
  - honesty: With an app actor disabled, a valid webhook writes no event and its actions fail with `actor_disabled`; re-enabling resumes
    - instruction: tests/server/`test_hooks_disabled_actor.py`
- Webhook receivers verify, write the event and answer 2xx without running rules or actions inline (GitHub times out deliveries at 10 s); Jira events are refetched from the API by issue key rather than trusted from the payload
  - honesty: A webhook handler returns 2xx after one event write with no rule evaluation in the request; a Jira hook refetches the issue by key
    - instruction: tests/server/`test_hooks_github.py` timing/no-eval + `test_hooks_jira.py` refetch with fake Jira

## Honesty conditions

- Every confirmed requirement in this frame maps to at least one plan task with a test, and the announcement holds on the live deployment, not just in MemoryStore
  - instruction: spec-to-plan coverage + validate-delivery live evidence
- A unit test of sequence()/live() for a 3-deep `must_after` chain with a failed root yields `predecessor_failed` for the leaf
  - instruction: tests/engine/`test_chaining.py`
- A test pins that a validly signed Access token naming no person or service ends as HTTP 401 code malformed
  - instruction: tests/auth/`test_access.py`
- pyproject runtime dependencies stay empty; every new third-party import sits behind an optional extra and is imported lazily
  - instruction: existing zero-deps test (or add one) importing `culture_rules` without extras
- Importing `culture_rules` and running the API without the discord/github extras works; GitHub/Jira HMAC verification uses hmac.`compare_digest`
  - instruction: test importing with extras absent + grep for `compare_digest`
- The spec's audience matches who actually uses rules.culture.dev today
  - instruction: operator review
- The before-state statements are each backed by a recorded scope entry
  - instruction: devague scope --list
- The culture-nodes bridges named are in fact stopped and their roles are covered by this frame's surfaces
  - instruction: operator confirms bridge status
- The demo walkthrough is executed live, not only in tests
  - instruction: docs/demo.md re-run with outputs recorded
- This signal is checked and its evidence filed at validate-delivery time
  - instruction: devague evidence file with run ids / CI links
- This signal is checked and its evidence filed at validate-delivery time
  - instruction: devague evidence file with run ids / CI links
- This signal is checked and its evidence filed at validate-delivery time
  - instruction: devague evidence file with run ids / CI links
- This signal is checked and its evidence filed at validate-delivery time
  - instruction: devague evidence file with run ids / CI links
- This signal is checked and its evidence filed at validate-delivery time
  - instruction: devague evidence file with run ids / CI links
- A payload containing shell metacharacters reaches the command only as one typed arg; a message containing @everyone is posted with mentions suppressed
  - instruction: tests/actors/`test_code.py` + `test_discord_message.py`

## Success signals

- On rules.culture.dev, a GitHub PR opened on an allow-listed repo produces exactly 1 github.pr.opened event and exactly 1 App-authored comment from the rule that matches it, within 60 s; the same holds for a jira.issue.updated -> jira.comment and a discord.message.created -> Discord reply
  - instruction: live check on spark against a test repo/project/channel; record run ids as evidence
- An event rule with no params.type is refused on save (HTTP 422, CLI exit 1) and 0 typeless event triggers remain in the live rules collection after migration
  - instruction: pytest for validation + a store query on the deployed DB
- A schedule rule placed on thor with cron '\*/5 \* \* \* \*' fires exactly 1 run per slot over 1 hour (12 runs), all on thor, including across a node restart
  - instruction: multihost test with fake clock + live observation on thor
- A workflow with 2 typed inputs is run from the Workflows tab input form with 0 rules referencing it, and its outputs render in place; Playwright covers it and CI's web job stays green
  - instruction: web e2e spec workflows-run.spec.ts
- Every issue #7 item is closed with a test or doc change linked, and the full suite (pytest -n auto, vitest, Playwright) and SonarCloud quality gate pass on the PR
  - instruction: gh issue view 7 checklist + CI

## Scope / boundaries

- chaining live() needs no change: a `must_after` predecessor whose predecessor failed resolves to `predecessor_failed`; only a unit test is added
- The Access 401 for a malformed token already holds; only a pinning test is added
- `culture_rules` runtime keeps zero third-party dependencies: outbound calls use urllib or argv CLIs (gh, culture, discord), secrets via grant, anything else behind an optional extra
- `culture_rules` runtime stays dependency-free: GitHub/Jira webhook HMAC checks use stdlib hmac/hashlib; the Discord Gateway client and GitHub App RS256 JWT signing live behind optional extras (e.g. discord, github) imported lazily
- External event data is untrusted: it reaches commands only as typed CodeRunner args (never spliced into argv strings), and message/comment actions neutralize mass mentions (@everyone, @here) by default

## Non-goals

- climate, sensibo and reTerminal app actors are later; this frame only makes them possible as server-actor commands

## Assumptions

- Events need not flow over MQTT to reach other hosts: the events collection lives in the shared replica set, so an ingest path on spark (webhook receiver or poller) is visible to every node's trigger consumers
- Secrets come from grant: the new culture-rules GitHub App's ID, installation ID, private key and webhook secret (new entries), `JIRA_SERVICE_ACCOUNT_TOKEN`; a Discord bot token and a Jira webhook token have no grant entry yet; the operator adds all new entries

## Scope exploration

- `s1` — `culture_rules/engine/matching.py + model/validate.py`: `trigger_matches`: wanted=params.get('type'); wanted is None matches all; `_check_trigger` (validate.py:311) only checks kind non-empty; Trigger.kind is a free string (model/rule.py:15-20)
  - seeds: `c3`
- `s2` — `web/src/rules/Forms.tsx + routes/rules-view.ts`: NewRuleForm (Forms.tsx:112-160) and RuleEditForm write only params.label from free text; `TRIGGER_KINDS`=\[event,schedule,manual\]; triggerLabel already reads params.event; Rules tab is HTML stage list, not React Flow
  - seeds: `c4`
- `s3` — `culture_rules/node/daemon.py run_once + node/firing.py`: grep finds no cron/schedule evaluator (only ops/backup.py); Node.`run_once` stages start/beat/ingest/poll/`start_fired`/redeliver/drive/report at 1s idle; firing has placed (triggers@host) and shared consumers, `_mine` resolves placement; a `_schedule_tick` stage can synthesize kind=schedule events deduped by rule/slot marker
  - seeds: `c5`
- `s4` — `culture_rules/machines/probe.py + model/condition.py`: probe.py is platform/health only (nvidia-smi, lsusb, load) with no-shell 5s `_run`; condition evaluator reads ctx.trigger dotted paths and compares numbers, so probe output as event data works unchanged; 'fires on change' needs new previous-value state
  - seeds: `c6`
- `s5` — `culture_rules/model/actor.py + actors/secrets.py`: ActorKind Literal + `ACTOR_KINDS` tuple (actor.py:41-44) is the only enum; declarations have no home (params or new fields with `schema_version`); secrets.py grant: regex, `assert_refs_only` enforced on save via auth/guards.py:88 gives the secret contract for free
  - seeds: `c7`
- `s6` — `culture_rules/auth/resolve.py + auth/access.py + server/app.py middleware`: AccessIdentity(subject,email,`common_name`,kind); Resolver holds no store and is sync per-request; the authenticate middleware (app.py:488-499) has the store; Principal carries only identity/kind/roles; nothing upserts actors today
  - seeds: `c8`
- `s7` — `culture_rules/actors/config.py + ~/.culture/server.yaml (culture repo)`: `parse_culture_yaml`/`load_all_from_repo` exist but have no callers; per-machine agent list is ~/.culture/server.yaml agents map suffix->dir; nicks are `<server>-<suffix>`; Actor.machine is the link; machines/enrol.py reads no culture.yaml
  - seeds: `c9`
- `s8` — `web/src/actors/ActorForm.tsx + actors/code.py`: ActorForm edits id/kind/harness/model/repo/machine/capabilities only; params (where runner commands live) never touched; CodeRunner takes commands {name:{argv,params,timeout}}, `bind_argv` coerces, refuses sh -c / python -c
  - seeds: `c10`
- `s9` — `culture_rules/engine/runs.py _context + node/runner.py default_ports + node/actors.py ActorRouter`: `_context` (runs.py:909-915) builds the action InvocationContext with actor=None, so only `action:<kind>` fallback ports apply; `default_ports` returns only action:noop; ActorRouter fallback already tries `action:<kind>` then action then \*; Action has no actor slot by design (action.py:17 "names it inside params")
  - seeds: `c11`
- `s10` — `web/src/rules/Forms.tsx action editing`: only rule.action.name is editable; new rules get placeholder {kind: mesh.message, name: Notify}; actionChips already parses trigger./workflow./vars./rule. refs
  - seeds: `c12`
- `s11` — `culture_rules/events/ingest.py + sibling culture-nodes adapters`: ingest writes {id, envelope, `received_at`, host} to the shared Mongo events collection; culture-nodes github/jira/notify bridges are outbound-only (human-inbox tracker polls only `github_pr_merged`), so inbound producers are new code; tracker poll/rate-limit design is citeable
  - seeds: `c13`
- `s12` — `events-cli broker + node.env on thor/orin/spark2`: events-cli broker is loopback 127.0.0.1:1883, remote access not built (events-cli #10); no `EVENTS_BROKER_HOST` in any deploy file or node-install.sh; nodes without broker run with no ingest (`open_event_source` -> None) but still read the shared events collection via EventTriggers change feed
  - seeds: `c14`
- `s13` — `culture_rules/engine/runs.py start + server/app.py POST /runs + cli/_commands`: RunStart is {`rule_id`, trigger, upstream}; run doc pins rule unconditionally (runs.py:595) and `_Plan`/`_dispatch` read plan.rule.action; `_check_workflow` maps inputs from rule.workflow.inputs; no runs start or workflows run verb; adding a Verb to the registry auto-adds CLI+MCP and tests/`test_surface_parity.py` checks routes against api/openapi.json
  - seeds: `c15`
- `s14` — `web/src/routes/Workflows.tsx Run + useOverlaidRun`: Run (~517) needs a rule using the workflow, sends no inputs; polling, runOverlay step badges and litEdges exist; RunDoc in web/src/api/workflows.ts has no outputs field
  - seeds: `c16`
- `s15` — `web/src/workflows/StepEditor.tsx + PlacementEditor.tsx`: StepEditor (dialog) has name, kind, ports, `max_iterations`; config, `timeout_s`, retry, description, required absent; Step model has all of them (model/workflow.py, RetryPolicy in common.py:55)
  - seeds: `c17`
- `s16` — `web/src/workflows/Canvas.tsx + nodes.tsx`: io nodes set selectable:false (Canvas.tsx:134, 220) and handlers guard `INPUTS_NODE`/`OUTPUTS_NODE`; no editor for inputs/outputs/variables/description exists; edges to `OUTPUTS_NODE` handles already parse
  - seeds: `c18`
- `s17` — `web/src/api/client.ts ApiError + server/app.py _envelope`: envelope is {error:{code,message,errors\[{path,code,message}\]}} with stable codes; RunError codes are a flat vocabulary; `_check_ports` errors have no path; UI shows raw message(err) strings in notice--error and `Not wired: <reason>`; no suggested-fix field, so the fix table is UI-side keyed on codes
  - seeds: `c19`
- `s18` — `server/app.py _register_kind + web/src/api/workflows.ts`: POST /{kind}/{id}/purge and GET ?`include_deleted` exist in API, CLI and MCP; web client has neither purge nor `include_deleted`: UI-only gap
  - seeds: `c20`
- `s19` — `web/src/workflows/WorkflowList.tsx`: WorkflowList mirrors RuleList with MachineDot, Link name, Switch, New button and tests; wired at Workflows.tsx ~910
  - seeds: `c21`
- `s20` — `issue #7 items, verified in culture_rules/ and web/`: verified: `_parse_fan_in_cursor` raises on legacy int cursor (`events_cli_adapter.py`:197-210); `_machine_online` fail-open untested (runs.py:818); FeedConsumer.`_fire` txn per finished run (node/chain.py:100); `OFFLINE_AFTER_S` fixed (heartbeat.py:33-35) vs scaled takeover (daemon.py:178); history scans whole runs collection (app.py:579) and Memory store matches top-level only; Switch has no disabled (stages.tsx:139); Canvas maxY+200 hard-coded; cache rule absent from docs/operations/rules-culture-dev.md; no deploy/node installer
  - seeds: `c22`
- `s21` — `culture_rules/engine/chaining.py live()`: refuted: live() recurses via adjust/`_refine`, which turns blocked-with-dead-predecessor into `predecessor_failed`; covered by tests/node/`test_chain.py`:157
  - seeds: `c23`
- `s22` — `culture_rules/auth/access.py _identity_of`: VerificationError(malformed) subclasses Unauthenticated (401); middleware catches AuthError and returns the envelope; history 404 for unknown rule also already exists (tests/server/`test_rule_history.py`:77)
  - seeds: `c24`
- `s23` — `CLAUDE.md packaging rule + client/http.py + node/runner.py MeshPoster`: client/http.py and access.py use urllib; MeshPoster runs culture channel message as argv; no Discord/Jira/gh code exists
  - seeds: `c25`
- `s24` — `sibling cultureflare remote-login`: remote-login assumes one hostname per Access app with a single allow-policy (README:195); --no-access provisions tunnel+DNS only, leaving the backend to verify HMAC/signatures
  - seeds: `c26` (rejected)
- `s25` — `sibling climate-cli, sensibo-cli, reterminal-cli`: climate weather latest/stats, sensibo read/set, reterminal display/board are argv CLIs registrable as runner commands; reterminal board actions could be an event source later
  - seeds: `c27`
- `s26` — `sibling culture repo (supervisor webhooks, agentirc events)`: server.yaml webhooks list includes `agent_complete`; agentirc events (agent.connect/disconnect, room.\*) use dotted types compatible with events-cli `_TYPE_RE`; no MQTT/events bridge exists in culture
  - seeds: `c9`
- `s27` — `sibling culture-nodes internal/api/{githubwebhook,jirawebhook}.go + docs/operations/nodes-culture-dev.md`: nodes.culture.dev mounts POST /v1alpha1/webhooks/{jira,github} behind a path-scoped Access Bypass (Include Everyone, exact path, nodes-culture-dev.md:315-343); GitHub verifies X-Hub-Signature-256 HMAC with constant-time compare and requires X-GitHub-Delivery; Jira accepts X-Hub-Signature HMAC or ?token= URL token compared by sha256+ConstantTimeCompare; 2 MiB body limit
  - seeds: `c32`
- `s28` — `culture-nodes githubwebhook.go verify + CLAUDE.md zero-dependency rule`: GitHub/Jira HMAC-SHA256 is stdlib-implementable; Discord interactions use Ed25519 and GitHub App installation tokens need RS256 JWT, neither in the Python stdlib
  - seeds: `c33`
- `s29` — `grant list on spark`: grant holds `CULTURE_NODES_GITHUB_APP_WEBHOOK_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `JIRA_SERVICE_ACCOUNT_TOKEN`; no `DISCORD_`\* bot token or public key, no Jira webhook secret/token
  - seeds: `c34`
- `s30` — `sibling culture-nodes Discord code`: Discord appears only as outbound (notify bridge posts to a webhook URL; humanfanout discord.post); no inbound receiver exists. Discord sends no outgoing webhooks for messages: message events need the Gateway websocket (bot token), the only HTTP push is the Ed25519-signed Interactions endpoint (slash commands, buttons)
- `s31` — `sibling discord-bot-cli + culture_rules/node/daemon.py`: discord-bot-cli is one-shot (`DISCORD_BOT_TOKEN`, discord.py extra; channel messages, message post/reply/react, thread) with no gateway daemon; node daemon has a beat thread and `run_once` stages but no long-lived per-actor connection; engine/leasekeeper.py already provides leases
  - seeds: `c35`
- `s32` — `sibling culture-nodes adapters/github + grant list`: culture-nodes github bridge posts comments via REST with `GITHUB_TOKEN` and an exact-repo allowlist; grant holds `GITHUB_APP_PRIVATE_KEY` and `CULTURE_NODES_GITHUB_APP_WEBHOOK_SECRET`; RS256 is not in the Python stdlib
  - seeds: `c36`
- `s33` — `challenge pass / security lens: culture_rules/server/app.py _install_auth`: authenticate middleware runs resolver.resolve(headers) for every request with no exempt path (app.py:487-499); only /health is a plain route; a bypassed webhook request would get 401 without an explicit exemption
  - seeds: `c46`
- `s34` — `challenge pass / failure-mode lens: c35/c36 + sibling culture-nodes jirawebhook.go`: the App comments and bot replies proposed in c36/c35 produce `issue_comment` / message events the same receivers ingest; culture-nodes Jira receiver tracks botAccountID and withholds self-authored origins (jirawebhook.go:63-68), no equivalent guard exists in `culture_rules`
  - seeds: `c47`
- `s35` — `challenge pass / security lens: c11 http.call + scripts/scan-secrets.py`: http.call from a rule is an SSRF vector into the tailnet (mongod 100.127.105.72:27028, LAN API 127.0.0.1:8791); CLAUDE.md says scan-secrets rejects non-localhost endpoints in committed files
  - seeds: `c48`
- `s36` — `challenge pass / security lens: culture_rules/actors/code.py bind_argv`: `bind_argv` type-coerces named args into a fixed argv template and `inline_eval_reason` refuses sh -c/python -c; webhook payloads (PR titles, Discord text) come from outside contributors
  - seeds: `c49`
- `s37` — `challenge pass / migration lens: node/firing.py rule loading`: firing.py:286,311,395 load actors/rules/workflows with strict=False, so old nodes tolerate new kinds but keep the old typeless match-all; probe: Actor.`from_dict`(kind='app') loads without error
  - seeds: `c50`
- `s38` — `challenge pass / lifecycle lens: c5 schedule trigger`: c5/h4 fix dedupe and no backfill but never state the clock; four hosts may differ in local TZ
  - seeds: `c51`
- `s39` — `challenge pass / observability lens: server/status.py + ops/health.py`: health and Statistics cover machines and runs only; nothing would show a GitHub redelivery storm or a dead gateway
  - seeds: `c52`
- `s40` — `challenge pass / containment lens: actor enable switch + POST /controls/pause`: enable/disable exists per actor and engine pause exists, but today an actor's enabled flag only gates adapter lookup in ActorRouter; ingest has no actor link yet
  - seeds: `c53`
- `s41` — `challenge pass / failure-mode lens: sibling culture-nodes jirawebhook.go`: culture-nodes Jira receiver extracts keys then fetchJiraIssue for each (jirawebhook.go:56-60); its GitHub receiver writes the fact and returns 201
  - seeds: `c54`
- `s42` — `challenge pass / concurrency lens: schedule slots, gateway lease, human-actor creation`: covered by confirmed h4 (slot dedupe in a transaction), h25 (single gateway holder by lease) and h7 (no duplicate actor on concurrent first sign-in); residual: lease takeover is fail-open on unreadable heartbeat (#7, c22), which could briefly allow two gateway holders; duplicate events are absorbed by message-id dedupe
- `s43` — `challenge pass / adjacent-systems lens: nodes.culture.dev`: culture-nodes still documents a live Jira system webhook and the GitHub App webhook on nodes.culture.dev; moving surfaces to rules.culture.dev must repoint, not duplicate, them; not examined: whether nodes.culture.dev is still serving
- `s44` — `challenge pass / unexamined surfaces`: not examined: the live rules collection (token refused, lapse l1); Discord developer-portal settings; GitHub App settings page; Jira admin webhook list; Cloudflare Access app policies for rules.culture.dev

## Decisions

- The left-pane workflow list is already shipped on main and is out of this frame
- Direct workflow runs pin a synthetic ad-hoc rule (manual trigger, workflow ref carrying the given inputs, noop action) into the run doc, so executor, history and filters stay unchanged
- First-cut surfaces and actions: GitHub, Discord and Jira (events in, comment/message actions out), plus machine.command and http.call actions with schedule and probe triggers
- A human actor created on Access sign-in has id = slugged email; asks stay in the asks collection and the editor inbox (delivery to connected surfaces is later)
- GitHub uses a new, dedicated culture-rules GitHub App whose webhook points at rules.culture.dev; its App ID, installation ID, private key and webhook secret are new grant entries; culture-nodes' App and nodes.culture.dev are left untouched
- Rollout rule: upgrade all four engine nodes (spark2 from the offline wheelhouse) before enabling new trigger or action kinds; doctor warns on node version skew

## Hard questions

- Human actor identity: what is the actor id for an Access sign-in (email, slugged email, or Access sub), and does the first cut deliver asks to connected surfaces (Discord DM/GitHub) or keep the existing asks collection + editor inbox? (resolved: id = slugged email; asks stay in the asks collection + editor inbox (user, see c30))
- First-cut action kinds: all five (message, github.comment, jira.comment, http.call, machine.command), or a smaller set first (machine.command + message + github.comment)? (resolved: All of github.comment, Discord message, jira.comment, http.call, machine.command (user, see c29))
- Ingest per surface for the first cut: pollers placed on spark (no public endpoint; GitHub/Jira/Discord all pollable), or webhook receivers on a separate self-authenticating hostname (lower latency, needs cultureflare --no-access + HMAC)? And which surfaces ship first? (resolved: Webhooks for GitHub and Jira; Discord via a Gateway bot listener (user, see c31, q5))
- Direct workflow run: pin a synthetic ad-hoc rule into the run doc (small change, history readers keep working), or make rule optional in the run doc and teach `_Plan`/`_dispatch`/history to tolerate it? (resolved: Synthetic ad-hoc rule pinned into the run doc (user, see c28))
- Discord inbound: Discord sends no message webhooks. Accept the Interactions endpoint (slash commands/buttons, Ed25519-signed, needs an optional crypto extra) as the 'webhook', or run a Gateway bot listener (needs a bot token, a long-lived connection, not a webhook) for discord.message.created? (resolved: Discord Gateway bot listener: a bot is more powerful than the Interactions endpoint (user))
- github.comment identity: post as the GitHub App (`GITHUB_APP_PRIVATE_KEY`, RS256 JWT -> installation token, needs a crypto extra or the gh CLI's app support) or through the operator's authenticated gh CLI on the placed machine (posts as the user)? (resolved: github.comment posts as the GitHub App (user))

## Open parks

- [unknown_nonblocking] Agent activity as an event source: culture's supervisor webhooks (`agent_complete` etc., ~/.culture/server.yaml) POST to a URL and agentirc emits IRCv3-tagged system events, but nothing bridges either into the events collection; the bridge's home (culture-rules, culture, or a bot) is undecided
- [follow_up] Discord prerequisites outside the repo: a bot application with the privileged `MESSAGE_CONTENT` intent enabled, invited to the guild, and its token stored in grant (none exists today)
- [follow_up] `RULES_CULTURE_DEV_CLI_TOKEN` in grant is refused by the live API (401 `bad_token`): the admin CLI token was revoked or rotated without updating grant
