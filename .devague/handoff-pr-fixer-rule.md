# Handoff: pr-fixer-rule workforce run

This is the working state for resuming after context compaction. The main agent
rewrote it on 2026-10-07 at about 19:40 IDT. The authoritative records are the devague
frame, plan and delivery store. This file points at them and adds what they don't hold.

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
| Plan | `docs/plans/2026-10-06-pr-fixer-rule.md`: 24 tasks in 9 waves. `devague plan waves --json` gives every brief verbatim. |
| Split (gate 2) | `docs/plans/2026-10-06-pr-fixer-rule-split.md` |
| Deviations d1-d19 | `devague deviate --list` |
| Risks | `devague plan show --plan pr-fixer-rule` |
| Lapse l1 | Proposed; `devague lapse --list` |
| Evidence log (for /validate-delivery) | `.devague/evidence-log-pr-fixer.md`. It holds the full t20 run table. |
| Ops recipe | `docs/operations/pr-fixer.md`: spark2 setup, gate, rules, variables, stop-runs, describe |
| Fixer data | `docs/rules/pr-fixer/` (rules JSON, workflow JSON, `seed-variables.sh`) |

## Key operator decisions (do not re-litigate)

- **Bridges:** in agentculture/cultureagent. The Vercel AI SDK was rejected.
- **Pushing:** the GitHub App pushes, never the agent. The engine's test gate decides between push and hand-back.
  - The gate comes from the `culture.yaml` `gate:` section on the PR's BASE commit.
  - Its commands are configuration, never defaults in code.
- **The fixer never merges and never force-pushes.**
- **Fixer machine:** spark2, account `culture-fixer`. Only the Qwen bridge runs there; there is no Codex on spark2 (d8).
- **Skipped PRs:** fork PRs and drafts are skipped. The quiet period is a workflow `wait` step.
- **Trusted authors:** the shared variable `trusted_authors`, which is the operator plus `qodo-code-review[bot]`. Keep Qodo.
- **Rules:** four rules share one workflow and one global per-PR concurrency key (d13). Every trigger carries the PR facts (d14).
- **Repo selection:** an allow-list in `vars.fixer_repos` (d18), with `fixer_excluded_repos` as an override.
- **Attempt budget:** max_attempts 3 per PR is the accepted loop bound for issues the fixer can't fix (r26). Only a human push or green checks reset it.
- **App key:** it also lives in spark2's node grant store (r16 accepted).
- **API listener:** the LAN listener binds spark's tailnet IP `100.127.105.72:8791` so bridge callbacks from spark2 reach it.

## Deviations (all approved)

| Id | Decision |
|---|---|
| d1 | Bridge callbacks on an API route, plus node redelivery |
| d2 | The self-tag exempts only check-completion event types |
| d3 | `Executor.deliver(attempt=)` |
| d4 | culture-rules is the live test repo |
| d5 | The App keeps Actions: write |
| d6 | The reviewer differs from the implementer |
| d7 | Saving or enabling a rule that uses variables is refused while any online node lacks the `variables` capability |
| d8 | No Codex on spark2 |
| d9 | Codex reviews |
| d10 | `retry_until` carry, `github.push` `gate_verdict`, BuiltinCodePort |
| d11 | Codex is the preferred implementer; Qwen is the fallback, on `cortex-spark2` only |
| d12 | Built-in `action` code step |
| d13 | Four rules with a global per-PR key |
| d14 | PR facts and base_sha on every trigger |
| d15 | `github.threads` and `github.threads_addressed` built-ins: only trusted threads go to the agent and get replies |
| d16 | Rule `on_failure`: a hand-back comment with `run.error.*` |
| d17 | Disabling a rule offers "Stop N current runs?", and approving cancels them |
| d18 | Allow-list `fixer_repos`; guildmaster#138 tracks adding a repo at provisioning |
| d19 | Deterministic config-derived descriptions (`rules describe` / `workflows describe`, API, MCP, editor), canvas zoom, and an (i) info button on every rule and workflow |

**Pending:** a possible d20. The operator said "gitguardian result should be reviewed and posted as a comment". My reading, NOT yet confirmed by the operator:

- the fixer never auto-fixes GitGuardian findings; it posts a review comment instead;
- the checks settle stops blocking on empty queued suites (0 check runs), for any app.

Ask the operator to confirm before recording or building it.

## Workforce rules in force

- **Branches and worktrees:** the integration branch is `rules/pr-fixer`, in the main checkout. Task branches are `rules/pr-fixer-<x>`, with worktrees in `../.worktrees.culture-rules/`. Remove a worktree with `git worktree remove`, never `rm -rf`.
- **Implementers:** Codex is preferred. Opus subagents implement when Codex is out or for follow-ups. Qwen is a fallback only, and only on `cortex-spark2`. Don't use cortex while live fixer runs need it.
- **Reviewers:** the reviewer is never the implementer.
  - Codex reviews Opus work with `codex review --base rules/pr-fixer`, run in the worktree, with output in the scratchpad `rev/` directory. Check for "usage limit".
  - An Opus subagent reviews Codex work.
- **Merging:** the main agent verifies every finding. At most 3 rounds. Run the full suite before and after `git merge --no-ff`. Expect 1 skip; more skips mean the venv lost optional deps.
- **Ignore** Codex's "missing version bump" findings. 0.13.0 is the single bump, on #17.
- **Never write an unquoted heredoc containing backticks.**
- **Portability:** committed files must not contain absolute home paths (`/home/<user>/`). harness-smoke and `devex pr lint` refuse them. Use placeholders such as FIXER_HOME or `~user`. `docs/operations/pr-fixer.md` is waived for its per-account home-directory paths.
- **Session-only crons:**
  - the status loop at :07 and :37, prompt "Give the operator a status update…";
  - recreate it after a restart (it was `067039de`).

## Status now (2026-10-07, about 19:40 IDT)

### PRs

| PR | State |
|---|---|
| **culture-rules#17** (0.13.0, the delivery PR, gate 3) | OPEN, head `eb9087d`, which contains main's 0.12.1. CI is green except the SonarCloud quality gate (295 issues), which also fails the `test` job's scan step. **The fixer is working on it (t22 dogfood, pulled forward by the operator).** Do NOT push to `rules/pr-fixer` while a fixer run is active: a push supersedes the run or fails its push. |
| culture-rules#18 | MERGED (0.12.1: `gate:` section on main) |
| lobes-cli#300 | MERGED (`gate:` section) |
| guildmaster#139 | OPEN, waiting for the operator's merge (ledger: provision culture-rules-tester, formerly pr-fixer-sandbox) |
| guildmaster#138 | Issue: `guild create --pr-fixer` adds a repo to `fixer_repos` |

### Local, unpushed

- `rules/pr-fixer` is ahead by `a90037f`: the sandbox is renamed to culture-rules-tester in the seed script, docs and tests.
- This handoff commit is also unpushed.
- Push both after the fixer finishes on #17, together with d19.

### Branch in flight

- `rules/pr-fixer-describe-zoom` (d19), worktree `pr-fixer-describe-zoom`. An Opus subagent is working on it:
  - it is fixing Codex round-1 P2s: nested loop bodies were dropped from descriptions, and the fit zoom rounded up;
  - then it adds the (i) info button the operator asked for;
  - then it goes to Codex round 2, then merges.

### Live (rules.culture.dev)

- **Nodes:** all four nodes and the API run a LOCAL 0.13.0 wheel built at `d068dc5`. Published wheels lack web_dist (r22).
  - spark2 was installed with `install.sh --gate-run-as "sudo -n -u culture-fixer -- /usr/bin/env PATH=<fixer home>/.local/bin:/usr/local/bin:/usr/bin:/bin"` plus `--secret RULES_QWEN_FIXER_TOKEN --secret RULES_GITHUB_APP_PRIVATE_KEY`.
  - That sets NoNewPrivileges=no and includes the yaml extra.
- **Rules:** the four rules are imported and ENABLED. A re-import sets them back to the file's `enabled: false`, so re-enable all four after any import.
- **`fixer_repos`:** `agentculture/culture-rules`, `agentculture/culture-rules-tester`, `agentculture/lobes-cli`. `fixer_excluded_repos` is `[]`. The github-app actor's `repos` also lists culture-rules-tester and lobes-cli.
- **Actors:** `qwen-fixer` (machine spark2, bridge `100.93.248.8:8093`, cortex, yolo, max_concurrency 1) and `github-app`. The github-app actor declares the fixer events (checks, review_comment, synchronize, ready, checks_settled) and actions (comment, push, review_reply).
- **CLI against production:** `grant run --inject CULTURE_RULES_TOKEN=RULES_CULTURE_RULES_SERVICE_TOKEN -- env CULTURE_RULES_API_URL=http://100.127.105.72:8791 culture-rules ...`
- **Fixer run on #17:** `run-47bd8394…` (`pr-fixer-checks`, settled `timeout` at eb9087d) started at 16:08 UTC. It is attempt 1 of 3. Earlier `run-56186abd…` was superseded (it targeted the old head).
- **Slow settles:** the `claude` suite is ignored, but the `gitguardian` suite stays queued with 0 check runs, so every settle waits for the 900 s timeout. See the pending d20.

### t20 (done; evidence logged)

On the sandbox (now `agentculture/culture-rules-tester`), PR #1:

- **One full pass:** the checks settled red, the agent fixed both seeded issues, the gate passed, the App pushed 0e1b4bf, and the run-link comment was posted.
- **Two runs superseded** during the quiet period.
- **Three hand-backs:**
  - the gate failed on NoNewPrivileges; fixed;
  - the gate failed on the missing yaml extra; fixed;
  - an agent timeout on a failure the fixer can't fix (test-publish).
- **Draft #2 and fork #3:** zero runs.

On the sandbox, test-publish should now pass, because the repo name matches the TestPyPI publisher `culture-rules-tester`. No run has confirmed it yet.

## Next items

1. **Watch the fixer on #17.** It gets at most 3 attempts.
   - Record each run in the evidence log: gate verdict, push, comments, attempts.
   - A gate on culture-rules runs the full suite as culture-fixer. The MongoDB tests should skip there, since the account has no Docker. Verify that.
   - When the runs end, push `a90037f`, the handoff and the merged d19 to #17.
2. **d19:** take the subagent's report, run the Codex review, then merge. Then push once the fixer is idle.
3. **d20:** confirm the GitGuardian interpretation with the operator, then build it. The empty-queued-suite fix removes the 15-minute settle delay.
4. **lobes-cli:** main has a pre-existing failing test, `tests/test_gateway_pool_saturation.py::test_the_engine_plumbing_is_gone_from_the_server_module`. The gate can't pass on lobes-cli PRs until it is fixed.
5. **t21 live probes** (task t21), reading outcomes from run history and webhook deliveries:
   1. settled checks with Sonar, GitGuardian and the claude suite;
   2. a push during the wait step supersedes the run (seen in t20);
   3. disable mid-run: no push, and the d17 prompt appears;
   4. an excluded repo starts no run;
   5. a node held on an old version fails closed. **Ask the operator before holding any node back.**
   6. a comment from an untrusted account starts no run.
6. **t22** (dogfood, in progress on #17), plus "at least two more real culture-rules PRs".
7. **t23** /validate-delivery: obligations, evidence and deltas. Deltas to file:
   - Qodo's billing comment starts a run;
   - an unfixable failure burns up to 60 minutes per attempt (r26);
   - uv.lock re-lock;
   - the lapse l1 adjudication.
8. **t24** /summarize-delivery, then the operator merges #17 (gate 3).
9. **After merge:**
   - fix publish so wheels carry web_dist (r22), then move the nodes to the PyPI 0.13.0;
   - the canvas design artifact Jgm3JPnAhKWpeiCxFXvNBi ("Chosen" row) still owes the Variables tab, and now the (i) button and zoom.

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

- **spark:** API (LAN listener `100.127.105.72:8791`, Access listener `127.0.0.1:18765`) and node. One shared venv at `$XDG_DATA_HOME/culture-rules/venv` (the account's data dir). Mongo `culture-rules-mongod` in docker.
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
