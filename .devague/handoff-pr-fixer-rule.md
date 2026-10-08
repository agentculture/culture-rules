# Handoff: pr-fixer-rule workforce run

This is the working state for resuming after context compaction. The main agent
rewrote it on 2026-10-07 at about 19:40 IDT. The authoritative records are the devague
frame, plan and delivery store. This file points at them and adds what they don't hold.

## Read this first: status at 2026-10-08 ~23:10 IDT

- **Merged:** #17 (0.13.0, squash 5c524e6, tree == rules/pr-fixer@3196452); #19 (0.13.1 Variables editor: an edited empty list item takes the list's type, 45e1959).
- **#20 open, CLEAN** (`rules/pr-fixer-split`, 988d80f, 0.14.0): d21 split + d23 + d24 + the hops-on-the-bus fix (events-cli 0.10.0 has no `hops` field: `to_bus`/`from_bus` move it into `data._culture_rules_hops`). Main was recorded merged with `-s ours` after applying #19's diff (content verified identical). Includes the fixer's own commit 4cce77d (run-2515a070: Qwen fixed 10 Sonar issues, gate + Codex approved, App pushed). CI green, Sonar 0 issues. Waiting on the operator's merge.
- **d25 ready, not pushed** (`rules/d25-gitguardian` worktree, on top of #20 at 8fb46a3/988d80f-equivalent; head 48ecb14). GitGuardian findings comment (`pr-fixer-secrets`, `pr-fixer-secrets-late`, workflow `report-secrets`, built-ins `gitguardian.findings`/`gitguardian.hold`), fixer held off a head GitGuardian fails on (pr-fixer-checks clause + `secrets` step in pr-fix; trusted pr-fix digest only 04570dee), late failures via `github.pr.checks_failed_late` with token-CAS candidates (`checks_settle_late`) and atomic confirm+emit, `once_key` on `github.comment` (`github_comment_once`, backed up). Ingest hardening: reserved settle/late/schedule/probe namespaces, every reserved field validated, unstorable shapes refused, allowlisted content errors quarantined, everything else stalls+retries. 7 Codex rounds; final verdict sound. 4327 passed. Next: after #20 merges, merge main in, move d25 CHANGELOG lines to a 0.15.0 section, bump, open PR.
- **Rollout (after both merge):** one window per docs/operations/pr-fixer.md "Rolling out the split" (pause, upgrade every node + API, seed `fixer_comment_triggers`, import actors→workflows→rules, disable workflow `pr-fixer`, enable 9 rules: stages, then `pr-fixer-secrets`/`-late`, then triggers, resume), then prove on culture-rules-tester (chain + review-only + a seeded GitGuardian finding if feasible).
- **Live now:** 0.13.x d20 wheel (7bb9026) on all nodes; any trusted comment triggers a run until d24 deploys (Qodo billing notices did: run-29b53dcd on #19, run-2515a070 on #20). Operator: "Qodo will be fixed".
- **Records:** d1-d25 approved (d25 = GitGuardian findings comment, never auto-fix), lapses l1-l7 approved.

### Waiting on the operator

1. Merge #20.
2. t21 probe 5 (hold one node on the old build ~15 min) and probe 6 (`/fix` from a non-trusted account; better after d24 deploys).
3. Required status checks on `main` (rulesets "Protect main" exist on culture-rules, lobes-cli, culture-rules-tester: PR required, no force-push/deletion, 0 approvals, NO required checks).
4. d25 second half: should settle stop blocking on empty queued GitGuardian suites?
5. guildmaster#139.
6. Optional follow-up: automatic replay of failed GitHub webhook deliveries (App deliveries API).

### Then

- d25 PR, single rollout, live proof;
- r22 (published wheels lack web_dist);
- canvas artifact Jgm3JPnAhKWpeiCxFXvNBi owes Variables tab, (i), zoom;
- optional dedicated `culture-reviewer` account for the Codex bridge (Codex quota is shared with the main session's login: running out stops live reviews, fail closed).

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
| d25 | GitGuardian failure: PR comment with findings (type, file:line, commit, incident link), never auto-fixed |

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
