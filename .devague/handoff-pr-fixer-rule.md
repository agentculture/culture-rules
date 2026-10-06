# Handoff: pr-fixer-rule workforce run

Working state for resuming after context compaction. Written 2026-10-06 by the
main agent. The authoritative records are the devague frame, plan and delivery
store; this file points at them and adds what they do not hold.

## Original request (operator, verbatim intent)

"Set up a rule that when we have a new PR, an agent gets it and starts fixing
the issues (SonarCloud, failing tests, etc.) for all repos. What are we
missing? We can re-use bridges from ../culture-nodes, take them, or commit on
separate repos. I'm also open to using vercel agent sdk for wrapping
harnesses." Flow run: /scope -> /think -> /challenge -> /spec-to-plan ->
/assign-to-workforce, with /validate-delivery and /summarize-delivery as the
last steps before the final PR, plus live testing and dogfooding.

## Artifacts

- Spec: `docs/specs/2026-10-05-pr-fixer-rule.md` (frame `pr-fixer-rule`, 43
  claims; `devague show`).
- Plan: `docs/plans/2026-10-06-pr-fixer-rule.md` (24 tasks, 9 waves;
  `devague plan show`, `devague plan waves --json`).
- Split (gate 2, approved with Codex review gates):
  `docs/plans/2026-10-06-pr-fixer-rule-split.md`.
- Deviations: `devague deviate --list` (d1-d5). Risks: `devague plan show`.
- Live evidence log for /validate-delivery:
  `.devague/evidence-log-pr-fixer.md`. No devague obligations filed yet.
- Briefs and logs (session scratchpad, may be gone after the session):
  `/tmp/claude-1000/-home-spark-git-culture-rules/<session>/scratchpad/`
  (`brief-tN.md`, `prompt-*.txt`, `qwen-*.log`, `codex-*.log`). Briefs can
  be regenerated from `devague plan waves --json`.

## Key operator decisions (do not re-litigate)

- The bridges live in agentculture/cultureagent (shared core, qwen ACP +
  codex only). Vercel AI SDK rejected.
- The GitHub App pushes, never the agent; the engine test gate decides push
  vs hand back. The gate reads `culture.yaml` `gate:` from the PR's BASE
  branch (r1). Test commands are configuration only, no defaults in code.
- Fixer harness: Qwen Code on cortex, or Codex. Fixer machine spark2
  (internet confirmed back 2026-10-06).
- Trigger: check suites / workflow runs completed (once per head SHA, settle
  with ignored_check_apps), pull_request opened as fallback. Fork PRs and
  drafts skipped. Quiet period = workflow `wait` step. No `no-fixer` label.
- Trusted comment authors = shared variable `trusted_authors`, referenced from
  a field on the fixer rule's condition. Shared variables get a fifth editor
  tab, Variables (Rules | Workflows | Actors | Variables | Statistics).
- Replaces culture-nodes' pr-upkeep (taken down). Never merges, never
  force-pushes.
- Test repo for live probes: agentculture/culture-rules itself (d4).
- App keeps Actions: write on purpose (d5).
- Push announcements: the App's own PR comment (rules.culture.dev).

## Workforce setup

- Integration branch `rules/pr-fixer` (in the main checkout). Task branches
  `rules/pr-fixer-<tN>`, worktrees `../.worktrees.culture-rules/pr-fixer-<tN>/`.
- Lanes: two Qwen Code lanes, one task each at a time, both `qwen -m cortex
  --approval-mode yolo -o text "<brief>"` in the task worktree. The lobes
  gateway (localhost:8001, container model-gear-gateway) pools spark and
  spark2 cortex (unsloth/Qwen3.8-27B-NVFP4) under `cortex`; `-m main` pins
  spark2. Opus/Sonnet subagents for the rest; the main agent owns t17-t24
  and t18/t19.
- Merge gate: Codex `codex review --base rules/pr-fixer` (no custom prompt
  allowed with --base) per task; main agent verifies each finding before
  routing it; max 3 Codex rounds per task (P1s always fixed); full suite
  before and after `git merge --no-ff`; then remove the worktree. After a
  wave: Codex review of the wave diff (`--base rules/pr-fixer-wave-<N>-base`).
- Version bump: once, in the final PR (Codex flags it every time; ignore).
- Status update loop: cron job `270465ef` at :07 and :37 (session-only).

## Current state (2026-10-06)

Wave 1 (t1 t2 t3 t4 t5 t6 t9 t16):

| Task | State |
|---|---|
| t1 bridges | Merged as cultureagent#52; cultureagent 0.14.0 on PyPI |
| t2 webhook events | Merged (d2 applied) |
| t3 variables model/store | Qwen lane fixing 4 Codex findings (Mongo version race P1, envelope/schema guard, list content validation, name fullmatch) in `pr-fixer-t3`; prompt `prompt-t3-fix.txt` |
| t4 wait step model | Merged (null-config fix by main agent) |
| t5 github.push / review_reply | Merged (3 Codex rounds) |
| t6 gate section | Merged here; culture-agent-template#34 open, all green, awaiting operator merge |
| t9 bridge agent actor | Merged (d1, d3) |
| t16 App permissions | Done, live-verified (PR #14 probe, closed) |

Full suite on `rules/pr-fixer` at `42f827f`: 2418 passed, 1 skipped (one
intermittent unidentified failure, r9).

## Next steps

1. When the t3 Qwen run finishes: read its report, run Codex
   (`codex review --base rules/pr-fixer` in `pr-fixer-t3`), verify, merge
   with the full-suite gate, remove the worktree.
2. Wave-1 review: `codex review --base rules/pr-fixer-wave-1-base` on
   `rules/pr-fixer`; fix verified findings.
3. Wave 2: create `rules/pr-fixer-wave-2-base`; t7 (Opus), t8 (Sonnet), t10
   (Sonnet, or Qwen lane B if the operator agrees), t18 (main agent: spark2
   `culture-fixer` user, bridges as user units with `commit_author`
   `rules-culture-dev[bot] <337624453+rules-culture-dev[bot]@users.noreply.github.com>`,
   qwen actor needs `params.mode`).
4. Waves 3-9 per the plan, then t20 e2e, t21 live, t22 dogfood,
   /validate-delivery (t23), /summarize-delivery (t24), final PR via cicd.

## Carry-forward notes for later tasks

- t14 must add `github.push` / `github.review_reply` to the web action
  pickers (`web/src/rules/ActionPicker.tsx`, `web/src/actors/app-config.ts`).
- t17/t18: hand the agent's commit to `github.push` as a git bundle or a
  worktree readable by the node (a worktree owned by `culture-fixer` trips
  git `safe.directory`); set `params.commit_author` on the App actor and on
  both bridges.
- t13 must cover `.github/workflows` edits (culture-nodes' scope_guard was
  dropped in the lift).
- Bridges lose in-flight runs on restart (GET 404); t9 should treat 404 for
  an accepted id as lost.
- Qodo has no credits (not reviewing PRs) — affects t21/t22.
- steward doctor crashes on its own (r7).

## spark2 fixer machine (2026-10-07)

- `culture-fixer` exists (no extra groups; cannot read /home/spark2 or node.env). Spark's
  ed25519 key is authorized: `ssh culture-fixer@spark2`.
- Installed in its ~/.local: uv, grant 0.11.0, Node v24.13.1, Qwen Code 0.24.7, gh 2.102.0;
  git uses gh's credential helper (GH_TOKEN). cultureagent 0.14.0 qwen bridge is a user
  unit on 100.93.248.8:8093, active and enabled (no Codex on spark2, d8).
- Its grant store: FIXER_QWEN_BRIDGE_TOKEN, FIXER_CORTEX_API_KEY, FIXER_GITHUB_TOKEN
  (fine-grained read-only, OriNachum), FIXER_SONAR_TOKEN. Verified: bridge 200 with token /
  401 without, cortex 200, Sonar valid, PRs/Actions/check runs readable (public), private
  repo metadata and git fetch OK, private check runs 403 (PAT limit; the engine reads checks
  via the App), all writes 403 except permissionless public issue creation (r12, accepted;
  probe issue #16 closed).
- DEFERRED to t20 (node is 0.12.0, no bridge actor yet; d7 also needs all nodes upgraded):
  reinstall the spark2 node with `--secret RULES_QWEN_FIXER_TOKEN` (already in spark2's grant
  store; the unit does not inject it yet) and register actor `qwen-fixer` per
  docs/operations/pr-fixer.md section 4.
