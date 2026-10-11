# Claude fixer level

> The PR fixer gains a Claude level first: Claude Code (the cultureagent claude bridge on spark) makes try 1 and Qwen reviews it, then Qwen (reviewed by Codex), then Codex (reviewed by Qwen), so fixes land faster

## Audience

- The operator, who wants fixes in faster with Claude first, and the PR fixer's maintainers who keep the ladder, hand-off and reviewer pairing honest

## Before → After

- Before: Today the ladder is qwen-fixer (level 1) then codex-fixer (level 2, disabled), and pr-fix.json has exactly two agent paths
- After: pr-fix.json has three agent paths with per-account hand-offs, claude-fixer ships as an actor, and the seeded ladder is Claude (disabled until t6), Qwen, Codex

## Why it matters

- The operator wants fixes in faster: Claude makes the direct fix first, with Qwen as an independent reviewer

## Requirements

- Operator decision (culture-rules#56, 2026-10-11): the ladder is level 1 claude-fixer reviewed by qwen-reviewer, level 2 qwen-fixer reviewed by codex-reviewer, level 3 codex-fixer reviewed by qwen-reviewer, all three enabled once Claude is live; `MAX_TRIES` 3 gives one try per level
  - honesty: `test_fixer_levels_are_seeded` asserts the seeded three-level ladder exactly (actors, reviewers, pools, queues) and `CHAIN_VARIABLES` equals variables.json
- Operator decision 2026-10-11: Claude gets a new queue pr-fixer-claude (pool claude-spark); qwen keeps pr-fixer (the queue workflows' fallback) and codex keeps pr-fixer-codex
  - honesty: A queue test shows a request lands in pr-fixer-claude for level 1 and in pr-fixer for level 2, and an undefined variable still falls back to pr-fixer
- Operator decision 2026-10-11: the release ships the Claude level disabled (seeded level=1 actor=claude-fixer ... enabled=false); after the bridge's live check (cultureagent t6) passes, `fixer_levels` is set live to enable it
  - honesty: The seeded level 1 is enabled=false and a ladder test shows try 1 then goes to qwen-fixer; flipping it to true makes try 1 claude-fixer
- Operator decision 2026-10-11: qwen-reviewer reviews Claude's tries on the shared qwen-spark2 pool (reviews serialise behind Qwen fixes on spark2)
  - honesty: A review-routing test shows a claude-fixer try is reviewed by qwen-reviewer and the reviewer-is-not-implementer check passes (backends claude vs qwen)
- Operator decision (cultureagent#54): the Claude bridge runs on spark under a dedicated culture-claude-fixer account with its own claude login; its commits reach spark2's gate through the 0.18.13 hand-off, as Codex's do
  - honesty: The handoff test for the Claude path runs the export as culture-claude-fixer (the step's `run_as`) and the import on spark2 as the gate account
- pr-fix.json gains `agent_claude` (placement actor claude-fixer, sandbox workspace-write, `require_commit`, when fixer == claude-fixer) and its own `handoff_export_claude` / `handoff_import_claude` steps feeding select as source claude -> claude-fixer, so the implementer label stays exact; the new pr-fix digest is added to `ROLE_FIX` in `culture_rules`/actors/trusted.py with the old ones kept
  - honesty: `test_pr_fix_builds_and_gates` asserts the body steps and each step's when; a select test shows a Claude commit is labelled claude-fixer, never codex-fixer; the new digest is in `ROLE_FIX` and the earlier three still are
- handoff.export takes the account to run as from the step's config (`run_as`), so the Claude path runs as culture-claude-fixer and the Codex path as culture-codex-fixer; the node's single `CULTURE_RULES_HANDOFF_RUN_AS` stays the default for steps that name none
  - honesty: handoff.export tests: a step `run_as` wins over `CULTURE_RULES_HANDOFF_RUN_AS`, a step without one keeps the env default, and a `run_as` outside an allowlisted form is refused
- docs/rules/pr-fixer/actors/claude-fixer.json: harness claude (equal to the bridge's reported backend), machine spark, `bridge_url` <http://127.0.0.1:8096> (8095 is codex-fixer's bridge), `callback_url` <http://127.0.0.1:18765>, `bridge_token` grant:`RULES_CLAUDE_FIXER_TOKEN`, sandbox workspace-write, `concurrency_pool` claude-spark, `max_concurrency` 1; not digest-pinned, like codex-fixer
  - honesty: A bundle test asserts claude-fixer.json's fields (harness claude, port 8096, loopback URLs, grant name, pool) and that it is not digest-pinned; scan-secrets passes
- pr-fixer-retry-failed's step pattern names `agent_claude` too (agent(`_qwen`|`_codex`|`_claude`)?), so a Claude timeout is retried; the capacity cooldown already matches `agent_`\*
  - honesty: A retry test shows a fix\[0\]/`agent_claude` timeout is retried like `agent_qwen`'s and `agent_codex`'s
- docs/operations/pr-fixer.md gains 'Operator setup for the Claude level' (account, claude login, the bridge unit on 8096, grants `CLAUDE_FIXER_BRIDGE_TOKEN` and `RULES_CLAUDE_FIXER_TOKEN`, the sudoers grant for the export, the `fixer_levels` flip after t6) and the level sections describe three levels
  - honesty: The operator doc's Claude setup is ordered and complete (account, login, unit on 8096, grants, sudoers, the flip) and markdownlint and the describe test pass

## Honesty conditions

- A chain test (tests/rules chain world) runs a story through all three levels on fakes: try 1 on claude-fixer reviewed by qwen-reviewer, try 2 on qwen-fixer reviewed by codex-reviewer, try 3 on codex-fixer reviewed by qwen-reviewer
- git diff shows no change under `culture_rules`/actors/review.py, node/actions/`fixer_levels.py`, queue.py or `fixer_cooldown.py`
- Checked live after t6 and the flip, in the last plan task: the first story's run shows `agent_claude`, the hand-off ids, qwen-reviewer's verdict and the App's push
- The before state is read from origin/main 0.19.3: pr-fix.json's select has exactly two sources and the seeded `fixer_levels` has two levels (quoted in the scope findings)
- The after state is what the tests in h1-h11 assert on the release's tree, nothing beyond them
- Faster fixes are not claimed by this release: the success signal (c14) is checked live after t6 and the flip, and the release says the Claude level ships disabled
- docs/operations/pr-fixer.md's Claude setup section is written for the operator (ordered steps they run), and the level/hand-off sections for maintainers; the PR names both

## Success signals

- After 0.20.0, t6 and the flip, a /fix on a tester PR runs try 1 on claude-fixer, the hand-off carries its commit to spark2's gate, qwen-reviewer reviews it against the original task, and the App pushes

## Scope / boundaries

- No engine change to the queue, ladder, cooldown, review or reviewer-is-not-implementer code: they are level-count and backend agnostic (`REVIEWER_BACKENDS` stays codex, qwen since Claude never reviews)

## Non-goals

- Claude as a reviewer, and enabling the Claude level live, are not in this release; t6 and the live flip follow

## Scope exploration

- `s1` — `docs/rules/pr-fixer/workflows/pr-fix.json + node/actions/select.py`: select's sources map is the two-path assumption ({codex: codex-fixer, qwen: qwen-fixer}); select.py itself takes any number of sources, and the handoff steps feed select.`codex_`\*, so a shared hand-off would mislabel Claude commits as codex-fixer
  - seeds: `c7`
- `s2` — `culture_rules/actors/handoff.py:146,197`: the export's run-as is one node-wide env var `CULTURE_RULES_HANDOFF_RUN_AS` (culture-codex-fixer); a second account needs a per-step `run_as`
  - seeds: `c8`
- `s3` — `spark ports (ss -ltn) + actors/codex-fixer.json`: codex-fixer's bridge is on 127.0.0.1:8095; the claude bridge moved to 8096 in cultureagent#55
  - seeds: `c9`
- `s4` — `rules/pr-fixer-retry-failed.json:26, actors/review.py:163,1324-1345, node/actions/fixer_levels.py, queue.py:245`: retry-failed names only `_qwen`/`_codex`; review, ladder and queue code are backend/level agnostic; `MAX_TRIES` 3
  - seeds: `c10`, `c12`
