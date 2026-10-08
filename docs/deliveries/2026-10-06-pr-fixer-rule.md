# Delivery Summary — pr-fixer rule

plan: `pr-fixer-rule` · run: `partial` · date: `2026-10-06`
baseline: `devague summary skeleton`

## Intent

> A new PR in any AgentCulture repo is picked up by a fixer agent that drives it to green: SonarCloud gate, failing checks and unresolved review threads fixed on the PR branch, never merged

After: A same-repo PR in any agentculture repo gets a fixer run on spark2 without anyone asking: the agent fixes Sonar issues, failing checks, and review comments from the operator, Qodo and a pre-approved list of users and apps (comments from anyone else are ignored) in a worktree; the engine's tests gate each commit, the App pushes passing commits, and the operator only reviews and merges

## Planned Work

- `t1` — cultureagent: lift the qwen (ACP) and codex bridges from culture-nodes into cultureagent with one shared core and a plain result schema
- `t2` — Webhook: map synchronize, `ready_for_review`, `check_suite`/`workflow_run` completed and `pull_request_review_comment`; enrich event data; `self_authored` applies to synchronize only
- `t3` — Shared variables: model, store (memory + mongo) and migration
- `t4` — Workflow model: a wait step kind (duration plus a resume guard) with validation
- `t5` — GitHub actions: github.push and github.`review_reply` as the App, with per-push repo-scoped tokens
- `t6` — culture.yaml gate section: declare setup and test argv in culture-agent-template and in culture-rules' own culture.yaml
- `t7` — Engine resolves shared variables in conditions and workflow inputs; undefined references refused at save; fail closed on nodes without variable support
- `t8` — Variables over the API, CLI, MCP, openapi.json and explain: admin-only writes, version history, referencing rules
- `t9` — Async bridge agent actor: dispatch to a cultureagent bridge for an arbitrary repo and PR head, completion via callback
- `t10` — Executor: run wait steps without holding a worker, persist across restarts, enforce the head-unchanged guard
- `t11` — Per-PR concurrency key and attempt budget
- `t12` — Once-per-SHA settle: fire the fixer only when every non-ignored check suite on the head SHA has completed, or after a settle timeout
- `t13` — Test gate and diff guard: run the culture.yaml gate as the fixer user, reject commits that weaken checks
- `t14` — Editor: Variables tab and a variable picker in the condition editor
- `t15` — Docs: five primary tabs in CLAUDE.md, QWEN.md, AGENTS.override.md, AGENTS.colleague.md and the engine-editor spec
- `t16` — Operator hand-turn: add Contents RW, Checks R and Actions R to the rules-culture-dev App, re-approve the org installation, and prove it on a scratch repo
- `t17` — The fixer rule and workflow, defined as data: settled checks or trusted comment -> wait -> agent -> gate (retry) -> push -> replies -> comment
- `t18` — spark2 fixer machine: dedicated unprivileged user, cultureagent bridges as user services, Qwen Code on cortex, Codex, read-only GitHub and Sonar access, registered actors
- `t19` — Roll the gate section out to agentculture repos
- `t20` — End-to-end success run on a scratch repo, read off run history
- `t21` — Live testing on rules.culture.dev: the built fixer on the real fleet (spark, thor, orin, spark2) against real GitHub webhooks
- `t22` — Dogfood: turn the fixer on for culture-rules itself and let it work this delivery's own PR
- `t23` — Run /validate-delivery: run the plan's behavioral tests agent-side and file evidence and deltas
- `t24` — Run /summarize-delivery: planned versus actual, drift, evidence-backed delivery claims, remaining work

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | cultureagent 0.14.0 (agentculture/cultureagent#52, merged): qwen (ACP) and codex bridges on one shared core; the fixer and the reviewer run them |
| `t2` | delivered | new GitHub event types and enriched data (9bb9c81); self-tag exempts only check completions (`d2`) |
| `t3` | delivered | shared variables model, append-only store, migration (4d9c73f) |
| `t4` | delivered | wait step kind, model and validation (42f827f) |
| `t5` | delivered | github.push and github.review_reply as the App with repo-scoped push tokens (e3c1bff) |
| `t6` | delivered | gate section in culture-agent-template (#34, merged) and culture-rules (5342ccb; #18) |
| `t7` | delivered | engine resolves shared variables, fails closed on nodes without support, save/enable guard (f0f79d6, `d7`) |
| `t8` | delivered | variables over API, CLI, MCP, openapi, explain; admin-only writes (b342732) |
| `t9` | delivered | async bridge agent actor, API callback route (`d1`), attempt-guarded deliver (`d3`) (0d230f0); later the agent review step (`d20`, fddf6af) |
| `t10` | delivered | wait steps park persisted; head_unchanged guard ends runs superseded (63b6489) |
| `t11` | delivered | global concurrency key and attempt budget (befc925, `d13`) |
| `t12` | delivered | once-per-SHA checks settle with min window, polled backoff and timeout (e75b643), recovery from stored completions (9e072f8) |
| `t13` | delivered | test gate in a fresh hardened checkout as the fixer user, diff guard (3521d0f, `d10`); later the gate builds the one pushed commit and runs with a username-free temp dir (`d20`, 42ed30e) |
| `t14` | delivered | Variables tab, variable picker, push/reply pickers (969ad16); describe, (i) button and zoom (`d19`, 1b9ff2d) |
| `t15` | delivered | five primary tabs in the four harness files, the engine-editor spec and the CLI self-description (7eaabb4) |
| `t16` | delivered | App permissions granted and proven by probe PR #14 (closed, as planned); Actions kept read/write (`d5`) |
| `t17` | delivered | four fixer rules and one pr-fixer workflow as data (0451500, `d12`-`d18`), stop-runs on disable (097da54) |
| `t18` | partial | spark2 runs culture-fixer with the qwen bridge on cortex only; Codex dropped from spark2 (`d8`); the read-only GH/Sonar tokens it holds contradict claim c30 (see Drift) |
| `t19` | partial | gate section rolled out to culture-rules (#18), culture-rules-tester and lobes-cli (#300) only, matching the `d18` allow-list; not to every agentculture repo |
| `t20` | delivered | full pass on the sandbox (run-4144cb04; App pushed 0e1b4bf); draft #2 and fork #3 got 0 runs; t20 ran on a new scratch repo (now agentculture/culture-rules-tester), not on culture-rules as `d4` records |
| `t21` | partial | probes 1-4 pass (end to end; supersede on push; rule disabled mid-run refuses the push, run-237e3abd; excluded repo gets no run); probe 5 (node held on an old build) and probe 6 (non-trusted commenter) not run |
| `t22` | delivered | the fixer worked #17: one fix pushed (cedd5ab, run-47bd8394 attempt 2, after the diff guard caught skip/nosec in attempt 1), three Sonar cycles timed out and the gate was fixed by hand (`d22`, 4d70fb3); lobes-cli#302 push refused head_moved |
| `t23` | delivered | 36 obligations, 77 evidence records (67 pass, 10 fail), 12 behavioural deltas (ca3ccfe), approved by the operator |
| `t24` | delivered | this summary |

## Mid-work Decisions

- `d1` — Bridge completion callbacks land on an API server route (POST /bridge-invocations/{id}/events on the LAN listener, authenticated by each attempt's callback token, not service tokens); the node daemon calls `redeliver_bridge` each cycle. t9 grows to cover one route in `culture_rules`/server/app.py and one call in `culture_rules`/node/daemon.py — the node daemon has no HTTP listener and a bridge retries a callback only about 5 times in a few seconds, so a node-hosted receiver loses completions when that node is down; operator chose the API route (plan risk r6)
- `d2` — `self_authored` stays set for every event type whose author matches `self_identity`; only the check-completion types (github.checks.`suite_completed`, github.checks.`workflow_completed`) are exempt, instead of 'synchronize only' — built literally, c28 stopped self-tagging the bot's own Jira, Discord and GitHub comment events, which lets rules fire on the bot's own messages; the intent was only that check results on App-pushed commits still fire the fixer
- `d3` — Executor.deliver takes an optional attempt= argument checked inside its compare-and-set; bridge redelivery and completions.deliver pass the recorded attempt, so a superseded attempt's result is refused atomically — t9's attempt check ran before Executor.deliver, leaving a narrow race where an old attempt's result could finish a newer attempt of an idempotent step; the fix needs a small change in engine/runs.py, outside t9's named files
- `d4` — Live GitHub probes (t16) and the end-to-end and live runs (t20, t21) operate on agentculture/culture-rules itself instead of a separate scratch repo; throwaway branches and PRs there are closed and deleted after each probe — operator chose culture-rules as the test repo (2026-10-06); no new scratch repo is created
- `d5` — The App keeps Actions: Read and write (not Read only as c12 says); the fixer itself uses only read (`workflow_run` webhooks, failing job logs) and github.push still mints contents-only tokens — operator decision 2026-10-06: Actions write is kept on purpose for future cases
- `d6` — Merge-gate reviewer must differ from the implementer: Qwen-implemented tasks are reviewed by an Opus subagent; Opus/Sonnet-implemented tasks (and the main agent's own) are reviewed by Qwen Code on cortex. Replaces Codex as the per-task and per-wave reviewer from wave 2 on (t7, t8 onward); max 3 review rounds and the before/after full-suite gate are unchanged. — Operator decision 2026-10-06: a reviewer different from the implementer; Codex also hit its usage limit mid-wave-2.
- `d7` — Option A for h28: while any online node's heartbeat lacks capabilities:\[variables\], the save path (create/update/import) refuses a rule that references a shared variable, naming the nodes; with every node upgraded before rollout this makes h28 hold for already-deployed 0.12.0 binaries — Operator decision 2026-10-06: t7 found that old binaries evaluate a condition-only reference not(a in vars.x) as true and fire; only current-code nodes fail closed
- `d8` — Codex is not installed on the fixer machine: spark2 runs only the cultureagent qwen bridge (Qwen Code on cortex) as culture-fixer; Codex stays on spark only. t18's codex-fixer actor and its bridge on spark2 are dropped; the 'both bridges' acceptance check applies to the qwen bridge — Operator decision 2026-10-06: codex stays only on spark
- `d9` — Codex returns as the merge-gate reviewer for every task and wave from here on (t7 round 3, t14, waves 3-9), amending d6: Qwen cortex is no longer used for reviews. The reviewer still differs from the implementer (no task is implemented by Codex). Max 3 rounds and the before/after full-suite gate are unchanged. — Operator decision 2026-10-07 after the cortex review tally: 27 findings, 10 real, every P1 false, about 2.5 h lost to stalled runs; Codex had caught both real P1s of the run.
- `d10` — t13 adds three engine/action pieces beyond its listed files: `retry_until` config.carry (feed the previous iteration's result into the next iteration's inputs), an optional `gate_verdict` param on github.push (refuse unless 'pass', before any credential or network access), and a BuiltinCodePort registered for kind 'code' (runs config.builtin gate; unknown builtins fail `no_builtin`) — Operator approved 2026-10-07: AC4 (a failing run's output becomes the agent's next instruction) cannot be met without carry, since a gate->agent back edge is rejected as a cycle; the engine has no branch step, so `gate_verdict` is how `no_gate`/guard end without a push; the gate step needs a code port
- `d11` — Codex is preferred over Qwen as the implementer for every task the split assigned to the Qwen lane that has not started or finished (t15 onward; t11 stays with Qwen as it is nearly done). A Codex-implemented task is reviewed by an Opus subagent (never Codex). Qwen, on cortex-spark2 only, remains the fallback when Codex is unavailable. — Operator decision 2026-10-07: prefer Codex over Qwen; spark is freed for other loads
- `d12` — A built-in 'action' code step: a workflow step of kind code with config {builtin: action, action: {kind, params}} runs any registered action kind through the same router as a rule's terminal action (actor checks, limits, `gate_verdict` refusal, disabled-rule refusal). The fixer workflow uses it for github.push and a `for_each` of github.`review_reply`; the rule's own action stays the final github.comment with the run link. — Operator chose option A 2026-10-07: t17's workflow needs four GitHub actions per run, but only a rule's single terminal action can reach action ports; workflow steps route by step kind only
- `d13` — The fixer is four rules (checks settled, PR comment, review submitted, review comment) sharing one pr-fixer workflow, because an event trigger matches exactly one type; and t11's concurrency key is GLOBAL: the active-run check, the attempt budget and coalescing are keyed by the resolved key string alone (e.g. pr-fixer:{repo}#{number}), not by rule id plus key, so one PR has one active fixer run and one budget whichever rule fired. — Operator confirmed 2026-10-07: one trigger type per rule; the per-PR limit must hold across all fixer triggers; no editor change needed
- `d14` — PR facts for every fixer trigger: (a) the GitHub webhook normalizer records `base_sha` (`pull_request`.base.sha); (b) a PR comment (`issue_comment` on a pull request) is enriched at the webhook path with the PR's `head_sha`, `head_branch`, `head_repo`, `base_repo`, `base_branch`, `base_sha`, draft and `pr_author` via a read-only App PR lookup; (c) `checks_settled`'s PR enrichment adds `base_sha`. The four fixer rules then share one condition shape and the workflow gets the same inputs whichever rule fired. — Operator approved 2026-10-07: `issue_comment` payloads carry no PR details and no event carried the base SHA the t13 gate needs to read culture.yaml at the base commit
- `d15` — add a code builtin github.threads (lists unresolved review threads via the App, keeps only vars.`trusted_authors`, outputs them with numeric reply comment ids) feeding the agent's threads input and the reply loop; the reply loop answers only threads from that list — the engine had no way to list or filter review threads, so spec honesty 'an untrusted thread is never handed to the agent' was enforced only by the agent; bridge thread ids are not REST comment ids
- `d16` — add an optional rule field `on_failure` (an action like the rule action, may reference run.id and the failing step's error) run when the workflow fails, not when it is superseded; the fixer posts a hand-back comment with the run link — the engine had no on-failure action, so guard/test failures and `no_gate` repos posted nothing and acceptance criterion 4 (every run's final comment links the run) held for successful runs only
- `d17` — disabling a rule that has active runs raises a 'Stop N current runs?' notification (editor prompt on disable; API/CLI/MCP report the active runs and offer an explicit stop); approving cancels those runs (cancelled: no push, no `on_failure` hand-back); not approving leaves them running, where the push step still refuses `rule_disabled` — operator decision 2026-10-07: a rule disabled mid-run should offer to stop its current runs rather than only failing at push and posting a hand-back comment
- `d18` — replace the deny-list rollout with an allow-list: new shared variable `fixer_repos`; every fixer rule fires only when trigger.data.repository is in vars.`fixer_repos` (`fixer_excluded_repos` stays as an override); widening the fixer = adding a repo to `fixer_repos`; guildmaster gets an issue to add a repo to `fixer_repos` at provisioning, chosen like public/private — operator decision 2026-10-07: a deny-list fixes every new repo by default and makes 'only the scratch repo' (t20) a list of every other repo
- `d19` — editor: (1) a deterministic, non-AI plain description of a rule and of a workflow generated only from its config (trigger, condition, steps, placement, action), shown in the editor and available via CLI/API/MCP; (2) zoom in/out (and fit) on the workflow canvas, which is locked at zoom 1 today — operator request 2026-10-07 while reviewing the PR fixer workflow: the flow needs a short readable explanation without prose or an LLM, and the canvas needs zoom
- `d20` — pr-fixer workflow gains an agent review step: after the gate passes, a codex-reviewer agent (Codex via the culture-nodes codex bridge on spark, read-only) reviews the fix commit (start..commit diff, PR intent, threads, gate verdict) and returns approve or `request_changes`; github.push runs only when the gate passes AND the reviewer approves; `request_changes` feeds the findings into the next fix attempt within the same 3-attempt budget, otherwise the run hands back with the findings on the PR. Live fixer rules stay enabled meanwhile (operator choice). — operator: the fixer's commit must be reviewed by another agent step and pushed by the rule's code, not by a human/main-agent review; Qwen will author PRs from tomorrow and this process is the safety net
- `d21` — split the PR fixer into rules + workflows chained by events (d22): the engine emits rules.run.succeeded / rules.run.failed events on run completion (rule, workflow, concurrency key, exported outputs, causation/correlation lineage) that triggers can match; derived events carry a hop count and firing past a cap fails closed; only fix runs count against the per-key attempt budget; the review record is keyed by repo+commit (written only by the review builtin) and github.push checks the exact commit; one hand-back comment per chain; editor trigger picker + describe support. Shape: pr-fixer-{checks,comment,review,review-comment} -> workflow pr-fix (quiet, threads, agent, gate); pr-fixer-review (on pr-fix succeeded) -> review-commit; pr-fixer-refix (review = `request_changes`) -> pr-fix with findings; pr-fixer-publish (review = approve) -> publish-fix (push, pick, replies). Disabling pr-fixer-publish gives review-only mode. Built on top of d20, after d20's review. — operator: the workflow needs splitting; chose rules+workflows (the product model's composition) over a new sub-workflow step kind, and asked to make the engine work for it, events and all
- `d22` — manual fallback after the fixer's 3 cycles: when the PR fixer hands back after 3 attempts on the same problem (e.g. #17's d19 Sonar batch), the main agent fixes it by hand and posts a PR comment explaining why the fixer could not (budget/limit), so the hand-back is never silently overridden — operator: '4. after 3 cycles, yes, with noting a comment why' (re #17 Sonar issues the fixer timed out on twice)

- **Queue fix (no deviation record; a live defect fix):** an agent's work timer starts when it accepts, queued work has its own bound (`queue_timeout`), blocked polls back off and history stays bounded; runs left by the old engine are adopted (5183b4c, deployed). Found live on #17 and lobes-cli#302.
- **Gate temp dir (no record):** gate commands get a workspace `TMPDIR` and pytest `--basetemp`, because the run-as name `culture-fixer` put `-f` into lobes-cli's tmp paths (42ed30e).
- **Reviewer runs as `spark` (operator decision 2026-10-08, under `d20`):** the Codex bridge uses the spark account's Codex login, not a dedicated account; its read-only sandbox was verified confined.
- **`commit_author` set on the github-app actor (operator decision 2026-10-08):** both the old and new App digests are trusted during the move.
- **`d21` moves to a follow-up PR (operator decision 2026-10-08):** #17 ships without the rule split; phase 1 sits on `rules/pr-fixer-split`.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t9` (`d1`) | the node daemon has no HTTP listener and a bridge retries a callback only about 5 times in a few seconds, so a node-hosted receiver loses completions when that node is down; operator chose the API route (plan risk r6) | `acceptable` |
| `t2` (`d2`) | built literally, c28 stopped self-tagging the bot's own Jira, Discord and GitHub comment events, which lets rules fire on the bot's own messages; the intent was only that check results on App-pushed commits still fire the fixer | `acceptable` |
| `t9` (`d3`) | t9's attempt check ran before Executor.deliver, leaving a narrow race where an old attempt's result could finish a newer attempt of an idempotent step; the fix needs a small change in engine/runs.py, outside t9's named files | `acceptable` |
| `t16` (`d4`) | operator chose culture-rules as the test repo (2026-10-06); no new scratch repo is created | `acceptable` |
| `t16` (`d5`) | operator decision 2026-10-06: Actions write is kept on purpose for future cases | `acceptable` |
| `t7` (`d6`) | Operator decision 2026-10-06: a reviewer different from the implementer; Codex also hit its usage limit mid-wave-2. | `acceptable` |
| `t7` (`d7`) | Operator decision 2026-10-06: t7 found that old binaries evaluate a condition-only reference not(a in vars.x) as true and fire; only current-code nodes fail closed | `acceptable` |
| `t18` (`d8`) | Operator decision 2026-10-06: codex stays only on spark | `acceptable` |
| `t7` (`d9`) | Operator decision 2026-10-07 after the cortex review tally: 27 findings, 10 real, every P1 false, about 2.5 h lost to stalled runs; Codex had caught both real P1s of the run. | `acceptable` |
| `t13` (`d10`) | Operator approved 2026-10-07: AC4 (a failing run's output becomes the agent's next instruction) cannot be met without carry, since a gate->agent back edge is rejected as a cycle; the engine has no branch step, so `gate_verdict` is how `no_gate`/guard end without a push; the gate step needs a code port | `acceptable` |
| `t15` (`d11`) | Operator decision 2026-10-07: prefer Codex over Qwen; spark is freed for other loads | `acceptable` |
| `t17` (`d12`) | Operator chose option A 2026-10-07: t17's workflow needs four GitHub actions per run, but only a rule's single terminal action can reach action ports; workflow steps route by step kind only | `acceptable` |
| `t17` (`d13`) | Operator confirmed 2026-10-07: one trigger type per rule; the per-PR limit must hold across all fixer triggers; no editor change needed | `acceptable` |
| `t17` (`d14`) | Operator approved 2026-10-07: `issue_comment` payloads carry no PR details and no event carried the base SHA the t13 gate needs to read culture.yaml at the base commit | `acceptable` |
| `t17` (`d15`) | the engine had no way to list or filter review threads, so spec honesty 'an untrusted thread is never handed to the agent' was enforced only by the agent; bridge thread ids are not REST comment ids | `acceptable` |
| `t17` (`d16`) | the engine had no on-failure action, so guard/test failures and `no_gate` repos posted nothing and acceptance criterion 4 (every run's final comment links the run) held for successful runs only | `acceptable` |
| `t17` (`d17`) | operator decision 2026-10-07: a rule disabled mid-run should offer to stop its current runs rather than only failing at push and posting a hand-back comment | `acceptable` |
| `t17` (`d18`) | operator decision 2026-10-07: a deny-list fixes every new repo by default and makes 'only the scratch repo' (t20) a list of every other repo | `acceptable` |
| `t14` (`d19`) | operator request 2026-10-07 while reviewing the PR fixer workflow: the flow needs a short readable explanation without prose or an LLM, and the canvas needs zoom | `acceptable` |
| `t9` (`d20`) | operator: the fixer's commit must be reviewed by another agent step and pushed by the rule's code, not by a human/main-agent review; Qwen will author PRs from tomorrow and this process is the safety net | `needs-follow-up` |
| `t9` (`d21`) | operator: the workflow needs splitting; chose rules+workflows (the product model's composition) over a new sub-workflow step kind, and asked to make the engine work for it, events and all | `needs-follow-up` |
| `t22` (`d22`) | operator: '4. after 3 cycles, yes, with noting a comment why' (re #17 Sonar issues the fixer timed out on twice) | `acceptable` |
| `t20`, `t21` (`d4`) | the record says t20/t21 run on culture-rules itself; they ran on a new scratch repo, agentculture/culture-rules-tester (operator chose to create it for t20); the record was not amended | `acceptable` |
| `t9` (`d21`) | not delivered in #17: phase 1 (run events, hop limit, budget field, completion outbox, restore reconciliation) is on the unmerged `rules/pr-fixer-split` (c918008) after 5 Codex rounds; phase 2 (the split) not started; moved to a follow-up PR by the operator | `needs-follow-up` |
| `t17` | claim c22's `pull_request opened` fallback for repos without CI was not built: a head with no checks settles `no_checks` and starts no run | `needs-follow-up` |
| `t17` | claim c33: no fixer rule fires on `github.pr.ready`; a draft becomes eligible only on its next settle or a trusted comment | `needs-follow-up` |
| `t17` | claim c19/c23: any comment by a trusted author starts a run, including ones that ask for nothing (three live cases); no `state = open` condition | `needs-follow-up` |
| `t9`, `t10` | claim c3: a step timeout never cancels the bridge job; an orphaned session held the Qwen seat about 30 min live | `needs-follow-up` |
| `t18` | claim c30: culture-fixer holds a read-only GH_TOKEN and SONAR_TOKEN, and the App key sits in spark2's node store (r16) | `risky` |
| `t22` | the fixer's instruction asks to fix "the SonarCloud issues" (~430 on #17) while the gate failed on 4; three full-budget timeouts before the `d22` hand fix | `needs-follow-up` |

## Evidence

- tests: every test the validate-delivery evidence map cites, 136 node ids (171 with parameters) — pass at `f388a02` (pytest, junit), and vitest 425/425 — pass at `f388a02`
- full suite on the delivery head: pytest 3439 passed, 1 skipped; vitest 425; Playwright 91 (4d70fb3)
- lint: black, isort, flake8, bandit, scan-secrets, markdownlint, `teken cli doctor --strict`, harness-smoke config — clean; CI on #17 at `4d70fb3`: tests, lint, web, harness-smoke, version-check, test-publish and SonarCloud green
- commits: `ca7ff75..rules/pr-fixer` (239 commits, merges listed in `git log --merges main..rules/pr-fixer`)
- PRs: culture-rules #17 (this delivery), #18 (gate section, merged), #14 (t16 probe, closed); cultureagent #52 (merged); culture-agent-template #34 (merged); lobes-cli #300 (merged), #302 (live probe); culture-rules-tester #1-#5 (fixtures and probes); guildmaster #139 (open)
- live runs (rules.culture.dev, read from run history): run-4144cb04 (t20 pass), run-47bd8394 (diff guard then cedd5ab), run-0f45b494 (d20 proof: gate-built 0e9d3f3 approved by Codex and pushed as the bot), run-0e5cd8d6 (`head_moved`), run-237e3abd (`rule_disabled`), t21 probe 4 (excluded repo, no run); `.devague/evidence-log-pr-fixer.md`
- validate-delivery records: obligations `o1`-`o36`, evidence `e1`-`e77` (67 pass, 10 fail), deltas `b1`-`b12` — llm-origin, approved by the operator 2026-10-08

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| A same-repo PR on an allow-listed repo gets a fixer run that fixes, gates, has Codex review and pushes exactly the reviewed commit as the App | high | run-0f45b494 (culture-rules-tester#4, 0e9d3f3) · `tests/rules/test_pr_fixer_review.py` · evidence `e8`, `e9` (approved) |
| No push happens without a Codex approval of that exact commit, a trusted workflow and trusted actors, even if the workflow is edited | medium | `tests/node/test_push_requires_review.py`, `tests/rules/test_trusted_workflow.py`; six Codex review rounds; no live attack tried; lapse `l4` (tests after code, proposed) |
| The diff guard rejects commits that skip tests or add suppression markers | high | run-47bd8394 attempt 1 (`test_skipped`, `suppression_marker`) · `tests/actors/test_gate.py` · evidence `e52`, `e53` |
| A disabled rule never pushes; an excluded repo gets no run; a moved head refuses the push | high | run-237e3abd (`rule_disabled`) · t21 probe 4 · run-0e5cd8d6 (`head_moved`) |
| Drafts and fork PRs get no run | high | culture-rules-tester #2, #3 (0 runs) · `tests/rules/test_pr_fixer_bundle.py` · evidence `e23`, `e24` |
| At most one fixer run per PR, with a 3-attempt budget | medium | `tests/node/test_concurrency_budget.py` · evidence `e10`, `e11`; live coalescing seen; no live budget exhaustion observed |
| Queued work keeps its full work budget and bounded history | high | run-d63748f3 (full 3600 s after queuing; 9 history entries vs 1527) · 5183b4c tests |
| Shared variables (`vars.*`) resolve in conditions and inputs, fail closed elsewhere, admin-only writes | medium | `tests/node/test_shared_variables.py`, `tests/server/test_variables_api.py` · evidence `e40`, `e59`, `e60`; not exercised live beyond the fixer's own vars |
| The fixer drives every agentculture PR to green (the announcement, c1/c21) | low | #17 needed a hand fix (`d22`); allow-list of 3 repos (`d18`); `d21` not delivered; see Remaining Work |
| Restore from backup keeps chained-rule work exactly once | unverified | `d21` is not in this delivery (follow-up PR) |

Lapse ledger evidence:

pending approval (not yet evidence): `l1`, `l2`, `l3`, `l4`, `l5`, `l6`

## Remaining Work / Follow-up

- **`d21` follow-up PR** — phase 1 on `rules/pr-fixer-split` (c918008) needs a final Codex pass; phase 2: split pr-fixer into chained rules, a per-commit review record, review-only mode, and hold the PR's key across the chain. Owner: next run.
- **Trigger fixes (fold into `d21` phase 2)** — comment intent (`/fix` or @mention, operator decision pending), `state = open` on all four rules, stop an attempt when the agent makes no commit, cancel the bridge job on step timeout, hand the fixer only the issues behind failing Sonar gate conditions.
- **`t21` probes 5 and 6** — hold one node on the previous build (operator OK pending); a comment from a non-trusted account (needs an account).
- **c22 / c33** — `pull_request opened` fallback for repos without CI; fire on `github.pr.ready`.
- **c30 / r16** — narrow culture-fixer's tokens and move the App key off spark2's node store, or amend the claim.
- **`t19`** — widen the gate section and `fixer_repos` repo by repo; the guildmaster provisioning hook (guildmaster#139) awaits the operator.
- **Reviewer account** — move the Codex bridge from `spark` to a dedicated `culture-reviewer` account when the operator creates it.
- **Adjudication** — lapses `l1`-`l6` are still proposed (the validate-delivery records are approved).
- **r22** — published wheels lack `web_dist`; the nodes run local wheels until publish is fixed.
- **Branch protection** — `main` on the enrolled repos requires no approvals and no checks; recommended before more agent-authored PRs.
