# Build Plan — Claude fixer level

slug: `claude-fixer-level` · status: `exported` · from frame: `claude-fixer-level`

> The PR fixer gains a Claude level first: Claude Code (the cultureagent claude bridge on spark) makes try 1 and Qwen reviews it, then Qwen (reviewed by Codex), then Codex (reviewed by Qwen), so fixes land faster

## Tasks

### t1 — handoff.export takes a per-step `run_as`

- instruction: Only `culture_rules`/actors/handoff.py and its tests. Keep the sudo prefix shape; the account comes from the step's config.`run_as` when present.
- covers: c8, h8
- acceptance:
  - `culture_rules`/actors/handoff.py: a step config `run_as` (an account name matching the allowlisted form) wins over `CULTURE_RULES_HANDOFF_RUN_AS`; a step without one keeps the env default; anything else is refused before running
  - tests/actors/`test_handoff.py` covers the three cases; the existing codex hand-off tests pass unchanged

### t2 — claude-fixer actor and the codex-fixer port off 8095

- instruction: Only the two actor files and tests/rules/`test_pr_fixer_bundle.py`'s actor tests.
- covers: c9, h9
- acceptance:
  - docs/rules/pr-fixer/actors/claude-fixer.json: harness claude, machine spark, `bridge_url` <http://127.0.0.1:8096>, `callback_url` <http://127.0.0.1:18765>, `bridge_token` grant:`RULES_CLAUDE_FIXER_TOKEN`, sandbox workspace-write, `concurrency_pool` claude-spark, `max_concurrency` 1
  - codex-fixer.json moves to <http://127.0.0.1:8097> (8095 is the climate stack's weather-web on spark); bundle tests assert both actors, `FIXER_ACTORS` lists both, neither is digest-pinned; scan-secrets passes

### t3 — pr-fixer-retry-failed retries a Claude timeout

- instruction: Only docs/rules/pr-fixer/rules/pr-fixer-retry-failed.json and its test.
- covers: c10, h10
- acceptance:
  - the rule's pattern is agent(`_qwen`|`_codex`|`_claude`)? and its description names the three steps; a retry test shows fix\[0\]/`agent_claude`'s timeout is retried

### t4 — pr-fix gains `agent_claude` and its own hand-off, select labels claude-fixer, new `ROLE_FIX` digest

- instruction: pr-fix.json, `culture_rules`/actors/trusted.py (add the digest only), and the pr-fix/select/describe tests.
- depends on: t1
- covers: c7, h7, c6, h6, c12, h12
- acceptance:
  - pr-fix.json body: `agent_claude` (actor claude-fixer, workspace-write, `require_commit`, when fixer == claude-fixer), `handoff_export_claude` (`run_as` culture-claude-fixer) and `handoff_import_claude` feeding select's new claude source; the codex steps' `run_as` is culture-codex-fixer
  - `test_pr_fix_builds_and_gates` asserts the body and each when; a select test shows a Claude commit is labelled claude-fixer; the new digest is in `ROLE_FIX` with the three old ones; the describe test matches; git diff shows no change to review.py, `fixer_levels.py`, queue.py or `fixer_cooldown.py`

### t5 — The seeded three-level ladder and the chain through all three levels

- instruction: variables.json, tests/rules/`chain_world.py` and the level/queue/review-routing tests.
- depends on: t2, t4
- covers: c1, h1, c2, h2, c3, h3, c4, h4, c5, h5
- acceptance:
  - variables.json seeds level 1 claude-fixer (reviewer qwen-reviewer, pool claude-spark, queue pr-fixer-claude, enabled=false), level 2 qwen-fixer (codex-reviewer, qwen-spark2, pr-fixer), level 3 codex-fixer (qwen-reviewer, codex-spark, pr-fixer-codex, enabled=true); `CHAIN_VARIABLES` matches
  - a chain test runs one story through three tries on fakes (claude reviewed by qwen, qwen by codex, codex by qwen); a queue test lands level 1 in pr-fixer-claude and keeps the pr-fixer fallback; with level 1 disabled try 1 goes to qwen-fixer

### t6 — Docs, operator setup for the Claude level, CHANGELOG and 0.20.0

- instruction: Docs and release files only.
- depends on: t5
- covers: c11, h11, c18, h17, c15, h14, c16, h15, c17, h16
- acceptance:
  - docs/operations/pr-fixer.md: an ordered operator section for the Claude level (account, claude login, bridge unit on 8096, grants, sudoers for the export, the `fixer_levels` flip after t6), three-level sections, the hand-off per account, the codex-fixer port 8097, the digest table; markdownlint and the describe test pass
  - CHANGELOG 0.20.0 says the Claude level ships disabled, Claude is not a reviewer, and claims no speed-up; version 0.20.0; release.json 0.20.0 with no new rules or variables; the approved d9 rollout-cli record carried in

### t7 — Live: roll out 0.20.0, then after cultureagent t6 the operator flips `fixer_levels` and a tester story runs on Claude

- depends on: t6
- covers: c14, h13
- acceptance:
  - 0.20.0 rolled out through the CLI and verified; after t6 and the flip, a /fix on a tester PR shows `agent_claude`, the claude hand-off, qwen-reviewer's verdict and the App's push in its run

## Risks

- [unknown_nonblocking] Needs the operator's culture-claude-fixer account, claude login and bridge unit (cultureagent t6) before the live task (task t7)
