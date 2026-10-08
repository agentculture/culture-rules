# Handoff: pr-fixer-rule workforce run

This is the working state for resuming after context compaction. The main agent
rewrote it on 2026-10-07 at about 19:40 IDT. The authoritative records are the devague
frame, plan and delivery store. This file points at them and adds what they don't hold.

## Read this first: status at 2026-10-08 ~17:50 IDT (compaction point)

- **#17 (culture-rules 0.13.0, gate 3)** is CLEAN at `6955315`: all CI green, SonarCloud gate OK, 41 open issues (38 python + 3 typescript S3776 cognitive complexity). The operator chose to **fix those 41 on #17 before merging** (worktree `../.worktrees.culture-rules/pr17-complexity`, branch `rules/pr17-complexity`, cut from `6955315`; list at the scratchpad `sonar17-41.tsv`, regenerate with the SonarCloud issues API for `pullRequest=17`). No subagent started yet. Then: Codex review, merge into `rules/pr-fixer`, push, CI + Sonar, operator merges.
- **Deployed live (rules.culture.dev):** d20 (agent review before any push) from wheel `7bb9026`; queue fix. Codex reviewer bridge on spark (`127.0.0.1:8094`, runs as `spark` by operator choice, read-only sandbox verified confined). `github-app` actor has `commit_author: rules-culture-dev[bot]`. All four old fixer rules enabled; workflow `pr-fixer` (single) live. Proven: culture-rules-tester#4 run-0f45b494.
- **d21 (split into chained rules; operator-approved FOLLOW-UP PR after #17):** branch `rules/pr-fixer-split` (worktree `pr-fixer-split`), head `0a4ac6f`, version 0.14.0, 3789 tests pass; already merged with `rules/pr-fixer@6955315` (`bc3482c`). Phase 1 (run events, hop cap, outbox, restore reconciliation) Codex-reviewed 7 rounds; phase 2 (pr-fix / review-commit / publish-fix, 7 rules, chain key hold, push verifies the whole chain, trigger fixes) Codex-reviewed 3 rounds + matcher fixes. Not deployed. After #17 merges: rebase/merge onto main, open the PR, deploy per `docs/operations/pr-fixer.md` "Rolling out the split" (pause, upgrade every node, seed `fixer_comment_triggers`, import actors→workflows→rules, disable workflow `pr-fixer`, enable stage rules then triggers, resume), prove on culture-rules-tester (chain + review-only mode).
- **t23/t24 done:** 36 obligations, 77 evidence (67 pass, 10 fail), 12 deltas, all operator-approved; summary `docs/deliveries/2026-10-06-pr-fixer-rule.md`, linked from #17's description.

### Waiting on the operator

1. Merge #17 (after the 41-issue pass).
2. t21 probe 5 (hold one node on the old build ~15 min) and probe 6 (a comment from an account outside `trusted_authors`). Probes 1–4 pass.
3. Branch protection on `main` (0 approvals, no required checks on culture-rules, lobes-cli, culture-rules-tester) — offer to draft settings.
4. GitGuardian = **d25** (pending): reading — never auto-fix a GitGuardian finding, post it as a review comment; settle stops blocking on empty queued suites.
5. guildmaster#139 (ledger: culture-rules-tester as downstream consumer).
6. Lapses `l1`–`l7` (proposed).

### Then

- the 41 complexity refactors on #17 (above);
- d21 follow-up PR, deploy, prove;
- r22 (published wheels lack web_dist; nodes run local wheels);
- canvas design artifact Jgm3JPnAhKWpeiCxFXvNBi owes the Variables tab, (i) button, zoom;
- optional: move the reviewer bridge to a dedicated `culture-reviewer` account.

## Original request (operator, verbatim intent)

> "Set up a rule that when we have a new PR, an agent gets it and starts fixing the
> issues (SonarCloud, failing tests, etc.) for all repos. What are we missing? We can
> re-use bridges from ../culture-nodes, take them, or commit on separate repos. I'm also
> open to using vercel agent sdk for wrapping harnesses."

The flow is:

1. /scope, /think, /challenge, /spec-to-plan, /assign-to-workforce;
2. live testing and dogfooding;
3. /validate-delivery, then /summarize-delivery, as the last steps before the final PR.

## Artifacts

| What | Where |
|---|---|
| Spec | `docs/specs/2026-10-05-pr-fixer-rule.md` (frame `pr-fixer-rule`) |
| Plan / split | `docs/plans/2026-10-06-pr-fixer-rule.md`, `docs/plans/2026-10-06-pr-fixer-rule-split.md` |
| Delivery summary | `docs/deliveries/2026-10-06-pr-fixer-rule.md` |
| Records | `devague deviate --list` (d1–d24), `devague lapse --list` (l1–l7), `devague oblige/evidence/delta --list` |
| Evidence log | `.devague/evidence-log-pr-fixer.md` (every live run) |
| Ops recipe | `docs/operations/pr-fixer.md` (sections 7–8: rules, trusted workflows/actors, reviewer on spark) |
| Fixer data | `docs/rules/pr-fixer/` (actors, workflows, rules, `seed-variables.sh`) |

## Key operator decisions (do not re-litigate)

- **Bridges** in agentculture/cultureagent (0.14.0); no Vercel AI SDK.
- **The App pushes, never the agent;** the fixer never merges or force-pushes.
- **Review before push (d20):** Codex (read-only, on spark, as the `spark` account) must approve exactly the gate-built commit; push verifies trusted workflow/actor digests (`culture_rules/actors/trusted.py`), consumes the approval, refuses `base_changed`/`head_moved`/`rule_disabled`. Changing a pinned workflow or actor needs a release (digest first, then nodes, then import).
- **Fixer machine:** spark2, `culture-fixer`, Qwen only (d8).
- **Repos:** allow-list `fixer_repos` (d18) = culture-rules, culture-rules-tester, lobes-cli; `fixer_excluded_repos` override; App `repos` list excluded from its digest (guildmaster adds repos).
- **Attempts:** 3 per PR; after 3 failed cycles the main agent fixes by hand with a PR comment (d22).
- **Comment intent (d24):** a comment starts a run only if it begins with `/fix` (then a space) or `@rules-culture-dev`; forms in `fixer_comment_triggers`; comment runs count toward the 3 attempts. Ships with d21.
- **d21 split** approved as a follow-up PR; stage rule named `pr-fixer-review-commit`, optional workflow inputs (d23).
- **Sonar on #17:** fix the 386 smells (done) and the 41 complexity issues (in progress) before merge.

## Deviations (all approved)

| Id | Decision |
|---|---|
| d1 | Bridge callbacks on an API route, plus node redelivery |
| d2 | The self-tag exempts only check-completion event types |
| d3 | `Executor.deliver(attempt=)` |
| d4 | culture-rules is the live test repo (in practice: culture-rules-tester; record not amended) |
| d5 | The App keeps Actions: write |
| d6 | The reviewer differs from the implementer |
| d7 | Variable-using rules refused while any online node lacks the capability |
| d8 | No Codex on spark2 |
| d9 | Codex reviews |
| d10 | `retry_until` carry, `github.push` `gate_verdict`, BuiltinCodePort |
| d11 | Codex preferred implementer; Qwen fallback on cortex-spark2 |
| d12 | Built-in `action` code step |
| d13 | Four rules with a global per-PR key |
| d14 | PR facts and base_sha on every trigger |
| d15 | Trusted threads only (`github.threads`, `threads_addressed`) |
| d16 | Rule `on_failure` hand-back comment |
| d17 | "Stop N current runs?" on disable |
| d18 | Allow-list `fixer_repos` |
| d19 | Deterministic describe, canvas zoom, (i) button |
| d20 | Agent (Codex) review before any push |
| d21 | Split into rules + workflows chained by run events (follow-up PR) |
| d22 | Manual fallback after 3 fixer cycles, with a PR comment |
| d23 | `pr-fixer-review-commit` name; optional workflow inputs |
| d24 | Comment intent: begins with `/fix` or `@rules-culture-dev`; counts toward 3 attempts |

**Pending:** d25 GitGuardian (see above).

## Workforce rules in force

- **Branches/worktrees:** integration `rules/pr-fixer` (main checkout); task branches `rules/<x>` in `../.worktrees.culture-rules/`; remove with `git worktree remove`.
- **Implementers:** Opus subagents (Codex preferred when not rate-limited). **Reviewer ≠ implementer:** Codex reviews Opus work.
- **Codex invocation:** `codex exec -s read-only - < prompt.txt` (prompt from a FILE via stdin — inline prompts hung three times); neutral "code review request" wording (attack wording gets refused); tell it NOT to run Docker/Mongo tests (they hang under its sandbox). Check for "usage limit" (it resets ~5 h later; schedule a one-shot cron).
- **Gates:** full suite before/after merges, `set -o pipefail` when piping test output (l6); expect 1 skip with all extras (`uv sync --all-extras` in new worktrees).
- **Engine upgrades:** `culture-rules runs pause --apply` first, resume after (l5).
- **Don't push to `rules/pr-fixer` while a fixer run on #17 is active.**
- **Status loop:** session cron at :07/:37 ("Give the operator a status update…"); recreate after a restart.
- **Portability:** no absolute home paths in committed files (steward 0.28 also rejects home-relative dotfile paths in docs other than the waived ops doc).

## Open risks worth carrying

| Risk | What |
|---|---|
| r2 | Multi-hour agent runs vs step deadlines |
| r3 | cortex capacity on spark2 |
| r4 | Backup docs don't cover the variables collection |
| r9 | An intermittent test |
| r10, r23 | Variable history kept in one document |
| r11 | The bridge token is visible to the agent |
| r12 | The fixer token can open issues |
| r15 | The agent can read untrusted threads |
| r20 | The first-deploy 24 h settle scan |
| r21 | No indexes on runs |
| r22 | Published wheels lack web_dist |
| r24 | The gate packs full history |
| r25 | Variable helpers are duplicated |
| r26 | No proper stop for unfixable issues |

## Machines

- **spark:** codex bridge for the reviewer (the account's `cultureagent-bridges/venv` data dir, unit `cultureagent-codex-bridge`, token `RULES_CODEX_REVIEWER_TOKEN`); API (LAN listener `100.127.105.72:8791`, Access listener `127.0.0.1:18765`) and node. One shared venv at `$XDG_DATA_HOME/culture-rules/venv` (the account's data dir). Mongo `culture-rules-mongod` in docker.
- **thor, orin:** nodes only, installed with `install.sh` (default extras include yaml).
- **spark2:**
  - **access:** reach it as `ssh spark2`; the bare IP has no known_hosts entry. Tailnet IP `100.93.248.8`.
  - **culture-rules:** the node runs as user spark2, with grant secrets RULES_MONGO_URI, RULES_QWEN_FIXER_TOKEN and RULES_GITHUB_APP_PRIVATE_KEY.
  - **culture-fixer account:** uv, grant, Node 24, Qwen Code, gh, and the cultureagent qwen bridge user unit on `:8093`.
  - **sudoers:** `/etc/sudoers.d/culture-fixer-gate`.
  - **models:** cortex (vLLM) on `localhost:8000`, which needs a key.

## Carry-forward notes

- `install.sh` rewrites `node.env` and the unit on every run. Always pass the spark2 flags above.
- Pending TestPyPI publishers can't be edited. The sandbox repo was renamed to match `culture-rules-tester`, and its dist name is `culture-rules-tester`.
- Fine-grained PATs have no Checks permission, so the engine reads checks through the App.
- Qodo has no credits; the operator is handling it.
