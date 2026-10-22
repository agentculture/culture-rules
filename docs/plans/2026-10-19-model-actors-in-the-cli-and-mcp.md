# Build Plan — Model actors in the CLI and MCP

slug: `model-actors-in-the-cli-and-mcp` · status: `exported` · from frame: `model-actors-in-the-cli-and-mcp`

> culture-rules enrols each local model as its own actor from the gateways' /v1/models, lists where an actor is used, repoints a workflow from one actor to another and refuses to disable an actor still in use: one CLI verb and one MCP tool each, dry-run by default

## Tasks

### t1 — Model enrolment planner (pure, stdlib): `culture_rules`/actors/`model_enrol.py`

- instruction: Model it on `culture_rules`/actors/`enrol_agents.py` (Plan, change records, `plan_enrolment`). No I/O: the caller passes the model lists. Reuse the actor id validation the model layer applies. Zero third-party imports.
- covers: c11, h7, c12, h8, c36, h29
- acceptance:
  - `plan_model_enrolment`(existing, gateways, match, pools, `api_key`) plans create/update/unchanged/refused per model from gateways given as ordered (machine, url, model ids); tests/actors/`test_model_enrol.py` feeds the live model list (cortex-\*, embedder-\*, reranker-\*, unsloth/...) and plans only --match hits
  - the first gateway listed wins and later ones appear as `also_served_by`; models an existing endpoint actor holds but no gateway lists are reported as unseen, never disabled
  - pool defaults to the model id with model=pool overrides; an update touches only kind-agnostic enrolled fields (machine, params.`base_url`, params.model, params.`concurrency_pool`, params.`max_concurrency`, params.`api_key`); a second run over the result plans 0 changes
  - an invalid actor id, or an id held by a non-agent actor or an agent actor without `base_url`, plans a refusal and the existing doc is untouched

### t2 — Actor references and repoint (pure, stdlib): `culture_rules`/actors/references.py

- instruction: Step shape: docs/rules/newspaper/workflows/daily-newspaper.json (summarise.body\[summary\].config.action.params.actor). Placement: `culture_rules`/model/placement.py. Rules' actions carry params.actor too. Work on plain dicts; return new dicts (copy.deepcopy), never mutate the input.
- covers: c13, h9, c31, h25
- acceptance:
  - `find_references`(`actor_id`, workflows, rules) returns {workflow|rule, id, step path, via: action|placement, enabled}, walking `for_each` bodies at any depth; tests/actors/`test_references.py` covers a loop-body step, a rule action and a placed step
  - `repoint_workflow`(doc, frm, to) returns (`new_doc`, changed step paths, warnings): it rewrites only config.action.params.actor == frm, leaves description and notes byte-identical, and warns for notes that mention frm and for placement.actor == frm (unchanged)
  - `repoint_workflow` with no matching step raises a typed error the server maps to 409/422

### t3 — Model-holder rule (pure, stdlib): `culture_rules`/actors/`model_rule.py`

- instruction: One small module both the server guards and node/actions/llm.py import, so the rule has one definition.
- covers: c30, h24
- acceptance:
  - `is_endpoint_actor`(doc) is true only for kind agent with a non-empty params.`base_url`; `model_holder`(actors, candidate) returns the id of another enabled, non-deleted endpoint actor with the same params.model, else None
  - tests/actors/`test_model_rule.py` uses the live snapshot shapes: qwen-fixer and qwen-reviewer (model cortex, no `base_url`, enabled) never conflict; newspaper-llm vs a new cortex-spark2 does; a disabled holder holds nothing

### t4 — Server routes: POST /actors/enrol-models, GET /actors/{id}/references, POST /workflows/{id}/repoint-actor in `culture_rules`/server/`model_actors.py`, registered from app.py

- instruction: Follow `_register_discord_targets` (app.py:975) for the resolver/transport injection; add one `_register_model_actors`(...) line in `create_app`. Reads of `model_gateways` use the Variables store. Keep the logic in t1/t2's modules; this file is routing, auth context and I/O.
- depends on: t1, t2
- covers: c10, h30, h31, c29, h23, c32, h26, c34, h28, c14, h10
- acceptance:
  - enrol-models takes {gateways: \[machine...\], match, pools, `api_key`, `dry_run`}; each gateway is looked up in the `model_gateways` variable first, an unknown one is refused with the known names, and the grant resolver is called only after that (a test asserts the resolver and transport are never called on refusal)
  - /v1/models is read through an injectable transport (like `discord_transport`) with the resolved bearer; the key never appears in a response or the stored actor; tests/server/`test_model_actors_api.py` covers dry run, apply and `model_gateways` changes taking effect on the next call
  - repoint-actor takes {from, to, `expected_revision`, force, `dry_run`}: a stale revision is 409 `revision_conflict`, a missing/disabled/deleted target or no match is refused, a workflow whose v1 or v2 digest is trusted (`culture_rules`/actors/trusted.py) is refused without force naming the digest, and with force the new digest is returned
  - every write goes through Lifecycle (one audit entry per actor created/updated and per workflow repointed, naming the caller); responses list ids and revisions

### t5 — Actor guards on the definition routes: `model_taken` on create/update/enable, `actor_in_use` on disable (`culture_rules`/server/`actor_guards.py`, called from `_register_kind`)

- instruction: Guards live in the server layer, not `culture_rules`/engine (boundary c23). Hook them in `_register_kind` only for kind == 'actors'. Depends on t4 only because both touch app.py; land after it.
- depends on: t2, t3, t4
- covers: c15, h11, c26, h22
- acceptance:
  - POST /actors, PUT /actors/{id} and POST /actors/{id}/enable return 409 `model_taken` naming the holder and saying to disable it first when `model_holder` finds one; creating the actor disabled is allowed and it enables once the holder is disabled
  - POST /actors/{id}/disable returns 409 `actor_in_use` listing the enabled workflows and rules that reference it (action or placement), disabled ones not counting; ?force=true disables anyway; tests/server/`test_actor_guards_api.py` covers each
  - existing tests in tests/server (`test_api.py`, `test_authz_matrix.py`) still pass unchanged

### t6 — Runtime `model_taken` refusal in llm.complete (`culture_rules`/node/actions/llm.py)

- instruction: Import `model_holder` from `culture_rules`/actors/`model_rule.py`. Check after `load_bound_actor` and before the HTTP call; the failure string follows the module's `'<code>: <detail>'` convention.
- depends on: t3
- covers: c23, h20
- acceptance:
  - llm.complete fails the step with `'model_taken: <holder>'` when another enabled endpoint actor holds its actor's model; tests/node (llm action tests) seed two holders in MemoryStore and assert the failure, and the existing llm tests pass unchanged
  - git diff main touches `culture_rules`/node only in node/actions/llm.py and `culture_rules`/engine not at all

### t7 — Newspaper bundle on cortex-spark2 (docs/rules/newspaper, docs/operations/newspaper.md, bundle tests)

- instruction: Only actor names and the technical notes field change; never edit description prose. Check scripts/scan-secrets.py still passes (loopback URL only).
- covers: c20, h14
- acceptance:
  - docs/rules/newspaper/actors/newspaper-llm.json is replaced by actors/cortex-spark2.json (agent, machine spark, `base_url` <http://127.0.0.1:8001>, model cortex-spark2, pool qwen-spark2, `max_concurrency` 1, `api_key` grant:`LOBES_GATEWAY_API_KEY`), and the summary step's params.actor is cortex-spark2
  - tests/rules/`test_newspaper_bundle.py`, `test_newspaper_flow.py` and the describe fixtures pass; grep -r newspaper-llm docs/rules returns nothing; docs/operations/newspaper.md names cortex-spark2

### t8 — Pin the contract and roles: api/openapi.json, `culture_rules`/auth/policy.py, tests/server/`test_authz_matrix.py`

- instruction: Regenerate the pinned schema the way the repo does it (see the contract test for the command); never hand-edit the paths. Policy: leave enrol-models out of `_EDITOR_PATTERNS` so it defaults to admin.
- depends on: t4, t5
- covers: c17, h17
- acceptance:
  - api/openapi.json carries the three new routes and the disable force parameter; tests/server/`test_openapi_contract.py` passes
  - the route matrix makes POST /actors/enrol-models admin (an editor gets 403), GET /actors/{id}/references viewer, POST /workflows/{id}/repoint-actor editor; `test_authz_matrix.py` asserts all three

### t9 — CLI verbs and MCP tools: actors enrol-models, actors references, actors disable --force, workflows repoint-actor (cli/`_commands`/actors.py, workflows.py, explain/catalog.py, learn)

- instruction: Thin wrappers over the t4/t5 endpoints via ctx.client.request; reuse `_nounlib`.write for dry-run/apply envelopes. actors disable keeps its positional ID and gains --force.
- depends on: t8
- covers: c16, h12, c1, h1, c33, h27
- acceptance:
  - each verb is dry-run by default, --apply commits, --json works, errors are CliError with exit 1 (user) or 2 (environment); actors enrol-models takes repeatable --gateway, --match, --pool model=pool and --api-key
  - against a server that 404s the new routes each verb exits 2 with a `'server too old: needs culture-rules >= <version>'` hint (as rollout's `no_rollouts_endpoints`)
  - explain catalog entries and a learn mention exist; the MCP parity test lists the four tools; uv run teken cli doctor . --strict is green

### t10 — Actors page: Add models from a gateway, Used by, Move to another actor, in-use Disable (web/src/actors, web/src/api/actors.ts)

- instruction: Existing Actors board style (ActorsBoard.tsx, ActorForm.tsx, actors.css); large targets, keyboard access, prefers-reduced-motion. Types come from api/openapi.json.
- depends on: t8
- covers: c22, h16, h21
- acceptance:
  - the Add models panel offers the `model_gateways` entries, takes match and pool overrides, shows the server's dry-run plan, then applies; an actor shows Used by (references, each linking its workflow) with a Move action that repoints and shows Reload on a stale revision; Disable shows the in-use refusal naming the workflows
  - the controls are hidden when the endpoints 404; vitest and Playwright cover each flow; typecheck, the webglass agent-state gate and the web CI job pass
  - screenshots of the four states in light and dark are sent to the operator and approved before merge

### t11 — Docs, harness files and version: docs/operations/model-actors.md, CLAUDE.md + QWEN.md + AGENTS.override.md + AGENTS.colleague.md in step, minor bump, end-to-end test

- instruction: Use the version-bump skill. Load-bearing phrases in the harness files stay (see CLAUDE.md, Identity and the four harnesses).
- depends on: t6, t7, t9, t10
- covers: c6, h2, c8, h4
- acceptance:
  - docs/operations/model-actors.md covers `model_gateways`, the three verbs, the model rule and the one-time newspaper move; the four harness prompt files describe the verbs consistently; markdownlint and harness-smoke pass
  - tests/`test_model_actors_e2e.py` (MemoryStore): enrol two models from a fake /v1/models, repoint a workflow, and actors disable on the old actor succeeds only after the repoint
  - version-bump minor (pyproject.toml, CHANGELOG.md) and uv.lock updated; the full suite and lint pass

### t12 — Live: `model_gateways` variable, rollout, the three commands, evidence

- instruction: Deploys and shared-resource writes go through the operator's shell (offer a step script); the admin token is injected with grant run --inject, never read.
- depends on: t11
- covers: c21, h15, c9, h5, c7, h3
- acceptance:
  - after the PR merges and the rollout verifies, the operator runs: variables set `model_gateways` {spark: <http://127.0.0.1:8001}>, actors disable newspaper-llm --force, actors enrol-models --gateway spark --match 'cortex-\*' --pool cortex-spark2=qwen-spark2 (dry run plans exactly cortex-spark and cortex-spark2), workflows repoint-actor daily-newspaper --from newspaper-llm --to cortex-spark2; outputs recorded as evidence
  - the next 05:00 issue's run record shows the summary step on cortex-spark2 with 4 of 4 summaries; the before-state evidence is the scratchpad model-actors.sh (5 steps, hand-built JSON)

## Risks

- [unknown_nonblocking] The guards live in the server routes (boundary c23 keeps them out of `culture_rules`/engine), so writes that bypass the API (rollout import's store path, migrations, the node) skip `model_taken`/`actor_in_use`; the llm.complete runtime check (t6) is the backstop (task t5)
- [unknown_nonblocking] Mixed-version window during the rollout: nodes not yet on the new version skip the runtime check, and the old site lacks the controls; run the live commands (t12) only after rollout verify (task t12)
- [unknown_nonblocking] Concurrent creates or enables for one model and a repoint racing a disable stay check-then-write (frame parks v1, v2); no unique index is added (task t5)
