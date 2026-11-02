# Delivery Summary — Newspaper sections on Discord

plan: `newspaper-sections-on-discord` · run: `partial` · date: `2026-11-01`
baseline: `devague summary skeleton`

## Intent

> The daily newspaper posts to Discord one message per section, routes the jobs
> section to #open-to-work, and can post a section as a tagged forum post —
> through generic step options, with every route, channel and tag in config.

After: each day every newspaper section is its own forum post titled
topic - date - a one-line LLM summary, tagged with its topic (Jetson
optimization, Jetson jobs, Jetson news, Isaac ROS), so a reader filters the
forum by tag. The run built the generic capabilities (t1-t6), passed the full
gate (t7), proved the configured flow live on the testing forum (t8), and
stopped before t9: the operator answered "Not yet" to writing revision 3 of
the live workflow.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — markdown.`from_html` `split_at` and parts
- `t2` — Discord REST: channel read and forum thread create
- `t3` — llm.complete briefs from actor config
- `t4` — Admin guard on actor briefs
- `t5` — discord.message posts to a forum
- `t6` — Editor, guides and project docs
- `t7` — No-BL audit, full gate and PR
- `t8` — Live test on the testing forum
- `t9` — Live rev 3 on the Jetson forum and 3-issue check

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `split_at` (h1-h6) and the `parts` output in `markdown_html.py`, 15 new tests; the lead-part rule per `d3` (merge 895a17b) |
| `t2` | delivered | `DiscordClient.get_channel` and `create_forum_post`, 14 tests; extra caps per `d1` (merge 8c3ad72) |
| `t3` | delivered | actor `params.briefs` named by a step `brief`, code briefs locked (`d2`), 20 tests (merge f1e079a); then streaming (`d7`, merge d1f1eac, 27 tests) |
| `t4` | delivered | `check_actor_briefs` in `save_check`, 403 `briefs_admin_only` on create, update, import and model routes (`d4`), 35 tests |
| `t5` | delivered | `discord.message` `thread_name` and `tags`: forum check, tags by name and `tags_missing`, word-cut titles, replies for long text, non-retryable after the thread exists (`d5`), 31 tests (merge 3c8874f) |
| `t6` | delivered | editor fields and tests, guides (discord, llm, newspaper), the four harness files, 0.41.0 (merge 95fb4d9); `release.json` bump added in b6bf323 |
| `t7` | partial | no-BL grep clean, full lint gate and suite green (9012 passed after the `release.json` fix), ask-colleague review (doc fix 2160318); the PR is opened after this summary (`d6`) |
| `t8` | delivered | 0.41.0 (build 2160318) rolled out; test workflow `newspaper-sections-test` posted one real issue (11-02) to the testing forum: 4 tagged posts with headlines, then 3 after the empty-section skip |
| `t9` | blocked | revision 3 drafted, its dry run and the rollback dry run pass; the operator answered "Not yet", so rev 2 stays live and the 3-issue check has not started |

## Mid-work Decisions

- `d1` — `create_forum_post` also caps the name at 100 and the content at 2000 characters and adds Discord's 4xx message — a last-resort guard mirroring `post_message` (proposed)
- `d2` — the t3 criteria conflict resolved: code brief names always keep their code text, any other name comes from the actor (proposed)
- `d3` — `split_at` lead part: everything before the first heading of exactly that rank; no fallback (decider: hybrid 0.55, predictable 0.22) (proposed)
- `d4` — the briefs guard also covers create, every actor kind and the model routes through `save_check` (proposed)
- `d5` — forum-path interpretations: empty tags accepted, blank names refused, pieces join back with `''`, blank pieces not posted (proposed)
- `d6` — the PR waits for the live test, validation and this summary; t8 ran on a pre-merge build (proposed)
- `d7` — `llm.complete` always streams — operator 2026-11-02: "Always use stream" (approved)
- Test configuration (not code): `when` conditions use `exists summary`, because a comparison with a missing field is false (`== null` never matched); every body step skips a part whose text is the `_No results._` line (found by the c33 check).
- Headline model: `cortex-spark2` was not served; `cortex-spark` (GLM) was busy under LiveCodeBench; the operator allowed `senses-thor` for testing and as the temporary model. Endpoint actors `cortex-spark` and `senses-thor` were enrolled, each with the headline brief.
- Headline brief (configuration, operator 2026-11-02: "key words, maybe company names or specific roles", "tell a whole story", "10 words most"): the brief on `senses-thor`, `cortex-spark` and `cortex-spark2` asks for at most 10 words of specifics (companies, products, versions, roles); run-01d57da9 gave e.g. `NVIDIA JetPack 5 EOL Jetson Orin NX AGX Thor`; the jobs title (110 characters) is word-cut to 100.
- The 11-02 issue failed on the keysets work's v5 sections (more than 5 queries, lapse `l3` on that frame); v6 was applied and the issue rerun by hand before t8 used it.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t1` (`d3`) | the instruction's rule contradicted its own acceptance test; one-sentence lead-part rule | acceptable |
| `t2` (`d1`) | last-resort caps so an oversized field is never sent | acceptable |
| `t3` (`d2`) | criteria 1 and 4 conflicted; code names stay locked | acceptable |
| `t3` (`d7`) | operator 2026-11-02: "Always use stream" | acceptable |
| `t4` (`d4`) | one wiring point covers every actor write path | acceptable |
| `t5` (`d5`) | interpretations the brief left open, each tested | acceptable |
| `t7` (`d6`) | PR after the live test, validation and summary; pre-merge build live | risky |
| `t8` | the planned headline model (`cortex-spark2`) was down; ran on `senses-thor`; the test config needed two fixes (`exists`, the empty-section skip) | acceptable |
| `t9` | not started: the operator held revision 3 | needs-follow-up |

## Evidence

- tests: `tests/node/test_markdown_html.py` (37), `tests/node/test_discord_forum_post.py` (31), `tests/apps/test_discord_rest_forum.py` (14), `tests/node/test_llm_actor_briefs.py` (20), `tests/node/test_llm_stream.py` (27), `tests/server/test_actor_briefs_admin.py` (35), `tests/node/test_message_action.py` (33) — pass at 0f3ae1b; vitest `ActionPicker.discord.test.tsx` + `StepEditor.builtin.test.tsx` (29) — pass
- full suite: 9012 passed, 1 failed (`test_release_json_matches_pyproject…`), fixed in b6bf323 (19 packaging tests pass); Playwright 129/129 and vitest 926/926 in t6
- lint: black, isort, flake8, bandit, `markdownlint-cli2` (125 files, 0 errors), `scan-secrets` clean, `teken cli doctor --strict` 26/26, harness-smoke 6/6
- live: rollout 0.41.0 `rollout verify` ok (4 nodes, API, 20 rules = snapshot); runs run-26ca0b80 (fallback titles), run-2e7ddf78 (4 headline posts), run-56e58940 (3 posts, empty section skipped), run-01d57da9 (keyword headlines)
- devague: obligations o1-o16, evidence e1-e17 (proposed); the c33 failure (e16) and its fix (e17) both filed
- commits: `b04d991..83961a7` on `rules/discord-sections`
- PRs / issues: the PR for this branch (opened after this summary)

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `markdown.from_html` `split_at` gives one part per section; output without it unchanged (c6, c7) | high | e1, e2 · `tests/node/test_markdown_html.py` at 0f3ae1b |
| `discord.message` posts a tagged forum post, reports unknown tags, cuts titles, continues long text as replies, never re-posts after the thread exists, refuses more than 5 tags (c9, c19, c31, c32) | high | e3, e5-e7 · `tests/node/test_discord_forum_post.py` |
| forum-post failures map like messages and are never auto-retried (c10) | high | e4 · `tests/apps/test_discord_rest_forum.py` |
| a step names an actor brief; code briefs stay locked (c20) | high | e8 · `tests/node/test_llm_actor_briefs.py` |
| only admins change an actor's briefs (c30) | high | e9 · `tests/server/test_actor_briefs_admin.py` |
| the editor offers the new options (c8, c11) | high | e10, e11 · vitest |
| `llm.complete` streams (d7) | medium | `tests/node/test_llm_stream.py` (27); not yet live: the running build 2160318 predates it |
| one real issue posts to the testing forum as tagged posts titled topic - date - headline within 100 characters (c28 testing half, c21, c4, c15) | high | e12-e15 · run-2e7ddf78 |
| empty sections are never posted (c33) | high | e16 (fail, run-2e7ddf78) then e17 (pass, run-56e58940) — a configuration fix |
| the Jetson forum receives tagged posts for 3 issues (c28 Jetson half, c23, c26, c35) | unverified | (t9 not started — not claimed done) |
| the API cannot read the bot token, so forum reads stay in the node (c5) | unverified | (a design boundary; no test) |

Lapse ledger evidence:

| Lapse | Code | What |
|-------|------|------|
| `l1` | `control-absent` | h22 was put to the operator with a bare angle-bracket placeholder that fails MD033; lint not run before asking |
| `l2` | `provenance-missing` | s16 and c20 asserted actor params are admin-only without reading the role policy |

## Remaining Work / Follow-up

- `t9` — write `newspaper-to-discord` rev 3 (`ntd-rev3.json`, expected revision 2; rollback body `ntd-rev2-rollback.json`, both in the rollout worktree) when the operator approves; then check 11-03 onwards for 3 issues and file c28/c23/c26/c35 evidence
- Roll out a build with `d7` (streaming) — needs the operator's go-ahead
- Headline model: `senses-thor` is temporary; move to the worker (Nemotron Super on Thor) when it is served
- Daily summaries still use `cortex-spark2`, which is not served — a configuration change to ask about
- `r1` — validate the shape of `params.briefs` at save time
- `r2` — `actors update` dry run accepts the server-owned `updated_at` that the apply refuses
- Parks from the frame: move the three code briefs into actor config; a manual rerun of `newspaper-to-discord` duplicates posts
- `newspaper-sections-test` (no rule) and the testing-forum posts stay as test artefacts
