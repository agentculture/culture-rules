# pr-fixer live evidence log (input for /validate-delivery)

## 2026-10-06 — t16 App permissions (live probe on agentculture/culture-rules, PR #14)

- Installation 167755039 permissions after operator approval: contents write, checks read, actions **write**, issues write, pull_requests write, metadata read; events include check_suite, workflow_run, pull_request_review_comment.
- `GitHubApp.push_token("agentculture/culture-rules")` minted a single-repo token; it pushed `cbb0d1fac49d71d81956fc252c4206f1f8396a38` to PR #14's head branch (non-force).
- GitHub reports author, committer and login `rules-culture-dev[bot]` for that commit.
- The App read 11 check runs on that SHA; the App push triggered CI (lint, test, web, harness-smoke, version-check, GitGuardian).
- The same token was refused (git rc 128) for agentculture/cultureagent.
- PR #14 closed, branch deleted.

## spark2 run-as path for the test gate (t13/t18), 2026-10-07

Operator installed `/etc/sudoers.d/culture-fixer-gate`:
`spark2 ALL=(culture-fixer) NOPASSWD: /usr/bin/env` (visudo-checked, mode 0440).
Probed through the gate's exact prefix
`sudo -n -u culture-fixer -- /usr/bin/env PATH=~culture-fixer/.local/bin:/usr/local/bin:/usr/bin:/bin`:

- whoami: `culture-fixer`; stdin passes through sudo intact (the gate feeds the pack this way)
- `mktemp -d -t culture-rules-gate-XXXXXXXXXX` -> owned by `culture-fixer`, mode 700; removed by `rm -rf` as that user
- git 2.43.0 and uv 0.12.23 resolve on that PATH
- the node user (spark2) still cannot read `~culture-fixer` (permission denied)

Caveat, found in t20: this probe ran from an interactive shell. Inside the node's
systemd unit (`NoNewPrivileges=true`) sudo refuses. See lapse l1.

## t20 live run on agentculture/pr-fixer-sandbox, 2026-10-07

Deployed a local 0.13.0 wheel (d782924) to spark (API and node), thor, orin and
spark2. Imported the workflow and the four rules, seeded the variables and enabled
the rules, with the allow-list set to `["agentculture/pr-fixer-sandbox"]`. Outcomes
are read from run history (`culture-rules runs show`) and the PR's GitHub timeline,
never from the agent's report.

PR #1 was seeded with a floor-division bug that fails `test_mean_of_two_values`
and an unused local variable.

| Run | Trigger | Outcome |
|---|---|---|
| run-65c792ba3e7ef7d5ede86a48693c0f3e | `pr-fixer-comment`, Qodo's billing notice; Qodo is a trusted author | `superseded` in the quiet period: a human push moved the head from 30d73fe to c5f1b58. The agent never ran. |
| run-d6e5dabdf5ec61e8a47a912091a37e0f | `pr-fixer-checks`, coalesced behind the run above | The agent fixed both seeded issues (commit 0e09b25). The gate failed `source_unavailable` because the unit's `NoNewPrivileges=true` blocked sudo. The hand-back comment was posted and nothing was pushed. |
| run-b2a00c810276679164166f0ff71dc08c | `pr-fixer-comment`, the operator's retry comment | The agent fixed both again (ab81835). The gate failed `extra_missing: install culture-rules[yaml]`. The hand-back comment was posted and nothing was pushed. |
| run-4144cb0428806afe37cc95ed1ace2eb3 | `pr-fixer-checks`, settled `failure` at aca0f2a with PR facts (`head_repo == base_repo`, `draft: false`, `base_sha`) | **Full pass.** The agent produced 0e1b4bf. The gate read the `culture.yaml` gate section at the base commit and returned verdict `pass`. The App pushed 0e1b4bf as rules-culture-dev[bot] and posted the run-link comment. On the new head, tests (119 passed) and lint were green. |
| run-ef30c89bceb105c88e5a5d383999e51e | `pr-fixer-checks`, settled `failure` at 0e1b4bf: sandbox Sonar automatic analysis, TestPyPI publisher | `superseded` in the quiet period when a human push landed (3d80d8a). |
| run-f4e47b1b927291f8a9b656fc1ca2ec81 | `pr-fixer-checks`, settled `failure` at 3d80d8a: `test-publish` only (the TestPyPI publisher; the code cannot fix it) | The agent ran until the bridge's 3600 s timeout. The hand-back comment was posted, nothing was pushed and the head is unchanged. |

- Draft PR #2 and fork PR #3 (from OriNachum/pr-fixer-sandbox) got zero fixer runs.
  The checks on #3 were green; on #2 they were red.
- Live defects fixed during t20:
  - `NoNewPrivileges` on the node unit: a live drop-in, plus a21e07d `--gate-run-as`.
  - The `yaml` extra missing from the node install: installed live, plus 755aaf8.
  - The gate hid the run-as stderr: a21e07d.
- Behavioural deltas to file at t23:
  - A trusted bot's boilerplate comment (Qodo billing) starts a run.
  - A failure the code cannot fix (environment or publisher) burns up to 60 min of agent time per attempt.
  - The agent re-locked uv.lock, which was stale in the PR, and that change was accepted.

## t22 dogfood (in progress)

| Run | Trigger | Outcome |
|---|---|---|
| run-47bd8394c94ef496f203d40e2daef289 | `pr-fixer-checks` on culture-rules#17 at eb9087d (SonarCloud gate and one `test` job red) | Attempt 1: the agent fixed the 4 Sonar new-code issues (S5850 regex, S5332 x2 via an optional `ssl_context` on BridgeCallbackServer plus a TLS loopback test, S9383 `.catch`) in 8dfbfff. The gate returned verdict `guard`: the new test added `pytest.skip("openssl not available")` and `# nosec B310`. Nothing was pushed; the guard's instruction went to attempt 2 (agent on spark2, deadline 18:03Z). |
| run-96a1e3a45721cce64db1a7605ca83f1b | `pr-fixer-comment` on lobes-cli#302 (the operator's PR): Qodo's billing-blocked notice | The t20 delta recurred on a real PR. My cancel was refused by the session's permission policy, so it was left to the operator. The fix step then waited: `actor qwen-fixer at concurrency cap 1` (#17's run holds the only seat). It waited 16:47–17:40Z for the seat (~1000 dispatched/blocked/unblocked history entries from all four nodes), the agent was accepted at 17:40:44 and timed out at 17:52:47 because the deadline counted from the first dispatch. Hand-back posted on #302; nothing pushed. Defects: queue time eats the work budget; blocked churn grows history without bound (fix in progress, rules/pr-fixer-queue). |
| run-47bd8394c94ef496f203d40e2daef289 (cont.) | attempt 2 | The agent redid the fixes with a stdlib-only test (no skip, no nosec) as cedd5ab. The gate passed; the App pushed cedd5ab to rules/pr-fixer and posted the run link. **First full pass on a real PR.** Operator then required an agent review step before any push (d20). |
| run-0e5cd8d6786bd (lobes-cli#302) | `pr-fixer-checks` at 129781d | Attempt 1 redid the operator's MD033 fix; gate failed on a gate-environment artifact (the run-as account `culture-fixer` puts `-f` in pytest's tmp paths; lobes-cli asserts `-f` absent). Attempt 2 added a root conftest.py working around it, after resetting its worktree to the newer remote head feff2f5 on its own (overreach). Gate passed; github.push refused `head_moved` (expected 129781d, PR at feff2f5). Nothing pushed. |
| run-8f4b61837dba (#17) | `pr-fixer-checks` at c4c0bb3 (Sonar new issues from d19) | Queued 18:09-19:04Z behind lobes-cli#302 (1527 blocked/unblocked history entries), accepted 19:04:13, timed out 19:14:12 (deadline counted from the first dispatch). Hand-back posted. The queue fix (5183b4c, not yet deployed) addresses both defects. |
| run-3d55dbdc699a (lobes-cli#302) | `pr-fixer-comment`: the operator's informational status comment | Comment triggers fire on any trusted comment, including ones that request nothing (second instance after Qodo's billing notice). |
| run-d63748f3 (#17) | `pr-fixer-checks` at 35d8bea (Sonar: ~20 new issues from d19, reliability rating) | **First run on the queue-fixed engine.** Waited for the seat behind an orphaned bridge session of the timed-out run 8f4b6183 (the engine never cancels a bridge job on timeout: defect). 11 blocked polls, 9 history entries in total (old engine: 1527). Accepted ~19:47Z with its full budget; the agent ran the bridge's whole 3600 s and timed out (`qwen did not finish within 3600s`). Hand-back posted; nothing pushed. A real budget miss: the batch (complexity refactors, a11y rules, test-assert splits) is too large for one hour. |
| run-3d55dbdc (lobes-cli#302) | the operator's informational comment | Failed during the queue-fix rollout (mixed engine versions; lapse l5). Hand-back posted; nothing pushed. |
| run-62a05067 (#17) | `pr-fixer-checks` at 20bf618 (Sonar only; tests green) | Second full-budget timeout on the same Sonar batch (`qwen did not finish within 3600s`). Hand-back posted; nothing pushed. The batch is out of reach for one Qwen hour. |
| (deploy) d20 at 7bb9026 | operator OK 2026-10-08 (deploy, commit_author, reviewer = spark login) | Engine paused; wheel on spark (API+node, node unit gained the reviewer token), thor, orin, spark2; codex bridge on spark 127.0.0.1:8094 (pushes false, read-only confined by bwrap); actor codex-reviewer, workflow pr-fixer and the 4 rules imported and re-enabled; github-app commit_author set. Live digests equal the trusted constants (workflow 01ece1cd, reviewer dc26a418, app a44486a3). Engine resumed; proof PR culture-rules-tester#4 opened. |
| run-0f45b49415bb (culture-rules-tester#4, d20 proof) | `pr-fixer-comment` (Qodo billing notice; coalesced with the red checks) on a seeded `word_count` bug | **First full pass with the review step.** Agent efe72ab (split()); gate built 0e9d3f3 from its tree and passed; codex-reviewer (read-only, as spark) approved exactly 0e9d3f3 with no findings; verdict pass/approve; the App pushed 74f97a3→0e9d3f3, authored and committed by rules-culture-dev[bot], engine-written message, one parent; the agent commit never left spark2. Run-link comment posted. |
