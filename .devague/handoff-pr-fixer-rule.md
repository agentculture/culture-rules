# Handoff: pr-fixer-rule workforce run

Working state for resuming after context compaction. Rewritten 2026-10-07 ~04:00 by
the main agent. The authoritative records are the devague frame, plan and delivery
store; this file points at them and adds what they do not hold.

## Original request (operator, verbatim intent)

"Set up a rule that when we have a new PR, an agent gets it and starts fixing the
issues (SonarCloud, failing tests, etc.) for all repos. What are we missing? We can
re-use bridges from ../culture-nodes, take them, or commit on separate repos. I'm
also open to using vercel agent sdk for wrapping harnesses." Flow: /scope -> /think
-> /challenge -> /spec-to-plan -> /assign-to-workforce, then live testing and
dogfooding, /validate-delivery and /summarize-delivery as the last steps before the
final PR.

## Artifacts

- Spec: `docs/specs/2026-10-05-pr-fixer-rule.md` (frame `pr-fixer-rule`).
- Plan: `docs/plans/2026-10-06-pr-fixer-rule.md` (24 tasks, 9 waves;
  `devague plan show`, `devague plan waves --json` gives every brief verbatim).
- Split (gate 2): `docs/plans/2026-10-06-pr-fixer-rule-split.md` (hand-added
  "Review gates" section, amended by d6).
- Deviations d1-d18: `devague deviate --list`. Risks r1-r20: `devague plan show`.
- Evidence log for /validate-delivery: `.devague/evidence-log-pr-fixer.md`.
- Ops recipe and gate repo list: `docs/operations/pr-fixer.md`.

## Key operator decisions (do not re-litigate)

- Bridges in agentculture/cultureagent (0.14.0 on PyPI). Vercel AI SDK rejected.
- The GitHub App pushes, never the agent; the engine's test gate decides push vs
  hand back; gate read from culture.yaml `gate:` on the PR's BASE commit; commands are
  configuration, never defaults in code.
- Fixer machine spark2, account `culture-fixer`; Qwen bridge only there (d8, no Codex
  on spark2). Fork PRs and drafts skipped; quiet period = workflow `wait` step.
- Trusted comment authors = shared variable `trusted_authors`; fifth editor tab
  Variables. Replaces culture-nodes' pr-upkeep. Never merges, never force-pushes.
- culture-rules is the live test repo (d4); App keeps Actions: write (d5).
- Push announcements: the App's own PR comment (rules.culture.dev).
- The fixer is FOUR rules sharing one workflow, with a GLOBAL per-PR concurrency key
  (d13). PR facts for every trigger via d14.

## Deviations d1-d14 (all approved)

d1 bridge callbacks on an API route + node redelivery; d2 self-tag exempts only
check-completion types; d3 `Executor.deliver(attempt=)`; d4 culture-rules is the test
repo; d5 App keeps Actions write; d6 reviewer differs from implementer (amended by
d9); d7 save/enable refused while any online node lacks the `variables` capability;
d8 no Codex on spark2; d9 Codex is the reviewer again; d10 t13's `retry_until`
`carry`, `github.push` `gate_verdict`, BuiltinCodePort; d11 Codex preferred over Qwen
as implementer (Qwen fallback on `cortex-spark2` only); d12 built-in `action` code
step; d13 four fixer rules + global concurrency key; d14 base_sha everywhere + PR
comment enrichment + checks_settled base_sha; d15 `github.threads` builtin (trusted-only
threads to the agent and replies); d16 rule `on_failure` action (hand-back comment); d17 disabling a rule offers
'Stop N current runs?' (approve cancels them); d18 allow-list `vars.fixer_repos` (+ guildmaster#138 to add
repos at provisioning). Operator also confirmed the
`trusted_authors` seed and accepted r16 (App key on spark2).

## Workforce rules in force

- Integration branch `rules/pr-fixer` (main checkout). Task branches
  `rules/pr-fixer-<tN>`, worktrees `../.worktrees.culture-rules/pr-fixer-<tN>/`.
- Implementer: Codex preferred (`codex exec -s workspace-write -c
  'sandbox_workspace_write.writable_roots=["/home/spark/git/culture-rules/.git","/home/spark/.cache"]'
  -c sandbox_workspace_write.network_access=true "<brief>"` from the worktree);
  Opus/Sonnet subagents; Qwen only as fallback and only `qwen -m cortex-spark2`
  (spark must stay free; spark's own vLLM is down after the reboot anyway).
- Reviewer never the implementer: Codex reviews Opus/Sonnet/main-agent work
  (`codex review --base rules/pr-fixer` in the worktree); an Opus subagent reviews
  Codex-built work. Main agent verifies every finding; max 3 rounds; full suite
  before and after `git merge --no-ff`; remove worktree. Wave review afterwards
  (`--base rules/pr-fixer-wave-<N>-base`, run in a throwaway detached worktree).
- Version bump once, in the final PR (Codex flags it every time; ignore).
- NEVER write an unquoted heredoc containing backticks (it once ran `uv sync` in the
  main checkout and removed 25 extras; fixed with `uv sync --all-extras`).
- Status loop: cron `5134657f` at :07/:37; one-shot `11f2abed` at 06:13 to start the
  Codex reviews (session-only jobs; recreate after a restart).

## Current state (2026-10-07 ~04:00)

Integration branch `rules/pr-fixer` head after d14 record (`devague` commit);
last full suite 2669 passed, 1 skipped.

| Task | State |
|---|---|
| Waves 1-2 (t1-t10, t14, t16, t18) | Merged, incl. wave-2 review fixes |
| t12 checks settle | Merged |
| t13 gate + diff guard | Merged (d10) |
| t15 five-tab docs | Merged (Codex-built, Opus-reviewed) |
| t19 gate rollout | Batch 1 merged: culture-agent-template#34, devague#122, steward#85, guildmaster#137; list in docs/operations/pr-fixer.md; culture-rules gets it via the final PR |
| t11 concurrency key + budget | MERGED (befc925) after 3 Codex rounds; r18/r19 open |
| t17a built-in action step (d12) | MERGED (6b55087) after 3 Codex rounds |
| t17b PR facts (d14) | MERGED (edb9ed8) after 2 Codex rounds; facts validated, lookup bound covers secret resolve |
| t17 fixer rule + workflow as data | Branch `rules/pr-fixer-t17` rebased on the merges (583eb29, 61b55e5 d15, af18543 d16); suite 2899; Codex r1 P2 (on_failure double-add) fixed by Opus `2026501`, suite 2905; Codex r2 at 13:16 |
| r14 fix | Codex WIP hit the limit; Opus finished `fee5e09` on `rules/pr-fixer-r14` (shared watermark, find_events + Mongo index, bounded); suite 2833; Codex review at 13:16; r20 rollout notes |
| r19 (+r18) fix | Opus `d8016a6` on `rules/pr-fixer-r19` (non-owner completion guard write); Codex review at 13:16 |
| t17 d18 allow-list + `variables add/remove` | DONE `5ef0ec1` on rules/pr-fixer-t17 (suite 2931); Codex review at 13:16 (cron 1de3f115) |
| t17c (d17) | Opus `5653634` on `rules/pr-fixer-t17c`; Codex review at 13:16 |
| t20 sandbox repo | agentculture/pr-fixer-sandbox CREATED (public, guild create finished by hand after a black genesis-gate failure; configure-repo applied, SONAR_TOKEN set). Ledger registration committed-to-be in guildmaster worktree `../.worktrees.guildmaster/register-pr-fixer-sandbox` (branch ledger/register-pr-fixer-sandbox, docs/skill-sources.md only) — guildmaster PR #139 opened (operator OK). App + SonarCloud installed by operator |
| wave-3 review | interrupted by the Codex limit; re-run at 13:16 (cron b2c896e0) in detached worktree `wave-3-review` |
| t20-t24 | Not started |

Open risks worth carrying: r16 push on spark2 needs the App key there (t20); r15 agent can still
read untrusted threads; r14 t12's 503-on-arm-failure relies on GitHub redelivery, which is not automatic (fix: recover from stored events in the settle tick); r9 intermittent test, r10 variable history doc size, r11
bridge token visible to agent, r12 permissionless public issue creation by the
fixer token, r13 dedup vs must_after chaining.

## Next steps

1. t17b has reported (e2507f5); it is in the 06:13 Codex batch.
2. 06:13: `codex review --base rules/pr-fixer` in pr-fixer-t11, pr-fixer-t17a,
   pr-fixer-t17b; verify; route fixes; merge t11, then t17a, then t17b (full suite
   before/after each; resolve the validate.py conflict).
3. Codex review of wave 3 as a whole.
4. t17 (main agent), on the merged base. Design already settled:
   - Four rules `pr-fixer-checks` (github.pr.checks_settled), `pr-fixer-comment`
     (github.comment.created), `pr-fixer-review` (github.review.submitted),
     `pr-fixer-review-comment` (github.review_comment.created); all `enabled: false`,
     `concurrency_key: "pr-fixer:{trigger.data.repository}#{trigger.data.number}"`,
     `max_attempts: 3`, placement machine spark2; condition: head_repo == base_repo,
     not draft, repository not in vars.fixer_excluded_repos, and for comment/review
     rules author in vars.trusted_authors; rule action = github.comment with the run
     link (App actor `github-app`).
   - Workflow `pr-fixer`: wait (quiet period, head_unchanged guard) -> retry_until
     (max 3, until verdict in [pass, no_gate], carry instruction){ actor_task on
     placement.actor `qwen-fixer` (mode yolo, threads filtered to trusted_authors) ->
     code builtin gate (worktree, base_sha, start_sha=head_before,
     commit_sha=head_after) } -> code builtin action github.push (source = bundle,
     gate_verdict = verdict, runs on spark2) -> for_each code builtin action
     github.review_reply over threads_addressed. Action-step params may only
     reference `inputs.*` (t17a), so wire values through edges.
   - Seed variables: trusted_authors, ignored_check_apps (["claude"]),
     fixer_excluded_repos, fixer_protected_paths, checks_settle_timeout_s (900),
     checks_settle_min_s (60) via `culture-rules variables set ... --apply`.
   - Committed as importable files (`rules/<id>.yaml`, `workflows/<id>.yaml`) under
     e.g. docs/rules/pr-fixer/ plus a seed script; AC tests by replay.
   - Also update the design canvas (claude.ai artifact Jgm3JPnAhKWpeiCxFXvNBi, row
     "Chosen") with the Variables tab (owed from t14).
5. t20: OPERATOR APPROVED (2026-10-07 ~12:45) the plan: release PR (cicd, version bump) ->
   operator merges -> upgrade spark/thor/orin/spark2 + API -> spark2 node secrets
   RULES_QWEN_FIXER_TOKEN + RULES_GITHUB_APP_PRIVATE_KEY and GATE_RUN_AS -> register
   qwen-fixer -> seed variables, import workflow, import rules -> enable the four rules
   (allow-list = pr-fixer-sandbox only) -> sandbox PRs (seeded, draft, fork). App and
   SonarCloud are installed on pr-fixer-sandbox (operator). Previously: Upgrade all
   nodes (d7 needs it); on spark2 node add `--secret RULES_QWEN_FIXER_TOKEN` and
   `CULTURE_RULES_GATE_RUN_AS=sudo -n -u culture-fixer -- /usr/bin/env
   PATH=/home/culture-fixer/.local/bin:/usr/local/bin:/usr/bin:/bin`; register actor
   `qwen-fixer` (docs/operations/pr-fixer.md section 4).
6. t21 live, t22 dogfood, t23 /validate-delivery, t24 /summarize-delivery, final PR
   via the cicd skill with the version bump.

## Machines

- spark: API, node, tunnel units active after the 2026-10-07 reboot; culture-rules
  Mongo up; spark's vLLM (cortex) did NOT come back (fine: spark is to stay free).
- spark2: back (reach it as `ssh spark2`; the bare IP has no known_hosts entry). Node
  active; bridge on :8093 answers 401.
- spark2 setup (done, verified before the reboot): account `culture-fixer` (spark's
  key authorized), uv, grant, Node 24, Qwen Code 0.24.7, gh 2.102 in its ~/.local;
  qwen bridge user unit on 100.93.248.8:8093; grant store FIXER_QWEN_BRIDGE_TOKEN,
  FIXER_CORTEX_API_KEY, FIXER_GITHUB_TOKEN (fine-grained read-only), FIXER_SONAR_TOKEN;
  sudoers `/etc/sudoers.d/culture-fixer-gate` (`spark2 ALL=(culture-fixer) NOPASSWD:
  /usr/bin/env`), run-as path probed (evidence log).

## Carry-forward notes

- devex refuses steward's `backend: colleague` (opened steward#85 with gh, signed by
  hand); cultureagent has no culture.yaml (swapped out of t19 batch 1).
- Fine-grained PATs have no Checks permission; the engine reads checks via the App.
- Qodo has no credits (affects t21/t22). steward doctor crash (r7).
- t14's Variables tab cannot create a new variable (CLI/API only) — note in summary.
- t12 picked checks_settle_timeout_s fallback 900 s and checks_settle_min_s 60 s.
