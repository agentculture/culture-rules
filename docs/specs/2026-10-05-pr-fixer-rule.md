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
- The rules-culture-dev App gains Contents: read & write, Checks: read and Actions: read (`workflow_run` webhooks and failing job logs need it); it does not get Workflows permission; the org installation re-approves the new permissions (operator hand-turn)
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
- The fixer fires once per PR head SHA, when every check suite on that SHA has completed except those from apps in a shared `ignored_check_apps` variable (seeded with the 'claude' app, whose suite stays queued), or after a settle timeout; individual suite completions before that do not start a run
  - honesty: On a commit with GitHub Actions, SonarCloud and GitGuardian suites, exactly one fixer run starts, after the last of them completes; a suite stuck in queued from an ignored app does not hold it back
- The `self_authored` skip applies to `pull_request` synchronize only; `check_suite`/`workflow_run` completions on an App-pushed SHA still fire the fixer (bounded by the attempt budget), so the agent sees whether its fix went green
  - honesty: After the App pushes a fix, the next check completion on that SHA starts a verification run unless the attempt budget is spent; a synchronize from the App never does
- The App installation token used for a push is minted per push, scoped to the single PR repository with contents:write only, held by the push step alone, and never placed in the agent's or the test gate's environment or worktree
  - honesty: Inspecting the agent and test-gate process environments and worktree files during a run finds no GitHub App token or private key; the push token's repositories list holds exactly one repo
- A diff guard in the gate rejects (and hands back) any fixer commit that touches protected paths or weakens checks: .github/workflows/\*\*, sonar-project.properties, coverage or lint config, deleted or skipped tests, and new NOSONAR / noqa / type-ignore markers; the protected list is a shared variable
  - honesty: A fixer commit that deletes a failing test or adds NOSONAR is never pushed; the gate verdict names the guard rule it tripped
- A github.`review_reply` action kind posts a reply to a review thread and optionally resolves it, as the App; the fixer's result lists, per thread, the commit that addressed it
  - honesty: Every thread reply and resolution made on the fixer path is authored by rules-culture-dev\[bot\]; none is authored by the operator's account
- Draft PRs get no fixer run; marking a PR ready for review (`pull_request` `ready_for_review`) makes it eligible
  - honesty: A draft PR with failing checks gets 0 fixer runs; the same PR gets a run once marked ready and its checks complete
- A rule whose condition or inputs reference a shared variable is evaluated only by nodes that advertise variable support; on any other node it fails closed (no fire, recorded as an error), never evaluating the reference as missing
  - honesty: With one node on the pre-variables version, an event matching a rule that uses not(author in vars.X) fires nothing on that node and leaves an error on the rule's history
- Writing a shared variable requires the admin role, every write creates a new version with author and time, and the Variables tab shows which rules reference each variable
  - honesty: An editor-role principal is refused when changing `trusted_authors`; an admin's change appears as a new version naming who made it
- Containment: a repo listed in a shared `fixer_excluded_repos` variable gets no fixer runs; disabling the fixer rule stops new runs and lets in-flight runs finish without pushing; every fixer run appears on the fixer machine's Statistics lane and its PR comment links the run
  - honesty: Adding a repo to `fixer_excluded_repos` stops runs for its next event; with the rule disabled mid-run, no App push follows
- Workflows gain a wait step kind: it pauses the run for a duration (the quiet period) without holding a worker, persists across node restarts, and on resume checks a guard; if the PR head moved during the wait (someone pushed) the run ends without dispatching the agent, and the next checks-completed event starts a fresh one
  - honesty: A fixer run on a PR the author pushes to during the quiet period dispatches no agent; with no push during the wait, the agent is dispatched once the wait ends, including when the node restarted mid-wait
- culture.yaml gains a top-level gate section with setup and test commands as argv lists (e.g. setup: \[\[uv, sync\]\], test: \[\[uv, run, pytest, -n, auto\]\]); culture-agent-template ships it so new repos inherit it, existing repos add it, and a repo without a gate section gets a comment from the fixer and never a push
  - honesty: The test gate runs exactly the argv lists from the PR head's culture.yaml, with no shell; a repo with no gate section gets 0 App pushes; culture's loader and steward doctor accept the new section

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
- As the fixer user, reading the engine node's secrets, the App private key or the operator's gh and claude credentials fails with permission denied
- Every fixer commit on a PR branch is a descendant of the SHA it started from and is authored by rules-culture-dev\[bot\]

## Success signals

- On a test PR seeded with one Sonar issue and one failing test, within 30 minutes the App pushes at most 3 commits, CI is green, the Sonar gate passes, and the PR has 0 unresolved threads; a fork PR and a draft PR get 0 fixer runs
  - instruction: run against a scratch agentculture repo; read the result with the cicd skill's status verb

## Scope / boundaries

- The fixer never merges and never force-pushes; merging stays a human gate. It replaces culture-nodes' pr-upkeep lane (taken down), porting its Sonar/Qodo/failed-check readiness logic
- No SonarCloud client code in the `culture_rules` core library; Sonar state is read agent-side or via a probe/http.call
- The agent and the test gate run PR code as a dedicated unprivileged Unix user on the fixer machine, with no grant secrets, App key, Sonar token or operator credentials in their environment or home
- Fixer commits are ordinary fast-forward commits authored by the App, so any of them is undone with git revert; the fixer never rewrites history

## Non-goals

- Vercel AI SDK is not a dependency of `culture_rules` or the engine node; harness wrapping stays in Python (bridges/ACP), behind an optional extra
- Fork PRs and PRs from non-members are skipped (condition on head repo == base repo); fixing them is a later decision

## Assumptions

- The fix loop reuses the agent-side read tooling — devex pr read/await and the sonarclaude skill — in the PR worktree, with read-only access; the agent holds no GitHub write credential, and replies to and resolution of review threads are done by the engine as the App, from the agent's result
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
- `s19` — `challenge pass / operations lens: memory culture-rules-build-in-progress (spark2)`: spark2 has no internet and installs from an offline wheelhouse; decision c16 places the fixer on spark2, which cannot reach GitHub, SonarCloud, Codex or package indexes as-is — raised as a pending decision
- `s20` — `challenge pass / failure-mode probe: gh api check-suites on culture-rules PR #13 head`: five check suites per commit (github-actions x2, sonarqubecloud, gitguardian, claude); the claude suite was still queued a day later; SonarCloud completed 11 min after Actions. Firing per completion would start up to 4 runs per commit; waiting for all would never fire
  - seeds: `c27`
- `s21` — `challenge pass / data-flow lens: culture_rules/events/hook_sink.py:155-163`: `self_authored` is set whenever event author == params.`self_identity`, for every event type; once checks start the fixer (c22), App-pushed commits risk being skipped and the fix-verify loop would stop after one push
  - seeds: `c28`
- `s22` — `challenge pass / security lens: docs.github.com webhook-events-and-payloads`: `workflow_run` requires Actions: read; `check_suite`/`check_run` require Checks: read; `pull_request_review_comment` and `pull_request_review_thread` require Pull requests: read (already held). c12 omitted Actions
  - seeds: `c12`
- `s23` — `challenge pass / security lens: docs/actors/github.md (installation 167755039, all repositories)`: an unscoped installation token would carry contents:write on every agentculture repo; a leak from a process that runs PR code would be org-wide
  - seeds: `c29`
- `s24` — `challenge pass / security lens: ../culture-nodes/adapters (qwen, pi READMEs) + culture_rules/actors/code.py`: the culture-nodes bridges name a dedicated Unix account per agent as the trust boundary, not a sandbox; the test gate executes the PR's own code, so the same boundary has to cover the gate
  - seeds: `c30`
- `s25` — `challenge pass / unstated-assumption lens: c13 test gate + GitHub App without Workflows permission`: a passing test run does not prove a fix: deleting the failing test or suppressing the Sonar issue also goes green; a commit touching .github/workflows would be refused by GitHub anyway since the App lacks Workflows permission
  - seeds: `c31`
- `s26` — `challenge pass / overlooked-actor lens: .claude/skills/cicd + devex (reply auto-signs from culture.yaml via gh)`: devex pr reply posts through the local gh login (OriNachum on spark); run by the fixer it would speak as the operator and needs a write credential the agent must not hold (c5)
  - seeds: `c32`
- `s27` — `challenge pass / spec-consistency lens: exported spec c21 vs requirements`: success signal c21 says a draft PR gets 0 runs, but no requirement states the draft skip; c2 only adds the draft flag to event data
  - seeds: `c33`
- `s28` — `challenge pass / migration lens: culture_rules/model/condition.py docstring (missing operands)`: every comparison with a missing operand is false but explicit negation still applies, so not(a in vars.x) is TRUE when x is missing; an older node that does not resolve variables would fire such a rule wrongly
  - seeds: `c34`
- `s29` — `challenge pass / security lens: culture_rules/auth/resolve.py + guards.py (viewer/editor/admin)`: roles exist (viewer, editor, admin); editing `trusted_authors` grants the power to steer code changes on every repo, so it is an admin-grade write
  - seeds: `c35`
- `s30` — `challenge pass / containment lens: exported spec (no kill switch, no opt-out, no run visibility claim)`: the spec says how the fixer acts on ~100 repos but not how to stop it for one repo, stop it everywhere, or see what it is doing
  - seeds: `c36`
- `s31` — `challenge pass / reversibility lens: c8, c14 (non-force push)`: clean pass: non-force pushes plus App authorship make every fixer change revertable with git revert; residual risk only if a human force-pushes over it (see the PR-author question)
  - seeds: `c37`
- `s32` — `challenge pass / migration lens: store (new variables collection)`: examined: shared variables add a new MongoDB collection, additive, no change to existing documents; not examined: backup/restore coverage of the new collection in docs/operations/backup — residual, left for the plan
- `s33` — `challenge pass / adjacent-systems lens: ../culture-nodes (bridges leaving)`: examined: bridges import nothing from `culture_nodes`; culture-nodes would cite them back from cultureagent; not examined: culture-nodes' Go actor registration against the changed result schema — left for the plan
- `s34` — `challenge pass / operations probe: ssh spark2 curl + git ls-remote (2026-10-06)`: spark2 reaches api.github.com, sonarcloud.io, pypi.org, registry.npmjs.org (200) and api.openai.com (421, reachable); git ls-remote github.com/agentculture/culture-rules works — the offline-wheelhouse note in memory is stale
  - seeds: `c16`
- `s35` — `challenge pass / q8 follow-up: culture_rules/model/workflow.py:30 (STEP_KINDS)`: step kinds are logic, ai, code, `actor_task`, `for_each`, `retry_until`; there is no wait/delay step, so the quiet period needs a new step kind (human asks already prove persisted async waits exist in the executor)
  - seeds: `c40`
- `s36` — `challenge pass / q9 follow-up: ../culture/culture_core/config.py:301-335 + ../culture-agent-template (culture.yaml, tests.yml)`: culture.yaml today holds only agents (suffix, backend); `load_culture_yaml` reads the agents list and ignores other top-level keys, so a gate section does not break culture's loader; the template CI standard is uv sync then uv run pytest -n auto; steward doctor's treatment of a new top-level key is not examined
  - seeds: `c41`

## Decisions

- The culture-nodes agent bridges move into agentculture/cultureagent (cited by culture-nodes and culture-rules); the duplicated preflight/workspace/reap/preserve modules become one core and ledger-claim results become a plain result schema; only the qwen (ACP) and codex bridges are needed for the first cut
- The agent never pushes. It commits in its worktree; the engine runs the repo's tests on that commit and either pushes it to the PR head branch as the GitHub App, or hands the failure back to the agent for another attempt
- Fixer harness: Qwen Code (via the lifted qwen ACP bridge) pointed at the cortex model on spark2, or Codex via the codex bridge; the fixer runs on spark2
- The fixer fires on `check_suite` / `workflow_run` completed for a PR's head commit, with `pull_request` opened as the fallback for repos without CI; the agent never idles waiting for Sonar or CI
- Shared variables are a new first-class, versioned object with their own fifth editor tab, Variables: named values (e.g. `trusted_authors`) defined once and referenced by any rule's condition and workflow inputs via `vars.<name>`; the fixer rule's allowlist is the first one
- The quiet period before a fixer run is a workflow wait step placed after the PR's pipelines have run; there is no no-fixer label for now
- Each repo declares its fixer test command in its culture.yaml, following the culture-agent-template / culture-rules standard
- The test command is configuration, not code: culture-rules holds no default, inferred or language-specific test command; the gate runs only the setup/test argv declared in the repo's culture.yaml gate section, and the standard values (uv sync, uv run pytest -n auto) live in culture-agent-template's culture.yaml as configuration
- The author side of a fixer push is rules.culture.dev, as used today: the App's own PR comment announces each push; no separate mesh message to the PR's author agent

## Open parks

- [unknown_nonblocking] Long agent runs vs step deadlines and `unsafe_retry` (agent adapters declare `supports_idempotency_key`=False); timeout/retry behaviour for multi-hour fix runs unverified
- [unknown_nonblocking] Capacity: one fixer machine (cortex GPU) serving ~100 repos; queueing depth, `max_concurrency` and `token_budget` for the fixer actor are unmeasured
- [unknown_nonblocking] A gate command read from the PR head's culture.yaml is itself PR-controlled: a PR could change it to 'true'. Whether to read the gate from the base branch instead is unexamined

## Resolved vagueness

- [unknown_nonblocking] SonarCloud and CI finish minutes after `pull_request`.opened; whether the fixer waits (devex pr await) or the rule triggers on `check_suite`/`workflow_run` completed instead — resolved: Trigger on checks completed (`check_suite`/`workflow_run`), `pull_request` opened as fallback for CI-less repos
