# Handoff: the qodo bridge workforce run

Written 2026-11-16 for a session that resumes after compaction. Everything
here is on disk; verify a branch or file exists before relying on it.

## What this work is

Turn `../qodo-cli` into a **bridge**: one app with a CLI, an HTTP API, an MCP
server and a web UI, built on a reusable framework cited from culture-rules.
`../associate` gets the same bridge. culture-rules drives both through
**configuration only** and must never contain the word `qodo` in
`culture_rules/`. Two flows result:

- **PR opened:** after Qodo's own review lands, the bridge collects and
  triages the findings.
- **PR merged:** associate (Pi, cortex model) proposes an improved Qodo
  configuration, the qodo bridge validates it, and culture-rules opens the PR
  as its GitHub App.

Since deviation d1, qodo-cli is a **gateway over Qodo's MCP server and API**:
an exposure policy in configuration decides which upstream tools are exposed
and on which surfaces; anything not named is refused, and non-read-only tools
are refused by default.

## Where the records are

| Record | Location |
| --- | --- |
| Spec | `docs/specs/2026-11-16-qodo-bridge.md` |
| Plan (21 tasks, 10 waves) | `docs/plans/2026-11-16-qodo-bridge.md` |
| Split plan (owners, models) | `docs/plans/2026-11-16-qodo-bridge-split.md` |
| Frame and plan state | `.devague/frames/qodo-bridge.json`, `.devague/plans/qodo-bridge.json` |
| UI design canvas (part of the spec) | <https://claude.ai/artifact/JQLGKoX2zDwQKnSUQxFyWB> |
| Qodo probe write-up | `docs/qodo-mcp-tools.md` on branch `bridge/t2` in qodo-cli |
| npm package follow-up | agentculture/qodo-cli issue 23 |

All culture-rules files above are **uncommitted** in the main checkout, which
also holds the operator's unrelated uncommitted Discord button-code work on
`main`. Do not touch or commit that work. The agreed home for the spec, plan,
split and this handoff is the branch `rules/open-pr-action`.

`devague` commands run from the culture-rules repo root. `devague status`
and `devague plan status` give the frame and plan state; both converged at the
time of writing.

## Decisions the operator made

- culture-rules is configuration only; no Qodo extension, kind or literal.
- "Run Qodo" means collect and triage findings after Qodo's own review. The
  first version does not trigger reviews.
- The web UI ships in the wheel; culture-rules does not present it; three
  screens (Findings, Configuration, Rules) per the canvas, which is confirmed.
- The configuration PR is opened by culture-rules' GitHub App through a
  generic action, not by the bridge. That PR must never start another
  improvement run.
- The command is renamed from `qodo` to `qodo-cli`; the import package stays
  `qodo`.
- associate is the improvement agent and gets the full bridge.
- The Qodo configuration for culture-rules is a bundle committed in qodo-cli,
  shipped disabled.
- The split plan was approved as written.
- **Reviewer:** Pi on the cortex model reviews each task before merge; give it
  up to 30 minutes; use Codex when it takes longer.
- The Qodo API key is in `grant` as `QODO_API_KEY` (hidden). Use
  `grant run --inject QODO_API_KEY=QODO_API_KEY -- <command>`; never print it.

## Branches and worktrees

| Repo | Integration branch | Base | Task worktrees |
| --- | --- | --- | --- |
| culture-rules | `rules/open-pr-action` | `origin/main` | `../.worktrees.culture-rules/t17` on `rules/t17` |
| qodo-cli | `feat/bridge` | `docs/readme-rewrite` | `../.worktrees.qodo-cli/t1` on `bridge/t1`, `../.worktrees.qodo-cli/t2` on `bridge/t2` |
| associate | not created yet | planned: `spec/associate-multi-model-on-pi` | none yet |

Notes:

- `rules/open-pr-action` was created tracking `origin/main`; unset or change
  its upstream before any push.
- Git has **no user identity configured** in this environment. Commit with
  per-command `-c user.name=... -c user.email=...` taken from the repo's last
  local commit (`git log -1 --format=%an` and `%ae`). Commits are unsigned;
  culture-rules `main` needs signed commits, which a GitHub squash merge gives.
- Every commit message ends with the two attribution lines from the session's
  system reminder (Co-Authored-By and Claude-Session).
- Nothing has been pushed and no PR is open. Pushing and opening PRs need the
  operator's go-ahead.
- associate has an uncommitted `culture.yaml` edit and an open plan on its
  branch. Do not touch `culture.yaml`, `associate/harness/pi.py` or
  `gate.json`.

## Task status

| Task | State |
| --- | --- |
| qodo-cli: t1 to t12, t18, its half of t19, the exposure policy file, and the fix flow (deviation d4, approved) | **Merged** into `feat/bridge`: 1113 Python, 109 web and 73 bundle tests pass (the bundle tests need `uv run --with ../.worktrees.culture-rules/open-pr-action`). |
| culture-rules: t17 `github.pr_open`, the spec, plan, split and this handoff | On `rules/open-pr-action`. |
| associate: t13 to t16, `serve` and token verbs (d3, approved), its half of t19 | **Merged** into `feat/bridge`: 754 Python and 87 web tests pass; version 0.11.0. |
| t20, t21 | Operator-run. |

Every buildable task is merged on the three local integration branches. Still
owed: one PR per repo through `cicd` (the operator's third gate; nothing is
pushed), then `/validate-delivery` and `/summarize-delivery`. Associate's PR
sits on the unmerged `spec/associate-multi-model-on-pi`. Lapses l1 to l5 and
deviations d1 to d4 are all adjudicated. The operator has not yet said whether
the built qodo screens replace the confirmed canvas.

The fix flow (d4): `findings fix` publishes `qodo.fix.requested` on the
events-cli bus (the `events` extra; a broker listens on this machine at
127.0.0.1:1883, so never run a real publish without the operator's word);
`fix propose` gates the agent's change; the bundle's fifth rule,
`qodo-fix-on-request`, runs associate on the PR head in its own checkout root
(`/srv/qodo-fix-checkouts`) and opens a PR against the reviewed PR's branch.
A real publish and culture-rules ingesting it are unexercised (t21).

The operator confirmed the built qodo screens in place of the canvas. The UI
preview that was running for the operator is stopped and its state deleted.

Things later tasks must respect: `config propose` requires the checkout at
`<root>/<owner>/<name>` under `QODO_PROPOSE_CHECKOUT_ROOTS`, with the repo in
`QODO_PROPOSE_REPOS`; associate runs need the checkout to be a work tree root
under `ASSOCIATE_RUNS_ROOTS`, and a start needs an admin token. A clean run that
changed nothing reports `ok: true, merge: nothing_to_merge`. qodo-cli serves on
8792/8793 (`POST /verbs/<noun>/<name>`, tokens via `qodo-cli tokens issue`);
associate on 8794/8795 once d3 is merged. The bundle is `docs/rules/qodo/` in
qodo-cli (four rules, all disabled; its README lists what to set).

Reviewer note: cortex returned nothing in three attempts (30-minute limit), so
Codex reviews. `codex_review.sh` in the scratchpad passes the diff inline and
closes stdin. Codex cannot run tests; the main agent runs them before and after
each merge and fixes small findings itself. Integration worktrees:
`../.worktrees.qodo-cli/bridge`, `../.worktrees.associate/bridge` and
`../.worktrees.culture-rules/open-pr-action`. Lapses l3 and l4 are filed and
await the operator. The verb names differ from the plan's wording: upstream
`rules-get` is `rules show` because `rules get` is the older search verb.

Waves: 1 is t1, t2, t17; 2 is t3, t7; 3 is t4, t13; 4 is t5, t10, t14; 5 is
t6, t15; 6 is t8; 7 is t9, t11, t18; 8 is t12, t16, t19; 9 is t20; 10 is t21.

## What to do next

1. Read the reviews in the session scratchpad: `review-t1-codex.txt`,
   `review-t17.txt` (and `.err`, `.log` beside them). If the scratchpad is
   gone, rerun with the two helper scripts' logic: a one-shot
   `pi -p --provider glm --model cortex --no-tools` review with the diff
   inline and a 1800 s timeout, or `codex exec -s read-only`.
2. For each approved task: run its tests, merge with `git merge --no-ff` into
   the integration branch, run the tests again, then `git worktree remove` the
   task worktree. For `changes-needed`, send the findings back to a task agent.
3. Merge order in qodo-cli: t1 first, then t2 (docs and fixtures only).
4. Start wave 2 in qodo-cli from `feat/bridge`: t3 (public `qodo.api`) and t7
   (cite the framework). Briefs come verbatim from
   `devague plan waves --json`; task agents work test-first, never run
   `devague`, and report lapses instead of filing them.
5. Commit the spec, plan, split and this handoff on `rules/open-pr-action`.
6. A 30-minute status loop is scheduled in the session (cron job `dd3ee0a4`);
   it dies with the session and must be recreated with `/loop` if wanted.

## Open items and risks

- **t2 gap 1:** Qodo's history tools (`pull-request-review-history`,
  `pull-request-similar-suggestions`, `pull-request-stats`) failed twice with
  `MT-UPSTREAM-DOWN`. No successful reply has been seen. Re-probe before t4's
  history function or t18's improvement flow relies on them (plan risk r8).
- **t2 gap 2:** whether Qodo still reads `best_practices.md` and
  `[pr_reviewer] extra_instructions` is unverified; its current docs name
  `REVIEW.md` and `[review_agent]` guideline keys instead. `config init`
  writes the older settings today. This shapes t6's validation.
- **t17 choices to confirm with the operator:** it adds a run-time check that
  the actor lists the kind in `params.actions` (new behaviour), and it does not
  require a pinned actor.
- **associate:** source is 0.10.0 on an unmerged branch; the installed 0.9.0
  lacks `--model`. A run merges into the checkout's local `main` and exits 0
  even when the merge is refused; use a dedicated clone and read the result.
  It writes nothing unless the lobes decider is reachable.
- **associate's web UI (t16) has no confirmed design.** It needs artboards on
  the same canvas and the operator's confirmation first.
- **t20 and t21 are operator-run:** units, grants, GitHub App permissions
  (contents and pull-request write) and enabling rules each need the
  operator's go-ahead at the time.
- Obligation o19 is mislabelled (authentication behaviour attached to the
  rollback claim c50) and could not be rejected; o23 covers the same behaviour.
- Two lapses and deviation d1 are on record; file new lapses and deviations
  the moment they happen.

## After the run

Open one PR per repo through the `cicd` skill (the operator's third gate),
then run `/validate-delivery` and `/summarize-delivery`.
