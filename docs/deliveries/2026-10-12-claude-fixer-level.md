# Delivery Summary — Claude fixer level

plan: `claude-fixer-level` · run: `complete` · date: `2026-10-12`
baseline: `devague summary skeleton`

## Intent

> The PR fixer gains a Claude level first: Claude Code (the cultureagent claude bridge on spark) makes try 1 and Qwen reviews it, then Qwen (reviewed by Codex), then Codex (reviewed by Qwen), so fixes land faster

After: pr-fix.json has three agent paths with per-account hand-offs, claude-fixer ships as an actor, and the seeded ladder is Claude (disabled until t6), Qwen, Codex

The run built and shipped the level as 0.20.0 (#65). It turned the level on and ran the live check (t7), which failed at the review. The run then fixed that through deviation d1 (#66, 0.20.1, and cultureagent#56, 0.15.1), and passed t7 on a re-run. "Fixes land faster" is not measured here; see Remaining Work.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — handoff.export takes a per-step `run_as`
- `t2` — claude-fixer actor and the codex-fixer port off 8095
- `t3` — pr-fixer-retry-failed retries a Claude timeout
- `t4` — pr-fix gains `agent_claude` and its own hand-off, select labels claude-fixer, new `ROLE_FIX` digest
- `t5` — The seeded three-level ladder and the chain through all three levels
- `t6` — Docs, operator setup for the Claude level, CHANGELOG and 0.20.0
- `t7` — Live: roll out 0.20.0, then after cultureagent t6 the operator flips `fixer_levels` and a tester story runs on Claude

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `handoff.export` takes `config.run_as` and validates the sudo prefix (one account selector, `-n`/`-H` only, `--` required), after Codex's two P1s and one P2 (#65, `e795347`) |
| `t2` | delivered | `claude-fixer.json` (bridge `127.0.0.1:8096`, pool `claude-spark`); `codex-fixer` moved to 8097 (#65) |
| `t3` | delivered | `pr-fixer-retry-failed` pattern `agent(_qwen\|_codex\|_claude)?` (#65) |
| `t4` | delivered | `agent_claude`, `handoff_export_claude` (`run_as` culture-claude-fixer), `handoff_import_claude`, `select` source `claude`; `ROLE_FIX` `sha256:2255b082…` (#65) |
| `t5` | delivered | the three-level seed (level 1 disabled) and `tests/rules/test_claude_ladder_chain.py` (#65) |
| `t6` | delivered | `docs/operations/pr-fixer.md` section 10, CHANGELOG, 0.20.0 (#65) |
| `t7` | delivered, after `d1` | 0.20.0 rolled out 2026-10-12 16:16Z; the operator's spark setup, actors and the flip; the first live try failed at the review (d1); after 0.20.1 and cultureagent 0.15.1 on spark2, the re-run on tester#9 passed end to end at 22:20Z |

## Mid-work Decisions

- `d1` — t7's live check on tester#5 (dispatch run-30ab94e4, review run-f3776c74): `agent_claude`, `handoff_export_claude` (as culture-claude-fixer), `handoff_import_claude` and the gate passed, but `review_qwen` ended `permission_blocked` (Qwen asked to run a shell command in plan mode; the bridge answers permission requests 'cancelled'), so the verdict was `review_invalid` and nothing was pushed. Level 1 is set back to enabled=false (`fixer_levels` v3). Fix: the reviewer brief tells the reviewer to read and search rather than run commands, and the qwen bridge removes the shell tool from plan-mode (reviewer) sessions; then t7 is re-run. — Operator, 2026-10-12: 'Turn level 1 off (Recommended)' after the first live try. Qwen Code 0.25.0 maps an ACP 'cancelled' answer and its only reject option ('cancel', kind `reject_once`) to the same outcome, so a different answer cannot help. Operator then confirmed both guards: a line in the review brief, and the cultureagent qwen bridge starting a plan-mode session with --exclude-tools `run_shell_command`.
- The operator's spark setup ran as two scripts in the operator's own terminal, not through `!`. sudo needs a password prompt, and the `!` shell has none. The scripts were the bridge account and unit (`setup-claude-bridge.sh`), then the hand-off's sudoers line and the node drop-in with the Claude token (`setup-claude-level.sh`).
- `qwen-reviewer` was registered with `qwen-fixer`'s live `bridge_url` and `callback_url`. Its digest matched the one 0.20.0 pins (`sha256:94ea1151…`), checked before the write.
- Level 3 (`codex-fixer`) stays `enabled=false` live: the `culture-codex-fixer` account is not set up.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t7` (`d1`) | Operator, 2026-10-12: 'Turn level 1 off (Recommended)' after the first live try. Qwen Code 0.25.0 maps an ACP 'cancelled' answer and its only reject option ('cancel', kind `reject_once`) to the same outcome, so a different answer cannot help. Operator then confirmed both guards: a line in the review brief, and the cultureagent qwen bridge starting a plan-mode session with --exclude-tools `run_shell_command`. | `needs-follow-up` (now followed up: #66 and cultureagent#56 shipped, and t7 passed) |

## Evidence

- tests: the full suite on #65 (6356 passed) and #66 (6357 passed); cultureagent#56 (1315 passed). The sandbox-only socket tests were deselected.
- live: `e1` (fail): tester#5, dispatch run-30ab94e4, review run-f3776c74, `review_qwen` `permission_blocked`. `e2` (pass): tester#9, dispatch run-4a009593, review run-a141c582, publish run-8cb46638, App push `e8d8e19`. Both were filed against obligation `o1` on `c14` and are proposed until the operator confirms them.
- commits: `e795347` (#65, 0.20.0), `9023caf` (#66, 0.20.1), cultureagent `e89b6fc` (#56, 0.15.1)
- rollouts: 0.20.0 verified 2026-10-12 16:16Z; 0.20.1 verified 21:41Z (`culture-rules rollout verify`)
- PRs / issues: #56 (the level), #65, #66, cultureagent#55 (the bridge, t6), cultureagent#56, culture-rules-tester#9

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| A `/fix` on a tester PR runs try 1 on `claude-fixer`, the hand-off carries its commit to spark2's gate, `qwen-reviewer` reviews it, and the App pushes (`c14`) | high | `e2`: tester#9, App push `e8d8e19` |
| The hand-off export runs as `culture-claude-fixer` through the per-step `run_as` | high | `e1` and `e2`: `handoff_export_claude` succeeded both times |
| A plan-mode Qwen review finishes with a verdict | medium | `e2`, one live review: approve, 0 findings. A single run, not a sample |
| Fixes land faster with Claude first (`c1`'s "so fixes land faster") | unverified | no measurement yet; not claimed |

## Remaining Work / Follow-up

- Measure whether Claude-first fixes land faster than Qwen-first — read try counts and times from the fixer runs over the coming weeks (operator / culture-rules).
- Level 3 (`codex-fixer`): the `culture-codex-fixer` account and its bridge on 8097 are still operator hand-turns (section 9).
- The Claude fixer could not run `pytest` on the first try; on the second it did (134 passed). Whether `fixer_allowed_tools` should list `uv run pytest` explicitly is still open.
- culture-rules#67: the diff guard refuses a protected `pyproject.toml` that a conflict resolution must touch; tester#5 waits for it.
- A PR whose checks fail waits up to `checks_settle_timeout_s` (900 s) when another app's suite (SonarQube Cloud, `claude`) stays `queued`; the operator may list those apps in `ignored_check_apps`.
