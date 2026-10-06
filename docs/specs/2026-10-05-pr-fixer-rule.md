# pr-fixer rule

> A new PR in any AgentCulture repo is picked up by a fixer agent that drives it to green: SonarCloud gate, failing checks and unresolved review threads fixed on the PR branch, never merged

## Audience

- The operator (who reviews and merges PRs) and the AgentCulture repo agents whose PRs currently stall on Sonar, CI and review threads
  - instruction: the exported spec names the operator as the merge gate and the PR authors as beneficiaries

## Before → After

- Before: Every PR is driven to green by hand: an agent or the operator runs the cicd skill's status/await, reads Sonar, fixes, and replies to threads; culture-nodes' pr-upkeep sweep did this centrally but has been taken down
- After: A same-repo PR in any agentculture repo gets a fixer run on spark2 without anyone asking: the agent fixes Sonar issues, failing checks, and review comments from the operator, Qodo and a pre-approved list of users and apps (comments from anyone else are ignored) in a worktree; the engine's tests gate each commit, the App pushes passing commits, and the operator only reviews and merges
  - instruction: walk one real PR from open to green with no human action before merge review

## Why it matters

- Fixing Sonar issues, failing checks and trusted reviewers' comments is the bulk of PR toil across ~100 repos; doing it event-driven with a test gate keeps quality without the operator babysitting each PR

## Requirements

- The GitHub webhook maps `pull_request` synchronize/`ready_for_review` and `check_suite`/`workflow_run` completed to typed events, and event data carries head SHA, head branch, head repo (for the fork skip), base, draft and PR author (not just sender)
  - honesty: A synchronize event pushed by the App arrives tagged `self_authored` and fires no fixer run; one pushed by a human does
- An async agent dispatch that targets an arbitrary repo+PR branch in an isolated worktree and reports completion back to the executor (not a sync step holding a worker)
  - honesty: The engine-node worker is free while the agent runs: the agent step returns accepted and completion arrives later through deliver(), surviving a node restart
- At most one fixer run per PR at a time, and a per-PR attempt budget stops fix/test/push cycles; because the App pushes, its own synchronize events are skipped by the existing `self_authored` check
  - honesty: Two events for the same PR within a minute produce exactly one active fixer run; after the attempt budget is spent the PR gets a comment and no further runs until a human pushes
- The rules-culture-dev App gains Contents: read & write and Checks: read; the org installation re-approves the new permissions (operator hand-turn)
  - honesty: The App's installation token can push a commit to a same-repo PR branch and read check runs, verified on one scratch repo before the rule is enabled
- A test-gate step between the agent and the push: it runs the repo's test command on the agent's commit in the worktree; pass leads to an App push, fail returns the failing output to the agent, up to a per-PR attempt budget, after which the PR gets a comment and no push
  - honesty: A commit that fails the repo's tests is never pushed: the gate's verdict is recorded on the run and the failing output is what the agent receives on its next attempt
- A github.push action kind (as the App, non-force, to the PR head branch only, refusing when the remote head moved since the agent started)
  - honesty: github.push refuses when the remote head SHA differs from the SHA the agent started from, and only ever targets the PR's own head branch in its own repo
- The comment-author allowlist has two parts. It is defined once as the shared variable `trusted_authors` in the Variables tab (operator, Qodo's bot, pre-approved users and apps). The fixer rule's condition carries a field that references it: an 'in' predicate over the event author whose items field is set to vars.`trusted_authors`, picked in the condition editor. The rule's workflow inputs reference the same variable. A comment or review from a listed author fires a run, and only threads from listed authors are handed to the agent
  - instruction: in the editor, open the fixer rule's condition: the author check shows a variable picker set to `trusted_authors`; edit the variable in the Variables tab and confirm the rule fires on the new author without the rule being edited
  - honesty: A thread opened by an account not on the allowlist is never handed to the agent and never fires a run, even if it mentions the bot
  - honesty: The list's contents live only in the `trusted_authors` variable, and each rule's condition holds a reference to it in a field. Editing the variable changes every referencing rule without editing them. Pointing a rule at a different variable, or removing the check, is done in that condition field. The rule and workflow never hold a copied list
- The engine resolves `vars.<name>` from the shared variable store when it evaluates a condition and maps workflow inputs; WorkflowRef.inputs accepts a {"$var": name} reference; the API, CLI noun group (variables), MCP tools, openapi.json and explain catalog gain the variable verbs
  - honesty: Changing `trusted_authors` in the Variables tab changes what every referencing rule fires on and what its workflow receives on the next event, with no rule edited; a rule referencing an undefined variable is refused at save time
- The 'exactly four primary tabs' constraint becomes five (Rules | Workflows | Actors | Variables | Statistics): CLAUDE.md, QWEN.md, AGENTS.override.md, AGENTS.colleague.md, the spec and the App.test tab-order test change together, and the design canvas gains the Variables tab
  - honesty: All four harness prompt files and the engine-editor spec state the same five tabs, and the web test asserts that order

## Honesty conditions

- The fixer runs from a GitHub event, not a sweep: no cron rule is needed for a new PR to be picked up
- The App's credentials are never used for a merge or a force-push: github.push rejects non-fast-forward updates and no merge action kind exists
- `culture_rules` core imports still have zero third-party runtime dependencies after the change (import check passes with no extras installed)
- The operator is the only party who merges; repo agents need no change to their own setup to benefit
- No automated PR fixer is running anywhere today (pr-upkeep is down, no repo workflow runs an agent on PRs)
- Between PR open and merge review, the only pushes to the PR branch from the fixer path come from the App, after a passing test gate
  - instruction: read the PR's push history: every fixer-path commit on the head branch is authored by rules-culture-dev\[bot\] and has a passing gate verdict on its run
- Sonar, failing checks and review threads are the issues the fixer targets; anything else on the PR (design feedback, scope) is left to humans
- The success run is recorded on the culture-rules run history (rule firing, agent attempts, gate verdicts, App push SHAs) so the numbers are read off evidence, not reported by the agent

## Success signals

- On a test PR seeded with one Sonar issue and one failing test, within 30 minutes the App pushes at most 3 commits, CI is green, the Sonar gate passes, and the PR has 0 unresolved threads; a fork PR and a draft PR get 0 fixer runs
  - instruction: run against a scratch agentculture repo; read the result with the cicd skill's status verb

## Scope / boundaries

- The fixer never merges and never force-pushes; merging stays a human gate. It replaces culture-nodes' pr-upkeep lane (taken down), porting its Sonar/Qodo/failed-check readiness logic
- No SonarCloud client code in the `culture_rules` core library; Sonar state is read agent-side or via a probe/http.call

## Non-goals

- Vercel AI SDK is not a dependency of `culture_rules` or the engine node; harness wrapping stays in Python (bridges/ACP), behind an optional extra
- Fork PRs and PRs from non-members are skipped (condition on head repo == base repo); fixing them is a later decision

## Assumptions

- The fix loop itself is the existing agent-side tooling — devex pr read/await/reply plus the sonarclaude skill — run headless in the PR worktree; the fixer machine needs `SONAR_TOKEN` and a GitHub credential
- 'All repos' means the agentculture org (~100 repos, App installed org-wide); repos without sonar-project.properties are fixed on checks and threads only

## Scope exploration

- `s1` — `culture_rules/server/hooks/github.py`: `_TYPES` (l.47-54) maps only pr opened/closed/reopened, `issue_comment`, issues, review submitted; synchronize/`check_run`/`check_suite`/`workflow_run` return ignored; `_data` (l.104-122) has no head SHA/branch/draft/PR author
  - seeds: `c2`
- `s2` — `culture_rules/actors/agent.py + node/actors.py`: ColleagueActor is sync and repo-fixed per actor; MeshAgentActor has async deliver() but is not in production factories (node/actors.py:30,74); no worktree/checkout code anywhere in `culture_rules`
  - seeds: `c3`
- `s3` — `../culture + ../cultureagent daemons`: mesh agents take work by IRC mention, cwd pinned to the agent's own culture.yaml directory, no worktree support, no structured result; not usable as-is for cross-repo fixes
  - seeds: `c3`
- `s4` — `../culture-nodes/adapters/`: each bridge is its own stdlib-only package (dependencies=\[\]) speaking POST /v1/invocations + callbacks; claude-code/workspace.py does git worktree add and measures HEAD/diff itself; preflight/workspace/reap/preserve duplicated across bridges; ledger-claim semantics need a simpler result schema outside culture-nodes
  - seeds: `c4`
- `s5` — `docs/actors/github.md`: live App rules-culture-dev (id 5183824) is installed on all agentculture repos with Issues RW, Pull requests RW, Metadata R only (l.41) — no contents write, no checks read; only github.comment exists as a GitHub action
  - seeds: `c5`
- `s6` — `culture_rules/events/hook_sink.py + engine/matching.py + actors/limits.py`: `self_authored` skip covers only the App's params.`self_identity`; fire cap is 60/h per rule; limits.py has `max_concurrency` and `token_budget` per actor but no per-PR concurrency key
  - seeds: `c6`
- `s7` — `.claude/skills/cicd + sonarclaude + devex`: cicd status/await already report Sonar gate+issues, hotspots, CI checks and unresolved threads and exit non-zero on failure; devex pr reply resolves threads; nothing edits code — needs an agent session
  - seeds: `c7`
- `s8` — `../culture-nodes/examples/pr-upkeep`: existing sweep-based PR upkeep lane (Sonar, Qodo, failed checks over `PR_UPKEEP_REPOSITORIES`, fix lane + codex review lane + human-inbox gate) never merges; it is periodic, not event-triggered, and runs on the culture-nodes Go engine
  - seeds: `c8`
- `s9` — `Vercel AI SDK / ACP research + package.json survey`: claude-code/codex AI SDK providers are single-maintainer Node 22+ community packages wrapping CLIs that run their own loops (AI SDK is only a facade); ACP has an official Python SDK (agent-client-protocol); no repo here uses npm ai; cultureagent acp backend shells out to 'opencode acp'
  - seeds: `c9`
- `s10` — `culture_rules (grep sonar)`: only NOSONAR comments; probe triggers (node/`probe_trigger.py`) could poll Sonar but are fixed per rule and emit probe events, not PR events
  - seeds: `c10`
- `s11` — `agentculture org (gh repo list) + local checkouts`: gh lists 100 repos at --limit 100 (may be more); most carry sonar-project.properties; without it: agentirc, cultureflare, gitculture-cli, grant, jetson-bot, nebula-run, sparkrun and a few others
  - seeds: `c11`
- `s12` — `docs/actors/github.md (permissions, l.41)`: App currently has Issues RW, Pull requests RW, Metadata R; App-side push (decision on q2) needs Contents RW, and reading check results needs Checks R
  - seeds: `c12`
- `s13` — `culture_rules/model/action_kinds.py`: action kinds are noop, message, discord.message, github.comment, jira.comment, http.call, machine.command — no push or ref-update action exists
  - seeds: `c14`
- `s14` — `../culture-nodes/adapters/qwen + codex`: qwen bridge drives 'qwen --acp' over stdio JSON-RPC and fails closed on permission requests; codex bridge wraps 'codex exec' with --sandbox; both run in a provisioned worktree
  - seeds: `c16`
- `s15` — `culture_rules/server/hooks/github.py (_TYPES, _data)`: `issue_comment`.created and `pull_request_review`.submitted are mapped and carry author=sender, enough for an author allowlist condition; `pull_request_review_comment` (inline review comments, which is where Qodo posts) is not mapped and must be added
  - seeds: `c23`
- `s16` — `culture_rules/model/condition.py + model/rule.py (WorkflowRef.inputs)`: the condition tree already supports {op: in, value: {field: data.author}, items: {literal: \[...\]}} and a {var} operand, so the firing filter works today; WorkflowRef.inputs maps only trigger refs or literals, so passing the condition's list to the workflow without a second copy needs a new mapping (e.g. a rule variable both the condition and inputs reference)
  - seeds: `c23`
- `s17` — `culture_rules/engine/matching.py (variables)`: `_condition` builds ctx {trigger, variables} and match() accepts a variables mapping, but no production caller passes one (grep 'variables=' in `culture_rules` finds none) — the {var} operand exists but always resolves empty today
  - seeds: `c25`
- `s18` — `web/src/App.tsx + App.test.tsx + CLAUDE.md domain constraints`: App.tsx documents 'exactly four tab routes' (l.12) and App.test.tsx asserts exactly four tabs in order (l.25-33); CLAUDE.md lists 'Exactly four primary tabs' as a settled constraint from issue #2 + the spec — a fifth tab amends that settled constraint
  - seeds: `c26`

## Decisions

- The culture-nodes agent bridges move into agentculture/cultureagent (cited by culture-nodes and culture-rules); the duplicated preflight/workspace/reap/preserve modules become one core and ledger-claim results become a plain result schema; only the qwen (ACP) and codex bridges are needed for the first cut
- The agent never pushes. It commits in its worktree; the engine runs the repo's tests on that commit and either pushes it to the PR head branch as the GitHub App, or hands the failure back to the agent for another attempt
- Fixer harness: Qwen Code (via the lifted qwen ACP bridge) pointed at the cortex model on spark2, or Codex via the codex bridge; the fixer runs on spark2
- The fixer fires on `check_suite` / `workflow_run` completed for a PR's head commit, with `pull_request` opened as the fallback for repos without CI; the agent never idles waiting for Sonar or CI
- Shared variables are a new first-class, versioned object with their own fifth editor tab, Variables: named values (e.g. `trusted_authors`) defined once and referenced by any rule's condition and workflow inputs via `vars.<name>`; the fixer rule's allowlist is the first one

## Open parks

- [unknown_nonblocking] Long agent runs vs step deadlines and `unsafe_retry` (agent adapters declare `supports_idempotency_key`=False); timeout/retry behaviour for multi-hour fix runs unverified

## Resolved vagueness

- [unknown_nonblocking] SonarCloud and CI finish minutes after `pull_request`.opened; whether the fixer waits (devex pr await) or the rule triggers on `check_suite`/`workflow_run` completed instead — resolved: Trigger on checks completed (`check_suite`/`workflow_run`), `pull_request` opened as fallback for CI-less repos
