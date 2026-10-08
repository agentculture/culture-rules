# Build Plan — pr-fixer rule

slug: `pr-fixer-rule` · status: `exported` · from frame: `pr-fixer-rule`

> A new PR in any AgentCulture repo is picked up by a fixer agent that drives it to green: SonarCloud gate, failing checks and unresolved review threads fixed on the PR branch, never merged

## Tasks

### t1 — cultureagent: lift the qwen (ACP) and codex bridges from culture-nodes into cultureagent with one shared core and a plain result schema

- instruction: Work in ../cultureagent on a branch. Copy adapters/qwen and adapters/codex from ../culture-nodes (cite, don't import). Factor the duplicated preflight/workspace/reap/preserve/liveness modules into one cultureagent/bridges/core package. Keep stdlib-only and the HTTP surface (POST /v1/invocations, cancel, GET /v1/capabilities, /healthz, callbacks). Replace the ledger 'proposed claim' with a plain result: status, summary, `head_before`, `head_after`, commits, `changed_files`, diffstat, `threads_addressed`. Worktrees are created from a given repo URL + head branch + head SHA. Follow cultureagent's own CLAUDE.md for version bump and PR.
- acceptance:
  - cultureagent ships cultureagent.bridges.qwen and cultureagent.bridges.codex with no third-party runtime dependencies (pyproject check)
  - an invocation with repo, `head_branch` and `head_sha` provisions a detached worktree at that SHA and the result reports `head_before` == `head_sha`
  - the bridge never pushes: tests assert no git push is ever spawned, and the result lists local commits only
  - the shared core module exists once; no byte-identical copies of preflight/workspace remain across bridges

### t2 — Webhook: map synchronize, `ready_for_review`, `check_suite`/`workflow_run` completed and `pull_request_review_comment`; enrich event data; `self_authored` applies to synchronize only

- instruction: Files: `culture_rules`/server/hooks/github.py (`_TYPES`, `_data`) and `culture_rules`/events/`hook_sink.py` (`self_authored`). Add types github.pr.synchronize, github.pr.ready, github.checks.`suite_completed`, github.checks.`workflow_completed`, github.`review_comment`.created. `_data` adds `head_sha`, `head_branch`, `head_repo`, `base_repo`, `base_branch`, draft, `pr_author` (from `pull_request`.user), check app slug and conclusion. Tag `self_authored` only for github.pr.synchronize. Update api/openapi.json only if the event schema is in it.
- covers: c2, h2, c28, h22, c33, h27
- acceptance:
  - a `pull_request` synchronize payload sent by the App's identity is ingested with `self_authored` true; the same payload from a human is not
  - a `check_suite` completed payload whose sender is the App is ingested with `self_authored` false
  - event data for every `pull_request`-derived type carries `head_sha`, `head_branch`, `head_repo`, `base_repo`, draft and `pr_author`
  - `pull_request_review_comment` created maps to github.`review_comment`.created with the comment author

### t3 — Shared variables: model, store (memory + mongo) and migration

- instruction: New `culture_rules`/model/variable.py (Variable: name, value JSON scalar or list, version, `updated_by`, `updated_at`, description). Store port + memory.py + mongo.py gain variable CRUD with append-only versions; migrations.py adds the collection and a unique index on name. No engine or API wiring here.
- acceptance:
  - store.`put_variable` creates version n+1 and keeps every prior version readable
  - a variable name must match ^\[a-z\]\[a-z0-`9_`\]{0,63}$; other names are refused
  - MemoryStore and MongoStore pass the same variable contract test

### t4 — Workflow model: a wait step kind (duration plus a resume guard) with validation

- instruction: Files: `culture_rules`/model/workflow.py (`STEP_KINDS` gains 'wait'; config: seconds, guard: '`head_unchanged`' with a ref to the head SHA input) and `culture_rules`/model/validate.py. Model and validation only; the executor is a separate task.
- acceptance:
  - a workflow with a wait step of 300 seconds and guard `head_unchanged` validates; a negative or missing duration is refused with a field path
  - `STEP_KINDS` and the explain catalog list wait

### t5 — GitHub actions: github.push and github.`review_reply` as the App, with per-push repo-scoped tokens

- instruction: Files: `culture_rules`/model/`action_kinds.py`, `culture_rules`/apps/github.py, a new handler under `culture_rules`/node/actions/. github.push takes repo, `head_branch`, `expected_head_sha` and the local commit bundle/worktree ref; it mints an installation token with repositories=\[repo\] and permissions {contents: write}, does a non-force push, and refuses when the remote head differs from `expected_head_sha` or when the target is not the PR's own head branch in its own repo. It also refuses when the firing rule is disabled at push time. github.`review_reply` posts a reply to a review thread and optionally resolves it (GraphQL resolveReviewThread) as the App. No merge action is added.
- covers: c14, h9, c29, h23, c32, h26, c8, h5, c37, h31
- acceptance:
  - github.push with a stale `expected_head_sha` fails and pushes nothing
  - the minted push token request carries exactly one repository and only contents:write
  - a non-fast-forward update is refused before any network push; no merge action kind exists in `ACTION_KINDS`
  - github.`review_reply` posts as the App and resolves the thread when resolve is true
  - github.push is refused when the source rule is disabled at execution time

### t6 — culture.yaml gate section: declare setup and test argv in culture-agent-template and in culture-rules' own culture.yaml

- instruction: In ../culture-agent-template add a top-level gate: {setup: \[\[uv, sync\]\], test: \[\[uv, run, pytest, -n, auto\]\]} to culture.yaml and document it in its README; open the PR there per its CLAUDE.md. In this repo add the same section to culture.yaml. Check steward doctor and culture's loader accept the key (culture's `load_culture_yaml` ignores unknown top-level keys). Values are configuration only; no code default.
- covers: c41
- acceptance:
  - culture-agent-template's culture.yaml carries a gate section and its own CI still passes
  - culture-rules' culture.yaml carries a gate section; culture-rules doctor and steward doctor stay green

### t7 — Engine resolves shared variables in conditions and workflow inputs; undefined references refused at save; fail closed on nodes without variable support

- instruction: Files: `culture_rules`/engine/matching.py (pass variables loaded from the store into `_condition`), `culture_rules`/model/refs.py (WorkflowRef.inputs accepts {"$var": name}), `culture_rules`/model/validate.py (refuse a rule that references an undefined variable), node heartbeat capability 'variables' (`culture_rules`/node/daemon.py). A rule that references a variable is only evaluated on nodes advertising the capability; elsewhere it records an error and does not fire.
- depends on: t3
- covers: c25, h17, c34, h28
- acceptance:
  - changing a variable's value changes the outcome of the next matching event for every rule that references it, with no rule edited
  - saving a rule whose condition references vars.missing is refused with the variable name in the error
  - on a node without the variables capability, a rule with not(author in vars.x) fires nothing and records an error
  - workflow inputs mapped with {"$var": name} receive the variable's current value at firing time

### t8 — Variables over the API, CLI, MCP, openapi.json and explain: admin-only writes, version history, referencing rules

- instruction: Files: `culture_rules`/server/app.py (+ service.py), new `culture_rules`/cli/`_commands`/variables.py wired in `_build_parser`, `culture_rules`/mcp/tools.py, api/openapi.json, `culture_rules`/explain/catalog.py and learn. Verbs: list, get, set (dry-run by default, --apply commits), history, refs (rules that reference it). Writes require the admin role; every write records the principal. Keep the CLI/MCP parity test green.
- depends on: t3
- covers: c35, h29
- acceptance:
  - an editor-role principal gets 403 on PUT /variables/{name}; an admin's write creates a new version naming the principal
  - culture-rules variables set `trusted_authors` --value '\[...\]' is a dry run without --apply
  - tests/server/`test_openapi_contract.py` and the CLI/MCP parity test pass with the new verbs
  - GET /variables/{name}/refs lists every rule whose condition or inputs reference it

### t9 — Async bridge agent actor: dispatch to a cultureagent bridge for an arbitrary repo and PR head, completion via callback

- instruction: Files: `culture_rules`/actors/agent.py (new BridgeAgentActor) and `culture_rules`/node/actors.py (production factory). Speak the bridge HTTP protocol with stdlib urllib only (no third-party import in the core). invoke() returns accepted with the invocation id; completion and heartbeat callbacks arrive on a node endpoint and call Executor.deliver. Repo, head branch and head SHA come from step config, not the actor. Does not depend on cultureagent at import time (HTTP only).
- covers: c3, h3, c10, h6
- acceptance:
  - an `actor_task` on a BridgeAgentActor returns accepted and frees the worker; a later completed callback finishes the step
  - after a node restart between accepted and completed, the completion still finishes the step
  - python -c 'import `culture_rules`, `culture_rules`.actors.agent' succeeds with no extras installed
  - the bridge request carries repo, `head_branch` and `head_sha` taken from step inputs

### t10 — Executor: run wait steps without holding a worker, persist across restarts, enforce the head-unchanged guard

- instruction: File: `culture_rules`/engine/runs.py (and engine/lifecycle.py if the resume timer lives there). Reuse the persisted-wait machinery human asks use. On resume, the `head_unchanged` guard reads the PR's current head SHA (via the GitHub app actor) and ends the run as 'superseded' when it moved.
- depends on: t4
- covers: c40, h32
- acceptance:
  - a wait step of N seconds parks the run with no worker held and resumes after N seconds
  - with a node restart mid-wait the run still resumes once
  - if the PR head SHA changed during the wait, the run ends superseded and no later step runs

### t11 — Per-PR concurrency key and attempt budget

- instruction: Files: `culture_rules`/model/rule.py (optional `concurrency_key` template, e.g. '{trigger.data.repository}#{trigger.data.number}', and `max_attempts`) and `culture_rules`/engine/claims.py. A firing whose key has an active run is dropped (recorded as deduplicated); after `max_attempts` runs without the key going green, further firings are skipped until a non-self push (github.pr.synchronize with `self_authored` false) resets the counter.
- depends on: t7
- covers: c6, h4
- acceptance:
  - two firings with the same concurrency key within a minute produce exactly one active run; the second is recorded as deduplicated
  - after `max_attempts` the next firing is skipped and recorded; a human push resets the counter

### t12 — Once-per-SHA settle: fire the fixer only when every non-ignored check suite on the head SHA has completed, or after a settle timeout

- instruction: New module `culture_rules`/node/`checks_settle.py`, called from the GitHub check-event path. On a check completion, list the head SHA's check suites through the App (Checks: read), drop suites from apps in vars.`ignored_check_apps` (seed it with 'claude'), and emit one github.pr.`checks_settled` event per head SHA when all remaining suites are completed, or after vars.`checks_settle_timeout_s`. Deduplicate per SHA. The fixer rule triggers on github.pr.`checks_settled`.
- depends on: t2, t7
- covers: c27, h21
- acceptance:
  - for a SHA with github-actions, sonarqubecloud and gitguardian suites, exactly one `checks_settled` event is emitted, after the last completes
  - a queued suite from an app in `ignored_check_apps` does not hold back the event
  - with a suite still running at the settle timeout, one `checks_settled` event is emitted with `settled_by`: timeout

### t13 — Test gate and diff guard: run the culture.yaml gate as the fixer user, reject commits that weaken checks

- instruction: New `culture_rules`/actors/gate.py plus a 'gate' code-step in the fixer workflow. Read the gate section from culture.yaml on the PR's BASE branch (r1 decision), never the PR head, run setup then test as argv lists with shell=False in the agent's worktree as the fixer Unix user, capture the tail of output. Before running tests, the diff guard checks the commits since the start SHA against vars.`fixer_protected_paths` (seed: .github/workflows/\*\*, sonar-project.properties, coverage/lint config) and rejects deleted or skipped tests and new NOSONAR / noqa / type: ignore markers. Verdict: pass, fail (with output), guard (with rule name), `no_gate`. No built-in default command.
- depends on: t6, t7
- covers: c13, h8, c31, h25, h33
- acceptance:
  - a commit that deletes a failing test gets verdict guard naming the rule; nothing is pushed
  - a repo whose culture.yaml has no gate section gets verdict `no_gate` and zero pushes
  - the gate runs exactly the declared argv with shell=False; a test asserts no shell is spawned
  - a failing test run returns verdict fail with the output tail, which becomes the agent's next instruction
  - a PR that edits its own culture.yaml gate section is judged by the base branch's gate section

### t14 — Editor: Variables tab and a variable picker in the condition editor

- instruction: Files: new web/src/routes/Variables.tsx (+ test), web/src/App.tsx, web/src/App.test.tsx (tab order Rules | Workflows | Actors | Variables | Statistics), the condition editor under web/src/rules/ (an 'in' operand can pick a variable instead of a literal list). Follow the design canvas look; update the canvas with the new tab. Large targets, keyboard accessible, prefers-reduced-motion respected. Types compile against api/openapi.json.
- depends on: t8
- covers: c23, h20, c26
- acceptance:
  - App.test.tsx asserts exactly five tabs in the order Rules, Workflows, Actors, Variables, Statistics
  - in the condition editor, an author check can be set to vars.`trusted_authors` from a picker, and the saved condition stores a var operand, not a copied list
  - the Variables tab lists variables, edits a list value (admin only), shows version history and the rules that reference each
  - Playwright and the webglass agent-state gate pass in the web CI job

### t15 — Docs: five primary tabs in CLAUDE.md, QWEN.md, AGENTS.override.md, AGENTS.colleague.md and the engine-editor spec

- instruction: Edit the 'Exactly four primary tabs' constraint in all four harness prompt files and docs/specs/2026-10-03-culture-rules-engine-editor.md to five (Rules | Workflows | Actors | Variables | Statistics), noting the operator decision from the pr-fixer spec. Keep the load-bearing phrases listed in CLAUDE.md intact; run scripts/harness-smoke.py --stage config.
- depends on: t14
- covers: h18
- acceptance:
  - grep finds the same five-tab sentence in CLAUDE.md, QWEN.md, AGENTS.override.md and AGENTS.colleague.md, and none still says 'four primary tabs'
  - harness-smoke --stage config and markdownlint pass

### t16 — Operator hand-turn: add Contents RW, Checks R and Actions R to the rules-culture-dev App, re-approve the org installation, and prove it on a scratch repo

- instruction: Operator changes the App's permissions in GitHub settings and approves the installation update. The agent then, on a scratch agentculture repo, mints a single-repo token and pushes one commit to a same-repo PR branch and lists check runs; records the evidence. Update docs/actors/github.md (permissions and events: `check_suite`, `workflow_run`, `pull_request_review_comment`). No Workflows permission.
- covers: c12, h7
- acceptance:
  - docs/actors/github.md lists Contents RW, Checks R, Actions R, Pull requests RW, Issues RW, Metadata R and the new subscribed events
  - on a scratch repo, a token scoped to that repo pushes one commit to a PR branch and reads its check runs; the commit's author is rules-culture-dev\[bot\]

### t17 — The fixer rule and workflow, defined as data: settled checks or trusted comment -> wait -> agent -> gate (retry) -> push -> replies -> comment

- instruction: Author the rule and workflow as committed data (docs/rules/pr-fixer.yaml or the repo's io import format) and seed the variables `trusted_authors`, `ignored_check_apps`, `fixer_excluded_repos`, `fixer_protected_paths`, `checks_settle_timeout_s`. Rule: triggers github.pr.`checks_settled` and github.`review_comment`.created / github.review.submitted / github.comment.created; condition: `head_repo` == `base_repo`, not draft, repository not in vars.`fixer_excluded_repos`, and for comment triggers author in vars.`trusted_authors`; `concurrency_key` per PR, `max_attempts` 3; placement on spark2. Workflow: wait (quiet period, `head_unchanged`) -> `retry_until`(gate pass, max 3){ agent step on the bridge actor with threads filtered to vars.`trusted_authors` -> gate } -> github.push -> github.`review_reply` per addressed thread -> github.comment with the run link. Rule ships disabled.
- depends on: t5, t9, t10, t11, t12, t13
- covers: c1, h1, h15, c36, h30
- acceptance:
  - importing the rule and workflow with --apply validates; the rule's condition references vars.`trusted_authors` and vars.`fixer_excluded_repos`, not literal lists
  - a replayed github.comment.created from a non-trusted author does not fire; from a trusted author it does
  - with the repository in `fixer_excluded_repos` the rule does not fire; disabling the rule mid-run leaves no App push
  - every run's final App comment links the run, and the run appears on spark2's Statistics lane

### t18 — spark2 fixer machine: dedicated unprivileged user, cultureagent bridges as user services, Qwen Code on cortex, Codex, read-only GitHub and Sonar access, registered actors

- instruction: Add docs/operations/pr-fixer.md and a deploy script under deploy/. Create Unix user culture-fixer on spark2 with no access to the node's secrets, the App key or the operator's gh/claude credentials. Install the cultureagent qwen and codex bridges as systemd --user units of that user, bound to the tailnet with a bearer token sealed by grant. Qwen Code points at the cortex model; Codex logs in for that user. Give it a read-only GitHub token (Contents R, Pull requests R, Checks R, Actions R) and a Sonar token for reads. Register BridgeAgentActor actors (qwen-fixer, codex-fixer) placed on spark2.
- depends on: t1, t9
- covers: c30, h24
- acceptance:
  - as culture-fixer, reading the node's secret env, the App private key, or ~spark/.config/gh fails with permission denied
  - GET /v1/capabilities on both bridges answers over the tailnet with the bearer token and 401 without it
  - culture-rules actors list shows qwen-fixer and codex-fixer placed on spark2

### t19 — Roll the gate section out to agentculture repos

- instruction: After t6 merges, add a gate section to each agentculture repo's culture.yaml that wants fixer runs (start with the Python/uv repos; take each repo's command from its CI). Use guildmaster's broadcast or per-repo PRs, each with its own version bump. Repos without a gate section stay comment-only by design.
- depends on: t6, t18
- acceptance:
  - a list of repos with and without a gate section is committed to docs/operations/pr-fixer.md
  - at least culture-rules, culture-agent-template and one other repo carry a gate section on main

### t20 — End-to-end success run on a scratch repo, read off run history

- instruction: Enable the rule for one scratch agentculture repo only (all others in `fixer_excluded_repos`). Open a PR seeded with one Sonar issue and one failing test, plus a fork PR and a draft PR. Read the outcome with culture-rules runs and the cicd skill's status verb, never the agent's own report. File the evidence with devague evidence. Then widen `fixer_excluded_repos` step by step.
- depends on: t16, t17, t18
- covers: c17, h10, c18, h11, c19, h12, c20, h13, c21, h14
- acceptance:
  - within 30 minutes of the seeded PR's checks settling: at most 3 App commits, CI green, Sonar gate passing, 0 unresolved threads, all read from run history and the cicd status verb
  - the fork PR and the draft PR have 0 fixer runs in run history
  - every fixer-path commit and thread reply on the PR is authored by rules-culture-dev\[bot\]

### t21 — Live testing on rules.culture.dev: the built fixer on the real fleet (spark, thor, orin, spark2) against real GitHub webhooks

- instruction: Upgrade all four nodes to the release, then run live probes against rules.culture.dev, recording each with devague evidence: (1) a real PR on a scratch repo through settled checks with SonarCloud, GitGuardian and the never-completing claude suite present; (2) a push during the wait step supersedes the run; (3) disable the rule mid-run and confirm no App push; (4) add the repo to `fixer_excluded_repos` and confirm no run; (5) hold one node on the previous version and confirm a variable-referencing rule fails closed there; (6) a comment from a non-trusted account fires nothing. Use the worker colleague seat for live checks where it helps. Read outcomes from run history and webhook deliveries, never the agent's report.
- depends on: t20
- covers: h28, h30, h21, h32
- acceptance:
  - each of the six live probes has an evidence record naming the run ids or delivery ids that prove it
  - no probe leaves an App push on a PR where the spec says none may happen

### t22 — Dogfood: turn the fixer on for culture-rules itself and let it work this delivery's own PR

- instruction: Remove culture-rules from `fixer_excluded_repos` after t21. Open the delivery PR (per the cicd skill) and let the fixer handle its Sonar issues, failing checks and Qodo threads. Also run it on at least two more real culture-rules PRs. Record every fixer run, each gate verdict and any wrong or unwanted fix as devague evidence or behavioral delta. Keep other repos excluded until the operator widens it.
- depends on: t21
- covers: c1, h1
- acceptance:
  - the fixer ran on at least three real culture-rules PRs, including this delivery's PR, each with run ids recorded
  - every unwanted or wrong fix seen while dogfooding is filed as a delta or a follow-up issue, none silently dropped

### t23 — Run /validate-delivery: run the plan's behavioral tests agent-side and file evidence and deltas

- instruction: Run the validate-delivery skill after t22, before the final PR is marked ready. Run every task's acceptance criteria and every confirmed honesty condition as behavioral tests; file each outcome with devague evidence and every change from the plan with devague delta. Do not suppress failing or partial outcomes.
- depends on: t22
- acceptance:
  - every confirmed task has at least one evidence record; failing or partial outcomes are filed, not omitted

### t24 — Run /summarize-delivery: planned versus actual, drift, evidence-backed delivery claims, remaining work

- instruction: Run the summarize-delivery skill after t23 and commit its artifact; it is the last step before the final PR goes to the operator for review. Include the dogfooding and live-test results and every deviation.
- depends on: t23
- acceptance:
  - a delivery summary is committed that lists every task as delivered, changed or not delivered, with evidence ids
  - the final PR description links the summary

## Risks

- [unknown_blocking] Should the test gate read the gate section from the PR's base branch (a PR cannot change how it is judged) or from the PR head (gate edits take effect in the same PR)? Recommendation: base branch (task t13)
- [unknown_nonblocking] Multi-hour agent runs vs step deadlines and `unsafe_retry` (bridge actors do not support idempotency keys); `retry_until` bounds need a measured run time (task t9)
- [unknown_nonblocking] Capacity: one cortex GPU on spark2 for ~100 repos; set `max_concurrency` and `token_budget` on the fixer actors from the first week's queue depth (task t18)
- [follow_up] Backup and restore (docs/operations/backup) do not yet name the new variables collection (task t3)
- [follow_up] culture-nodes' Go actor registration against the new plain bridge result schema is unexamined; culture-nodes may need to cite the bridges back (task t1)
- [unknown_nonblocking] Bridge completion callbacks need a reachable endpoint: the node daemon has no HTTP listener, so callbacks likely land on the API server and are delivered from the store (task t9)
