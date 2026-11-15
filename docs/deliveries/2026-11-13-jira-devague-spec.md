# Delivery Summary — jira-devague-spec

plan: `jira-devague-spec` · run: `complete` · date: `2026-11-13` (trial `2026-11-15`)
baseline: `devague summary skeleton`

## Intent

> Opening a Jira issue starts a devague conversation in its comments: the engine asks scope, think and challenge questions, I answer in Jira, and a Fable 5.2 agent writes the converged devague spec, which lands in the library for my review.

After: Filing a Jira issue that names a repo is enough: the conversation runs in the issue's comments over as many rounds as it needs, and a converged, exported devague spec (with its obligations) appears in the library as a private spec item.

This run built the generic code that flow needs, in culture-rules and in cultureagent, and merged it. It then configured the flow live on core (t12–t14) and ran it on one real issue (t15).

**The after-state held on SCRUM-23.** Six reply rounds ran in the issue's comments, and the converged spec landed as library item **147**: kind `spec`, private, Markdown, byte-identical to the export, and the only spec item. The trial also went through every unhappy path the operator asked for (the Trial section below).

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — ADF helpers: flatten ADF to plain text, and plain multi-line text to ADF paragraphs/lists (extensions/jira/adf.py, new)
- `t2` — JiraClient reads the full issue: description, every comment (paginated) with author accountId and created, and named custom fields; `add_comment` sends `to_adf`(body) (extensions/jira/client.py)
- `t3` — Jira webhook puts the author on every event: data.author (accountId) on jira.issue.created/updated and jira.comment.created (extensions/jira/hooks.py)
- `t4` — jira.read action kind: register it and its port (extensions/jira/`__init__.py`, extensions/jira/read.py new)
- `t5` — jira.\* trigger filter users: match data.author against a list, never the actor's `self_identity` (extensions/jira/filters.py new; register in extensions/jira/`__init__.py`)
- `t6` — Runner argument type segment: one safe path segment for registered commands (`culture_rules`/actors/code.py)
- `t7` — Bridge step forwards an optional `working_path` and accepts an operating sandbox; read-only locks still hold (`culture_rules`/actors/agent.py)
- `t8` — cultureagent: Claude bridge operating role (separate repo, its own PR)
- `t9` — Docs: Jira actor guide gains jira.read, data.author and the users filter; a generic guide for comment-driven conversations with an agent (docs/actors/jira.md, docs/operations/jira-conversations.md new)
- `t10` — Boundary test: no devague literal in culture-rules code, tests or editor (tests/`test_extension_boundary.py`)
- `t11` — Release: version bump and CHANGELOG for the generic Jira/runner/bridge changes (pyproject.toml, CHANGELOG.md, uv.lock)
- `t12` — Core wrapper for devague (outside the repo, on core): proposals-only for the agent, adjudication only from the reply step, and a post-run check
- `t13` — Live actors on core: library actor gains kind spec + md and a key minted --kind spec; Fable 5.2 bridge agent actor (operate, `working_path` root); runner commands for the wrapper and git ls-remote head lookup; jira-app answerer accountIds noted
- `t14` — Live rules and workflows on core: round-one on jira.issue.created, reply rounds on jira.comment.created, users filter, `concurrency_key`, `max_attempts`, `on_failure`, final library.push; all shipped disabled until the trial
- `t15` — Trial on one real SCRUM issue: enable, run a full conversation, record the result
- `t16` — Editor: runner argument type segment in the Actors command editor, and a Users field for jira.\* triggers in the trigger picker (web/src/actors/app-config.ts, web/src/rules/TriggerPicker.tsx)
- `t17` — jira.comment treats an absent or empty connection.projects as every project (`culture_rules`/extensions/jira/port.py)
- `t18` — jira.comment refuses a malformed issue key with `bad_input` (like jira.read); one shared project-allowlist normaliser used by the webhook, jira.comment and jira.read (`culture_rules`/extensions/jira/projects.py new, port.py, read.py, hooks.py)
- `t19` — Review fixes in culture-rules (PR #148): `working_path` normpath parity and rule-metacharacter refusal; segment refuses rule/glob metacharacters; ADF flatten never raises (non-dict attrs/items) and `_block` below Sonar's complexity 15; paging by len(chunk); a malformed projects value refuses everything; `to_adf` keeps an ordered list's start and never sends an empty document; the TriggerPicker doc comment; Sonar composite assertions
- `t20` — Review fixes in cultureagent (PR #61): `working_path` refuses characters that break Claude permission rules; the claude config dir is protected; operate runs only under acceptEdits; the prompt names the working path

## Actual Delivery

All 20 plan tasks are accounted for: all 20 delivered.

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `extensions/jira/adf.py` (`flatten`, `to_adf`); hardened in t19. Squash `d3d97778` (#148) |
| `t2` | delivered | `JiraClient.get_issue_full`; `add_comment` sends `to_adf` (one line byte-identical). #148 |
| `t3` | delivered | `data.author` on every Jira event (`hooks.py`). #148 |
| `t4` | delivered | `jira.read` kind and `read.py` port; catalogue snapshot regenerated. Merged before its colleague review (lapse `l4`); the review afterwards was clean. #148 |
| `t5` | delivered | `extensions/jira/filters.py` `users` filter, registered via `ext.trigger_filter`. #148 |
| `t6` | delivered | runner type `segment`; hardened by review (leading `-`, control characters) and t19 (rule metacharacters). #148 |
| `t7` | delivered | bridge `working_path`. An early version let a step raise an unpinned actor to `operate`; cortex's review found it, and `operate` became actor-pinned only. #148 |
| `t8` | delivered | cultureagent Claude bridge `operate` role (0.17.0); time-of-check race and `state_dir` fixes from review. Squash `403f4dd5` (cultureagent #61) |
| `t9` | delivered | `docs/actors/jira.md`, new `docs/operations/jira-conversations.md`; two claims corrected after a check (attempt budget, `list.filter`). #148 |
| `t10` | delivered | boundary test, amended by `d1` to pin the existing repodocs mentions. It caught t7's slug in comments before merge. #148 |
| `t11` | delivered | 0.46.0 bump, CHANGELOG, README index, the four harness prompt files, `deploy/release.json`. #148 |
| `t12` | delivered | `agentculture/jira-devague` `main` at `bd72fc1`, CI green, installed on core as a uv tool (0.1.0, 133 tests). Its cortex review found that the agent could pass `--devague`, `--root` or `--state-root`; the agent role now refuses them and reads its paths from the environment. Shape changed by `d7` |
| `t13` | delivered | the Claude bridge unit `cultureagent-claude-bridge-jira` (cultureagent 0.17.0, its own venv, 127.0.0.1:8096); the actor `jira-conversation` on `claude-fable-5-2` (Claude Code upgraded to 2.1.323 for it; risk `r1` resolved); the actor `jira-spec-library` (kind `spec`, `md`, private, its own key); `jira-app` gains `jira.read` and `projects` [SCRUM]; `core-server` gains `jira_devague_*`, `repo_url` and `git_head`. `git_head` uses the operator's gh login, so private repos work, and the bridge allowlist admits `agentculture` and `OriNachum` |
| `t14` | delivered | the workflows `jira-conversation-open` and `jira-conversation-round` and the rules `jira-conversation-open` and `jira-conversation-reply` (users: the operator; `concurrency_key` `jiraconv:{key}`; `max_attempts` 12; `on_failure` posts a comment). They shipped disabled and were enabled for the trial; now at revision 6. Changed by `d8`–`d11`; two instruction slips were found live (`l9`, `l10`) |
| `t15` | delivered | SCRUM-23, 2026-11-14/15: 8 runs, a converged spec exported to library item 147. Details in the Trial section |
| `t16` | delivered | editor offers `segment`; Jira Users field; a surface change drops the other app's filters (review fix). #148 |
| `t17` | delivered | `jira.comment` treats empty `projects` as every project. #148 |
| `t18` | delivered | `extensions/jira/projects.py` shared by webhook and both kinds; `bad_input` for a malformed key in both. #148 |
| `t19` | delivered | the eight culture-rules `/code-review` findings plus 13 SonarCloud issues (3, then 10). #148 |
| `t20` | delivered | the four cultureagent `/code-review` findings, four residual hardening fixes, and 7 SonarCloud issues. #61 |

## Mid-work Decisions

- `d1` — t10's boundary test pins today's files that already mention devague (the repodocs extension, its tests and fixtures, the web action-kinds fixture) with their reason, and fails on any new file mentioning it — repodocs already names .devague paths (extensions/repodocs/port.py:197, `__init__.py`, model/`action_kinds.py`:51), so a literal no-mention-anywhere test fails on main
- `d2` — The t8 task agent commits only; the main agent opens the cultureagent PR (and the culture-rules PR) at the final human gate — outward-facing pushes and PRs wait for gate 3 and the operator's OK
- `d3` — Add t16: the editor offers the runner argument type segment in the Actors command editor, and a Users field for jira.\* triggers in the trigger picker — the build surfaced two hand-written editor lists (web/src/actors/app-config.ts:478, web/src/rules/TriggerPicker.tsx) that would leave segment and the jira users filter CLI/MCP-only
- `d4` — Add t17: jira.comment treats an absent or empty connection.projects as every project, like the guide, the webhook and jira.read — jira.comment refused every issue with an empty projects list while docs/actors/jira.md step 6, hooks.`_allowed_keys` and jira.read accept all
- `d5` — Add t18: the whole-branch review's two fail-closed inconsistencies are fixed before the PR (`bad_input` for a malformed key in jira.comment; one case-insensitive project normaliser for the webhook and both actions) — operator chose to fix the final review's findings before opening the PR
- `d6` — Add t19 and t20: fix all ten verified /code-review findings on PR #148 and #61 (three operate security gaps, two crash paths, five smaller) plus SonarCloud's three open issues — operator chose to fix all ten review findings before merge
- The workforce split changed at gate 2: the operator first named local colleague models as builders, then chose Claude subagents as builders (Fable for t7/t8, Opus for t2/t4/t5, Sonnet for the rest) with the local cortex and worker models as reviewers before every merge.
- Security findings from colleague reviews were folded into their tasks rather than recorded as deviations: `operate` became actor-pinned only (t7); `segment` refuses a leading `-` and control characters (t6); the bridge resolves the working path once and re-checks it before spawn, and excludes `state_dir` (t8).
- t20's scope grew after a worker re-review: four residual gaps (relative `claude_env` paths, a guarded prompt note, a dedicated path error, comma and parenthesis checks on configured rules) were fixed on the operator's go-ahead, under `d6`.
- The SonarCloud gates of both repos flagged issues after the first pushes (13 on #148, 7 on #61); all were fixed as behaviour-preserving refactors under `d6`'s acceptance.
- `d7` — t12's wrapper keeps its snapshot and adjudication log outside the agent-writable frame dir, adds a role gate (the agent may run only `agent` and `check`), checks the frame before and after every run, and lets the agent file lapses but never confirm them — the plan's location let the operating agent rewrite its own baseline
- `d8` — each round resolves main's current head sha instead of pinning it per issue — pinning needs a stored sha read back generically, and the repo is read-only context
- `d9` — the repo comes from two Jira fields the operator created, repo-org (`customfield_10175`) and repo-name (`customfield_10174`), read by id; `repo_url` and `git_head` take `{org}` and `{repo}` segments — a field id survives a rename
- `d10` — the agent's final message follows the bridge's result contract (`{"summary": "<round JSON>", "threads_addressed": []}`), the comment is capped at 6000 characters, the agent step binds the issue key, and the instruction drops `jira-devague check` — a live probe found that every round would have failed at parse
- `d11` — the round instruction gains the `resolve` move for hard questions under a claim, and bold section labels; bold shipped as culture-rules 0.46.1 (#150) — the operator asked for bold labels, and `resolve` fixes the failed round on comment 10417
- Private repos work through the operator's gh login, both for `git_head` and for the bridge's clone via git's credential helper, which now points at `/snap/gh/current/gh` instead of a pinned snap revision. The operator approved this on 2026-11-14.
- `main` began requiring verified signatures on 2026-11-15, so #149's unsigned commits could not merge. The same change was re-made as one GitHub-signed commit through the GraphQL API (#150), and #149 was closed.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t10` (`d1`) | repodocs already names .devague paths (extensions/repodocs/port.py:197, `__init__.py`, model/`action_kinds.py`:51), so a literal no-mention-anywhere test fails on main | acceptable |
| `t8` (`d2`) | outward-facing pushes and PRs wait for gate 3 and the operator's OK | acceptable |
| `t16` (`d3`) | the build surfaced two hand-written editor lists (web/src/actors/app-config.ts:478, web/src/rules/TriggerPicker.tsx) that would leave segment and the jira users filter CLI/MCP-only | acceptable |
| `t17` (`d4`) | jira.comment refused every issue with an empty projects list while docs/actors/jira.md step 6, hooks.`_allowed_keys` and jira.read accept all | risky |
| `t18` (`d5`) | operator chose to fix the final review's findings before opening the PR | acceptable |
| `t19` (`d6`) | operator chose to fix all ten review findings before merge | risky |
| `t20` (`d6`) | the deviation names t19 as its task but covers t20 too; t20 then grew by four residual hardening fixes and 7 SonarCloud refactors | acceptable |
| `t12` (`d7`) | the plan's snapshot location was agent-writable | acceptable |
| `t14` (`d8`) | a pinned sha needs a stored value read back generically; the repo is read-only context | acceptable |
| `t14` (`d9`) | the operator created two fields; ids survive renames | acceptable |
| `t14` (`d10`) | the bridge keeps only the trailing object's `summary`; the agent may not run `jira-devague check` | acceptable |
| `t14` (`d11`) | the operator asked for bold labels; the missing `resolve` move failed a round | acceptable |

## Evidence

Validation ran on the merged commits: culture-rules `d3d97778` (squash of #148) and cultureagent `403f4dd5` (squash of #61), at 2026-11-13T16:18:34Z. Neither repo has a behavioral-test marker, so the named test files are the evidence. Records `e1`–`e14` are filed and approved by the operator.

- tests: `tests/node/test_jira_read_action.py` — pass (29) — `e1`
- tests: `tests/extensions/test_jira_adf.py`, `tests/extensions/test_jira_client_read.py` — pass (34) — `e2`
- tests: `tests/server/test_hooks_jira_author.py` — pass (3) — `e3`
- tests: `tests/extensions/test_jira_filters.py` — pass (25) — `e4`, `e5`
- tests: `tests/actors/test_code_segment_param.py` — pass (31) — `e6`
- tests: `tests/actors/test_bridge_working_path.py`, `tests/actors/test_bridge_agent.py`, `tests/server/test_bridge_callbacks_api.py` — pass (131) — `e7`
- tests: cultureagent `tests/bridges/test_claude_backend.py`, `test_claude_config.py`, `test_bridge_server.py` — pass (446) — `e8`
- tests: `tests/extensions/test_jira_client_read.py -k field` — pass (2) — `e9`
- tests: `tests/engine/test_on_failure.py` — pass (20) — `e10` (generic seam only)
- tests: `tests/node/test_concurrency_budget.py` — pass (70); `::test_budget_exhausted_then_human_push_resets`, `::test_coalescing_fires_the_latest_event_through_the_rule_that_recorded_it` — `e11`, `e12` (generic seams only)
- tests: `tests/test_extension_boundary.py::test_no_new_file_names_the_spec_tool` — pass (26 in file) — `e13`
- tests: `web/src/rules/TriggerPicker.jira.test.tsx`, `web/src/actors/CommandsEditor.test.tsx` — pass (15) — `e14`
- full suites before merge: culture-rules pytest 9001 passed, 8 skipped; web vitest 999, Playwright 121; cultureagent 1536 passed plus 6 unix-socket failures that also fail on `origin/main`
- lint: black, isort, flake8, bandit, markdownlint, `scripts/scan-secrets.py`, `teken cli doctor --strict` — clean on #148; black, isort, flake8, bandit, markdownlint — clean on #61
- CI: #148 green except `harness-smoke` (the cause was a per-user path in this PR's own plan records, missed at the time; fixed in 0.46.1); #61 green after re-running two existing flaky tests (`test_reap`, `test_empty_and_garbage_bodies`)
- commits: culture-rules `03c3a42c..d3d97778`; cultureagent `origin/main` before #61 `..403f4dd5`
- PRs: agentculture/culture-rules#148, agentculture/cultureagent#61
- behavioral deltas: `b1`, `b3`, `b4`, `b5`, `b6` (b6 supersedes b2), tracing `d1`, `d3`–`d6`

## Trial: SCRUM-23

The issue "Testing culturerules spec creation", with repo-org `agentculture` and repo-name `culture-rules`, ran on 2026-11-14 and 2026-11-15. The operator `557058:dbaf…` answered in comments.

| Run | On | Result |
|-----|----|--------|
| `run-0ada6525…` (open) | issue created | failed: repo-org empty; the failure comment 10409 |
| `run-6ec59c33…` (open) | a direct `workflows run` after the operator filled the fields (repo-org was first typed `agentcutlure`) | round one, comment 10410: claims c2–c8, scope s1–s3, questions q1–q3 |
| `run-618d84f6…` | comment 10411 | q1–q3 answered, c7 rejected (h7 with it), c6 amended; comment 10412 |
| `run-2fe2ff1c…` | comment 10413 | confirms applied; comment 10415 |
| `run-d666dbdd…` | comment 10414, posted 14 s after 10413 | held by the issue's concurrency key until 10415 was posted; applied o2; comment 10416 |
| `run-efe815c8…` | comment 10417 | failed: `decision_failed` on `question-resolve q4` (`l10`); the failure comment 10418; the earlier decisions in it stayed applied |
| `run-82bdccb7…` | comment 10419 | five hard questions closed with `resolve`; frame converged; exported and pushed **item 147**; comment 10420, the first with bold labels |
| `run-bed5e24b…` | comment 10421 (a repeat of 10419) | no decision, no second export; comment 10422 |

Measured:

- **Library:** item 147, "SCRUM-23: Testing culturerules spec creation". It is kind `spec`, private, `md`, sha256 `64e3f812…`, and byte-equal to `docs/specs/2026-11-14-scrum-23.md` in the issue's frame directory. It is the only spec item.
- **Events (`l1`, `r2`):** 14 `jira.comment.created` events on SCRUM-23, 14 distinct comment ids, no duplicates. The bot's own comments started no round.
- **Unhappy paths:**
  - a rejected claim (c7) and an amended claim (c6);
  - two replies during one round (o2): exactly one further round applied the second;
  - two failed rounds: each posted one comment with the error, and the next reply continued on the same frame (o6);
  - a repeated reply: a no-op round, so exactly one library item remains.
  - A comment from an account outside the answerer list was seen only from the bot, which is excluded as self-authored anyway; no third person was available.
- **No repo change (`c32`):** no PR and no new branch on GitHub came from the flow (the only PRs since are the operator's #149 and #150). All ten bridge checkouts are clean and detached at `main` commits.
- **The live probes before enabling:**
  - `l3`: comment paging and `/field` against the real site.
  - `l2`/`l8`: a live `operate` run on `claude-fable-5-2`. `jira-devague agent` works; `check`, `ls /` and writes to the checkout are refused; writes to the frame directory work.
  - A round-one run whose summary passed the parse shape.

Findings the agent reported in comment 10422 (per c6, defects go to a separate issue, not into this trial):

1. **Frame questions q1–q3 hold the wrong answers.** The frame's own q1–q3 were also resolved with the answers to the hard questions c4 q1, c10 q2 and c8 q3, because the two kinds share q-numbers. The claims hold the right text.
2. **h13 does not hold.** It says the library spec names the comment ids its decisions cite, but the exported spec holds none; they are only in the adjudication log.

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `c3` — `jira.read` returns description, every comment with author, and named fields; refusals happen before the secret | high | test `tests/node/test_jira_read_action.py` (`e1`) · PR #148 · every SCRUM-23 round's `read` step; paging and `/field` probed live (`l3`) |
| `c4` — `jira.comment` posts multi-line text as ADF paragraphs and lists; one line is byte-identical | high | tests `tests/extensions/test_jira_adf.py`, `test_jira_client_read.py` (`e2`) |
| `c7` — every Jira event carries `data.author`, and the `users` filter admits only listed authors, never the bot | high | tests `tests/server/test_hooks_jira_author.py` (`e3`), `tests/extensions/test_jira_filters.py` (`e4`) · `web/src/rules/TriggerPicker.jira.test.tsx` (`e14`) |
| `c33` — the author filter applies to issue-opened events too | high | test `tests/extensions/test_jira_filters.py` (`e5`) |
| `c9` — a registered command can take an issue key as one safe path segment | high | test `tests/actors/test_code_segment_param.py` (`e6`) |
| `c13` — a bridge step forwards a `working_path` only under an actor-pinned `operate` | high | tests `tests/actors/test_bridge_working_path.py` et al. (`e7`) |
| `c30` — the cultureagent `operate` role confines writes to one rule-safe directory on a read-only checkout, under `acceptEdits`, with Claude's config protected | high | cultureagent tests (`e8`) · PR cultureagent#61 · the live probe on `claude-fable-5-2` (`l2`/`l8`) · ten clean bridge checkouts after the trial |
| `c20` — a Jira custom field can feed the repo input | high | test `-k field` (`e9`) · SCRUM-23: repo-org and repo-name (`d9`) fed `repo_url` and `git_head` in every round; an empty field failed the round with a comment (10409) |
| `c28` — culture-rules contains no devague literal outside the pinned repodocs files | high | test `tests/test_extension_boundary.py::test_no_new_file_names_the_spec_tool` (`e13`) |
| `c37` — a failed round posts one Jira comment with the error code | high | test `tests/engine/test_on_failure.py` (`e10`) · SCRUM-23 comments 10409 (`actor_failed`) and 10418 (`decision_failed`), one each |
| `c38` — rounds are capped per issue by `max_attempts` | low | test `::test_budget_exhausted_then_human_push_resets` (`e11`). The live rules carry `max_attempts` 12, but the trial used 8 runs and never reached the cap |
| `c5` — replies during a round coalesce into one follow-up that re-reads the thread | high | test `::test_coalescing_fires_the_latest_event_through_the_rule_that_recorded_it` (`e12`) · SCRUM-23 comments 10413 and 10414: one further round (`run-d666dbdd…`) |
| `c14` — the finished spec reaches the library as kind `spec`, private | high | library item 147: kind `spec`, private, `md`, byte-equal to the export; `run-82bdccb7…` |
| `c34` — the operating agent cannot adjudicate its own proposals | high | the role gate refused `check` and the forbidden flags live (`l2`/`l8`, t12's review fix) · every entry in SCRUM-23's adjudication log cites an operator comment id and author · the agent left its own proposals pending in every round |
| `c32` — the flow changes no repo and opens no PR | high | no PR or branch from the flow on GitHub · all ten bridge checkouts clean and detached at `main` commits |
| `c25` / `c27` — a Jira issue yields a converged spec in the library; the SCRUM trial's measures | medium | SCRUM-23: 8 runs, five rounds that posted questions, one library item (147), no devague command run by hand. Capped: round one was started by a direct `workflows run` after a field typo, and the agent's two findings (wrong frame-question answers, h13) stand |

Lapse ledger evidence:

| Lapse | Code | What |
|-------|------|------|
| `l1` | `assumption-for-measurement` | Jira's double event on comments and its comment size limit are stated from memory, not measured against agentculture.atlassian.net |

| `l9` | `assumption-for-measurement` | t14's output shape was written without reading the bridge's result contract; found by a live probe before enabling |
| `l10` | `assumption-for-measurement` | t14's instruction lacked the `resolve` move for hard questions under a claim; the round on comment 10417 failed |

Also approved by the operator: `l2`, `l3`, `l4`, `l5`, `l6`, `l7`, `l8` (2026-11-13). `l1`, `l2`, `l3` and `l8` are now settled by live measurement (the Trial section).

## Remaining Work / Follow-up

- **The agent's two findings:**
  - Frame-level questions get answered with hard-question answers when the q-numbers collide.
  - The exported spec holds no comment ids (h13).

  Per c6 they go to a separate issue. The operator plans to replace jira-devague with a devague service operated by culture-rules, so they are requirements for that service — operator
- **Per-issue directory retention (`r6`):** ten bridge worktrees and the issue's frame directory remain on core after one trial — operator + main agent
- **`max_attempts` (c38):** never reached in the trial. When it runs out, the rerun is a direct `workflows run` (c11), which has no concurrency key — operator
- **`rollout verify`** still counts the disabled machine `spark` (a culture-rules follow-up) — main agent
- **cultureagent:**
  - raise the 8000-character summary cap, or pass the trailing object through;
  - two existing flaky tests (`test_reap`, `test_empty_and_garbage_bodies`);
  - Dependabot alerts on `main`.

  — cultureagent owner
- **Signed commits:** `main` now requires verified signatures. Commits made on core go through GitHub's API (as #150 did), or core's SSH key is registered with GitHub as a signing key — operator
- Follow-ups recorded in the plan: opening PRs from an agent's commits through culture-rules (`r7`), and Bash patterns as the operating agent's residual write risk (`r8`)
