# Model actors in the CLI and MCP

> culture-rules enrols each local model as its own actor from the gateways' /v1/models, lists where an actor is used, repoints a workflow from one actor to another and refuses to disable an actor still in use: one CLI verb and one MCP tool each, dry-run by default

## Audience

- The operator and the assisting agent who run culture-rules from the CLI or MCP and wire local models into workflows

## Before → After

- Before: Pointing a workflow step at a local model means hand-building actor JSON, editing the workflow JSON with its revision, and disabling the old actor: a multi-step script in the scratchpad (model-actors.sh, 2026-10-19), and actors update even needs a positional ID that the operator's first command missed
- After: Three verb calls replace the script: actors enrol-models makes or updates one agent actor per matching model, workflows repoint-actor moves a workflow's steps to it with `expected_revision`, and actors disable refuses while an enabled workflow still names the actor; actors references shows where an actor is used

## Why it matters

- Every lobes lane (cortex-spark, cortex-spark2, worker-thor, hand-spark2) becomes a ready actor any workflow can use, and moving work between lanes is a reviewed dry run instead of hand-edited JSON

## Requirements

- actors enrol-models takes repeatable --gateway MACHINE, each a key of the `model_gateways` variable (machine -> URL), in priority order, and --api-key grant:NAME (default grant:`LOBES_GATEWAY_API_KEY`); the server resolves the grant only after the gateway is found in `model_gateways`, GETs URL/v1/models, and the actor stores `base_url` = URL and params.`api_key` as the grant reference, never the value
  - honesty: A test asserts the bearer key is resolved on the server from the grant reference only, never appears in a response, --json output or the stored actor, and the stored params.`api_key` is the grant reference
  - honesty: Tests: --gateway with a name missing from `model_gateways` is refused with exit 1 naming the known gateways; a `model_gateways` entry change is picked up on the next call; the site's panel offers the `model_gateways` entries as its gateway choices
- actors enrol-models enrols only models matching a repeatable --match glob (no default, so embedders, rerankers and Hugging Face names like unsloth/Qwen3.8-27B-NVFP4 are never enrolled by accident); the actor id is the model id, and a model id that is not a valid actor id is refused
  - honesty: A test feeding the live model list (cortex-\*, embedder-\*, reranker-\*, unsloth/...) plans only the --match hits, and a match with no valid id is refused with exit 1
- Each enrolled actor is kind agent, on the gateway's MACHINE, with params `base_url` = URL, model = the model id, `concurrency_pool` from c3, `max_concurrency` 1; an existing actor with that id is updated only in those fields, a matching one is unchanged, and the plan shows create/update/unchanged per actor
  - honesty: Running enrol-models twice in a row plans 0 changes the second time; an actor edited by hand keeps every field outside the enrolled ones
- actors references ID lists every workflow step (loop bodies included) whose action params.actor is ID, and every rule whose action names it, with workflow, step and enabled state; read-only
  - honesty: A test with a step inside a `for_each` body and a rule action naming the actor lists both
- workflows repoint-actor WORKFLOW --from A --to B rewrites every params.actor == A in the workflow, loop bodies included, and PUTs with `expected_revision`; it refuses when B is missing, disabled or deleted, or when no step names A; it never edits description or notes, and lists steps whose notes mention A as warnings
  - honesty: Tests: a stale revision returns the 409 `revision_conflict` as a CliError; description and notes are byte-identical after a repoint; missing or disabled --to refuses with exit 1
- actors disable ID refuses, in the dry run too, while an enabled workflow or rule still references the actor, naming them; --force disables anyway
  - honesty: A test: disable is refused while an enabled workflow references the actor, allowed once it is repointed, allowed with --force; a disabled workflow does not block
- Each new verb follows the CLI contract (dry run by default, --apply, --json, exit 0/1/2, CliError, explain catalog entry and learn mention) as a thin wrapper over its endpoint; MCP gets each as a tool through the registry, and the parity test covers them
  - honesty: tests/`test_mcp_parity` (or its equivalent) passes with the new verbs, and uv run teken cli doctor . --strict stays green
- The logic lives in the server, so the CLI, MCP and site share one implementation: POST /actors/enrol-models (`dry_run` plan, then apply), GET /actors/{id}/references, POST /workflows/{id}/repoint-actor (`dry_run`, `expected_revision`), and POST /actors/{id}/disable refusing 409 `actor_in_use` unless force; api/openapi.json pins them, and a server + node rollout ships them
  - honesty: tests/server/`test_openapi_contract.py` passes with the four endpoints pinned in api/openapi.json, and the web client's types compile against them
- The committed newspaper bundle (docs/rules/newspaper) moves its summary step to cortex-spark2 and drops actors/newspaper-llm.json for a cortex-spark2 actor; docs/operations/newspaper.md and the bundle tests follow
  - honesty: tests/rules/`test_newspaper_bundle.py` and `test_newspaper_flow.py` pass with the bundle on cortex-spark2, and grep finds no newspaper-llm under docs/rules
- The Actors page gains: an 'Add models from a gateway' panel (gateways, match, pool overrides; shows the server's plan, then applies), a 'Used by' list on an actor (from references, linking each workflow), a 'Move to another actor' action on that list (repoint, with the revision-conflict Reload), and Disable showing the in-use refusal with the workflows named
  - honesty: vitest and Playwright cover the panel's plan-then-apply, the Used by list, a repoint with a stale revision showing Reload, and the refused Disable; the webglass agent-state gate stays green
  - honesty: Screenshots of the Add models panel, the Used by list, the Move action and the refused Disable, in light and dark, are sent to the operator and approved before merge
- One enabled actor per model: creating or updating an enabled agent actor, or enabling one, whose params.model is already held by another enabled agent actor is refused (409 `model_taken`, naming the holder and saying to disable it first) in POST/PUT /actors, POST /actors/{id}/enable and enrol-models' plan; a disabled actor holds nothing; at runtime llm.complete refuses a step whose actor's model is also held by another enabled actor (`model_taken`)
  - honesty: Tests: create, update and enable of a second enabled actor for cortex-spark2 each return 409 `model_taken` naming the holder; the same actor created disabled is allowed and enables once the holder is disabled; enrol-models plans the refusal; a store seeded with two enabled holders makes llm.complete fail with `model_taken`
- enrol-models never sends a resolved secret to an arbitrary URL: it is admin-only (`culture_rules`/auth/policy.py defaults an unmatched POST to admin; keep it off `_EDITOR_PATTERNS`), and each --gateway must name an entry in an allow-listed set of model gateways rather than a free URL, so an editor cannot point the server, with grant:`ANY_SECRET`, at a host they control
  - honesty: Tests: an editor-role call to POST /actors/enrol-models is 403; a gateway URL outside the allowlist is refused before any secret is resolved (the resolver is never called); the bearer header is only ever sent to an allow-listed host
- The one-enabled-actor-per-model rule (c26) covers only endpoint actors, those whose params carry `base_url` (the llm.complete shape); bridge agents with a harness (qwen-fixer and qwen-reviewer both hold model cortex, enabled, live) are outside it, so the rule never refuses an edit to them
  - honesty: A test with qwen-fixer and qwen-reviewer from the live actors snapshot (same model cortex, no `base_url`, both enabled) lets PUT and enable on either succeed; the live store passes a dry check of the rule before rollout (0 conflicts besides newspaper-llm)
- actors references and the disable guard also count placement.actor on steps and rules (`culture_rules`/model/placement.py: a step or rule placed on an actor runs on its machine); repoint-actor rewrites only action params.actor and reports placement references to A as warnings, never moving where a step runs
  - honesty: A test: a step placed on actor A shows in references with kind placement, blocks disable, and is listed but unchanged by repoint-actor
- repoint-actor on a workflow whose v1 or v2 digest is in the trusted set warns in the dry run that the repoint drops its trust (`culture_rules`/actors/trusted.py: `workflow_digest_v2` keeps step params, so a changed params.actor changes the digest), and refuses without --force
  - honesty: A test: repointing a trusted workflow fixture is refused without --force with the digest named, and with --force the new digest is shown so a trust update can follow
- Version skew fails clearly: a new CLI against a server without the endpoints exits 2 with a 'server too old, needs culture-rules >= the new version' hint (as rollout's `no_rollouts_endpoints` does), and the site hides the new controls when the endpoints answer 404
  - honesty: A test: each new verb against a 404 endpoint raises CliError exit 2 with the version hint; the web test with the endpoint mocked 404 shows no Add models panel
- Every write the new endpoints make goes through the server's Lifecycle (audit with the caller), one audit entry per actor created or updated and per workflow repointed, and the response lists the resulting ids and revisions
  - honesty: A test reads the audit log after an enrol-models apply and a repoint and finds one entry per change naming the caller
- enrol-models refuses (in the plan) a model whose id is already the id of a non-agent actor or of an agent actor without `base_url`, rather than overwriting it into an endpoint actor
  - honesty: A test: a model id equal to an existing app actor's id plans a refusal and leaves the actor byte-identical

## Honesty conditions

- culture-rules learn and the MCP tool list both show actors enrol-models, actors references and workflows repoint-actor, and actors disable carries the reference guard
- The verbs are documented where the operator looks: explain catalog, learn, and docs/operations (a model-actors section)
- The scratchpad script model-actors.sh needed build/create/point/retire/check steps and hand-built JSON for one repoint (2026-10-19)
- An end-to-end test against MemoryStore: enrol two models from a fake /v1/models, repoint a workflow, then actors disable on the old actor succeeds only after the repoint
- One enrol-models run against the live lobes gateway with --match 'cortex-\*' plans exactly cortex-spark and cortex-spark2, and nothing else
- The 3 live commands are recorded with their output, and the next issue's run record shows the summary step's actor as cortex-spark2 with 4 summaries
- git diff main under `culture_rules`/node and `culture_rules`/engine shows only the `model_taken` check in node/actions/llm.py and its tests

## Success signals

- On the live store, 3 commands with no hand-written JSON (enrol-models, repoint-actor daily-newspaper, actors disable newspaper-llm) leave daily-newspaper's 1 llm.complete step on cortex-spark2, and the next 05:00 issue has 4 of 4 section summaries written by cortex-spark2

## Scope / boundaries

- No change to the engine or to any action kind, except one added refusal in llm.complete (`model_taken`, c26); the new endpoints only read and write actors and workflows through the store

## Non-goals

- llm.complete's locked brief stays as it is; moving the brief into step config is the separate newsletter work (review table row 1)
- No automatic disabling or deletion of actors whose model has left a gateway; enrol-models only creates and updates, and reports models it no longer sees

## Assumptions

- llm.complete falls back to context.actor when params.actor is absent (node/actions/llm.py:3); references can only see explicit names, so an implicit use is invisible to references, repoint and the disable guard

## Scope exploration

- `s1` — `lobes gateways GET /v1/models (spark :8001, spark2 :8000)`: Both gateways list the same lanes plus embedders, rerankers and HF names (cortex-spark, cortex-spark2, worker-thor, hand-spark2, embedder-\*, reranker-\*, unsloth/Qwen3.8-27B-NVFP4), so enrolment needs a match filter and a collision rule
  - seeds: `c11`, `q1` (question, resolved)
- `s2` — `culture_rules/cli/_commands/actors.py (enrol-agents)`: enrol-agents is the precedent: GET /actors, `plan_enrolment` diffs desired vs existing into create/update/disable changes, applies through POST/PUT only with --apply
  - seeds: `c12`, `c17`
- `s3` — `culture_rules/mcp/tools.py`: One MCP tool per registry verb (`noun_verb`, `params_schema`), so new CLI verbs become MCP tools with no extra code
  - seeds: `c16`
- `s4` — `culture_rules/cli/_nounlib.py definition_verbs`: actors update and disable take a positional ID; workflows update sends `expected_revision` (`_REVISIONED`); disable has no reference guard today
  - seeds: `c7`, `c14`, `c15`
- `s5` — `docs/rules/newspaper/workflows/daily-newspaper.json:610`: The one llm.complete reference is at summarise.body\[summary\].config.action.params.actor = newspaper-llm; the summarise step's notes name newspaper-llm
  - seeds: `c14`, `c20`
- `s6` — `culture_rules/node/actions/llm.py:1-30`: llm.complete needs an agent actor whose params carry `base_url` and model; the call runs on the actor's machine, so `base_url` must be reachable from there
  - seeds: `c12`, `q2` (question, resolved)
- `s7` — `newspaper-llm live actor (actor-llm-live.json)`: Today's actor stores `base_url` <http://127.0.0.1:8001> (spark loopback), `api_key` grant:`LOBES_GATEWAY_API_KEY`, pool qwen-spark2, machine spark
  - seeds: `c10`, `q2` (question, resolved)
- `s8` — `web/src/actors (ActorsBoard.tsx, ActorForm.tsx, AppConfigForm.tsx)`: The Actors page edits one actor at a time (ActorForm, AppConfigForm for app surfaces, CommandsEditor); it has no bulk-add, no references view and no repoint today
  - seeds: `c22`
- `s9` — `culture_rules/server/app.py:975-1000 (discord targets)`: The server already resolves a grant: reference per request (secrets.resolve, injectable `resolve_secret` for tests) to call an external service for an actor: the precedent for reading /v1/models server-side
  - seeds: `c10`, `c17`
- `s10` — `web/src (rollouts)`: Rollouts have server endpoints (/rollouts\*) but no site page, so the rollout precedent covers CLI+MCP over endpoints; the site page is new ground
  - seeds: `c17`, `c22`
- `s11` — `challenge pass / security lens: culture_rules/server/app.py:975-1000 vs the new enrol endpoint`: discord targets resolves the stored bot token and only calls Discord's fixed host; enrol-models would resolve a caller-named grant and send it to a caller-named URL: secret exfiltration unless the URL is allow-listed and the route admin-only
  - seeds: `c29`, `q5` (question, resolved)
- `s12` — `challenge pass / migration lens: live actors (actors-live.json)`: qwen-fixer and qwen-reviewer are both enabled agent actors with params.model cortex and no `base_url`; an unscoped c26 would refuse any later edit or enable of either
  - seeds: `c30`
- `s13` — `challenge pass / adjacent-systems lens: culture_rules/model/placement.py`: Placement has an actor form (runs where the actor lives); c13/c15 only named params.actor, so a placement reference would be invisible and disable would strand it
  - seeds: `c31`
- `s14` — `challenge pass / adjacent-systems lens: culture_rules/actors/trusted.py:194-213`: The v2 digest drops only prose (description, notes); step params stay in it, so any repoint breaks a pinned workflow's trust until its digest is re-pinned
  - seeds: `c32`
- `s15` — `challenge pass / operations lens: culture_rules/cli/_commands/rollout.py:225-262`: rollout already detects a server without /rollouts (`no_rollouts_endpoints`, `_rollouts_endpoints`) and refuses clearly; the new verbs need the same guard. The runtime `model_taken` check only runs on nodes at the new version, so mixed-version nodes skip it during a rollout
  - seeds: `c33`
- `s16` — `challenge pass / concurrency lens: store writes for actors and workflows`: Workflows have revision CAS (`expected_revision`); actors have none, so model uniqueness and the disable guard are check-then-write; residual risk parked, runtime check is the backstop
- `s17` — `challenge pass / observability lens: culture_rules/server/app.py:729 (Lifecycle(store, audit))`: Writes go through Lifecycle with an audit; the new composite endpoints must keep one audited write per object, not a bulk write that bypasses it
  - seeds: `c34`
- `s18` — `challenge pass / unstated-assumptions lens: culture_rules/node/actions/llm.py:3-4`: params.actor is required but falls back to context.actor; implicit uses cannot be found statically
  - seeds: `c35`
- `s19` — `challenge pass / reversibility lens: the three live commands`: Clean pass: each step reverses with existing verbs (repoint back, actors disable/enable, newspaper-llm stays disabled not deleted); residual: a re-enable of newspaper-llm is refused by `model_taken` until cortex-spark2 is disabled, which is the intended rule

## Decisions

- actors enrol-models reads each gateway's /v1/models; it takes more than one gateway URL, so the lobes gateway or a machine's own gateway can serve as the source (operator, 2026-10-19)
- A model actor's concurrency pool defaults to the model name; an override maps a model to an existing pool, e.g. cortex-spark2=qwen-spark2 (operator approved, 2026-10-19)
- One actor per local model lane (cortex-spark, cortex-spark2), never a task-named actor; the step stays work-agnostic and its configuration specialises it (operator, 2026-10-19)
- Repeated script runs against the store belong in the CLI and MCP, like rollouts (operator, 2026-10-19)
- q1: the first --gateway listed wins; no second actor may hold the same model, tested at definition and at runtime (operator, 2026-10-19)
- q2: one URL per gateway serves both the server's /v1/models read and the actor's calls (operator, 2026-10-19)
- q3: the Actors page additions follow the existing Actors board style, reviewed from screenshots, no canvas design first (operator, 2026-10-19)
- q4: only an enabled agent actor holds its model; enabling a second actor for the same model is refused with an error naming the holder (operator, 2026-10-19)
- q5: the gateway allowlist is the shared variable `model_gateways` (machine -> URL, admin-written); --gateway names a machine from it, never a free URL (operator, 2026-10-19)

## Hard questions

- The URL serves two readers, the CLI host listing models and the actor's machine calling the model: is one URL enough (e.g. spark's actor on its LAN address 100.127.105.72:8001), or does each gateway take a separate stored `base_url` (e.g. the loopback 127.0.0.1:8001 the newspaper actor uses today)? (resolved: (a) one URL per gateway, read by the server and called from the actor's machine; spark=<http://127.0.0.1:8001> as today, a machine gateway on its LAN address (operator, 2026-10-19))
- The lobes gateways on spark and spark2 list the same lanes (both serve cortex-spark and cortex-spark2): when two --gateway entries serve one model, which gateway and machine does its actor get - the first --gateway listed, a refusal unless --prefer names one, or one actor per gateway with a suffixed id? (resolved: (a) the first --gateway listed wins; other gateways serving the model show as 'also served by'. No second actor for the same model: refused when defined and checked at runtime (operator, 2026-10-19))
- The design canvas is the editor's visual source of truth: is the Actors-page shape (a panel plus a Used by list on the actor) fine to build from the existing Actors board style, or should it get a canvas design first? (resolved: Build the panel and the Used by list in the existing Actors board style; screenshots go to the operator for review instead of a canvas design first (operator, 2026-10-19))
- Today newspaper-llm already holds cortex-spark2, so enrolling cortex-spark2 would hit the new check: should enrol-models take over (rename) an existing actor holding the model, or must the operator repoint and disable newspaper-llm first? (resolved: (a) only an enabled actor holds a model; enabling a second actor for a model already held is refused with an error naming the holder; the one-time move is actors disable newspaper-llm --force, enrol-models, repoint-actor (operator, 2026-10-19))
- Where does the gateway allowlist live: (a) a shared variable `model_gateways` (machine -> URL, admin-written, read as vars.`model_gateways`) so --gateway takes a machine name, (b) server config (a flag or env on the API unit), or (c) only the URL hosts of enrolled machines plus loopback? (resolved: (a) a shared variable `model_gateways` (machine -> URL), written by an admin and read as vars.`model_gateways`; --gateway takes a machine name from it (operator, 2026-10-19))

## Open parks

- [unknown_nonblocking] Two concurrent creates or enables for the same model can both pass the check-then-write (no unique index on params.model in MongoDB); the runtime llm.complete check catches the pair, but both stay enabled until someone disables one
- [unknown_nonblocking] A repoint into actor A racing actors disable A can leave a step on a disabled actor; the step then fails at runtime as today (llm.complete on a disabled actor)
