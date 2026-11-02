# Build Plan — Newspaper sections on Discord

slug: `newspaper-sections-on-discord` · status: `exported` · from frame: `newspaper-sections-on-discord`

> The daily newspaper posts to Discord one message per section, routes the jobs section to #open-to-work, and can post a section as a tagged forum post — through generic step options, with every route, channel and tag in config

## Tasks

### t1 — markdown.`from_html` `split_at` and parts

- instruction: Owns `culture_rules`/node/actions/`markdown_html.py` and tests/node/`test_markdown_html.py` only. Add optional `split_at` (input or config, one of h1-h6, validated like the other params; bad value fails `bad_params`). Track each block's heading text and rank in `_Doc`; when `split_at` is set, build parts: a new part starts at every heading of rank <= `split_at`, each part holds its whole Markdown text (no `max_chars` cap), heading is the heading's plain text, text before the first such heading is a part with heading ''. Leave markdown, chunks and count exactly as today, and add the parts key only when `split_at` is given. Standard library only; deterministic.
- covers: c6, h5, c7, h6
- acceptance:
  - a test renders HTML shaped like the newspaper (h1, p.date, three h2 sections each with a ul) with `split_at` h2 and gets 4 parts in order: heading '' holding the masthead, then each section's heading and its whole text, index 0..3
  - a section longer than `max_chars` stays one part, while chunks still splits it as before
  - `split_at` h9 or 3 fails `bad_params` non-retryable
  - every existing test in tests/node/`test_markdown_html.py` passes unchanged, and output without `split_at` has no parts key

### t2 — Discord REST: channel read and forum thread create

- instruction: Owns `culture_rules`/apps/`discord_rest.py` and a new tests/apps/`test_discord_rest_forum.py`. Add DiscordClient.`get_channel`(`channel_id`) returning {id, type, flags, `available_tags`:\[{id,name}\]} via `_get_ok`, and `create_forum_post`(`channel_id`, name, text, `tag_ids`, \*, `allowed_mentions_parse`=()) that POSTs /channels/{id}/threads with {name, `applied_tags`, message:{content, `allowed_mentions`}} through self.`_transport` directly (never auto-retried), mirroring `post_message`'s error mapping (timeout -> `outcome_unknown` non-retryable, 429 retryable with `retry_after`, 5xx retryable, other 4xx refused with Discord's message), and returns {`thread_id`, `message_id`}. Validate channel ids with `_CHANNEL_RE`. No knowledge of tags by name here; no caching.
- covers: c5, h4
- acceptance:
  - FakeTransport tests: `get_channel` parses type 15 and `available_tags`; `create_forum_post` sends POST /channels/{id}/threads with name, `applied_tags` and message.content and `allowed_mentions` parse \[\]
  - `create_forum_post` maps a timeout to `outcome_unknown` non-retryable, 429 retryable, 503 retryable, 400 refused, and returns `thread_id` and `message_id` on 201
  - `post_message` and its existing tests are unchanged

### t3 — llm.complete briefs from actor config

- instruction: Owns `culture_rules`/node/actions/llm.py and tests/node/`test_llm_actor_briefs.py`. A step's brief name resolves first against the actor's params.briefs (a mapping of name to non-empty string), then against the three code briefs; an unknown name, a non-string brief, or an empty text fails `invalid_request` before any request. The step can only name a brief: no param supplies prompt text (any such param is ignored as today). Keep the cache rule tied to the recording code brief. Update the module docstring.
- covers: c20, h23
- acceptance:
  - a test with actor params.briefs {headline: 'X'} and step brief headline sends 'X' as the system message
  - brief 'nope' with no such actor or code brief fails `invalid_request` with no HTTP request
  - newspaper, transcript and recording select their code text exactly as before (existing llm tests pass unchanged)
  - an actor brief named like a code brief does not replace the code brief

### t4 — Admin guard on actor briefs

- instruction: Owns `culture_rules`/auth/guards.py, the place `check_runner_commands` is wired into saves (grep its callers in `culture_rules`/server and bundle import) and a new tests/server/`test_actor_briefs_admin.py`. Add BriefsDenied (code `briefs_admin_only`, HTTP 403 like RunnerCommandsDenied) and `check_actor_briefs`(principal, before, after): non-admins may not add, change or remove params.briefs; wire it everywhere `check_runner_commands` runs (API actor create/update, bundle import). Compare JSON-normalised values.
- covers: c30, h24
- acceptance:
  - an editor saving an actor with changed params.briefs gets 403 `briefs_admin_only` and the stored actor is unchanged
  - an admin save with changed briefs succeeds; an editor save leaving briefs unchanged succeeds
  - a bundle import by an editor that changes an actor's briefs is refused the same way

### t5 — discord.message posts to a forum

- instruction: Owns `culture_rules`/node/actions/message.py, `culture_rules`/model/`action_kinds.py` (discord.message gains `thread_name`=`_S` and tags=list) and a new tests/node/`test_discord_forum_post.py`. Validate before reading the secret: tags without `thread_name`, a non-list or non-string tag, or more than 5 tags fail `bad_params`; `thread_name` must be a non-empty string. With `thread_name`: `get_channel`; a channel that is not type 15 fails `bad_params` with no POST; resolve tag names exactly against `available_tags`, apply the found ids and list the unknown names in output `tags_missing`; cut `thread_name` to at most 100 characters at the last space (hard cut if none). If text fits `MAX_CONTENT`, one `create_forum_post`; otherwise split at line breaks into pieces of at most `MAX_CONTENT` (a single longer line cut hard), create the post with the first piece and `post_message` each later piece to the thread id in order. Any failure after the thread exists fails non-retryable with the thread id and the ids posted so far in the message. Outputs `message_id` (starter), `thread_id`, `reply_ids`, `tags_missing`. Buttons with `thread_name` fail `bad_params` (not in this work). Without `thread_name` the code path and outputs are unchanged.
- depends on: t2
- covers: c9, h8, c10, h9, c19, h12, c31, h25, c32, h26
- acceptance:
  - tests: `thread_name` on a type-0 channel fails `bad_params` with no POST; 6 tags fail `bad_params` with no request; tags without `thread_name` fail `bad_params`
  - tests: tags \['Jetson news','Nope'\] on a forum holding 'Jetson news' posts `applied_tags` \[its id\] and returns `tags_missing` \['Nope'\]
  - tests: a 4500-character text with line breaks creates the thread with at most 2000 characters and posts the rest as replies to the thread id, each at most 2000, joining back to the text
  - tests: thread POST 201 then a reply 503 fails non-retryable naming the thread id; a thread POST timeout fails `outcome_unknown` non-retryable
  - tests: a 140-character `thread_name` is sent as at most 100 characters ending at a word; every existing tests/node/`test_message_action.py` test passes unchanged

### t6 — Editor, guides and project docs

- instruction: Owns web/src/workflows/StepEditor.tsx (markdown.`from_html` declares `split_at` and the parts output), web/src/rules/ActionPicker.tsx (discord.message params `thread_name`: S, tags: list) with their tests, docs/operations/newspaper.md (`split_at`, parts, the forum flow as configuration), docs/actors/discord.md (a Forum posts section after Buttons: `thread_name`, tags by name, `tags_missing`, long posts as replies, limits and failures), docs/actors/llm.md (actor briefs and the admin guard), CLAUDE.md plus AGENTS.override.md, AGENTS.colleague.md and QWEN.md (one bullet each, in step), and CHANGELOG via a minor version bump. No targets-picker change.
- depends on: t1, t3, t4, t5
- covers: c8, h7, c11, h10
- acceptance:
  - web typecheck, vitest and Playwright pass; ActionPicker offers `thread_name` and tags for discord.message
  - markdownlint-cli2 passes on every touched .md; the four harness prompt files describe the change in the same words
  - `test_openapi_contract` passes with no change to the Discord targets schema

### t7 — No-BL audit, full gate and PR

- instruction: Main agent. Grep the branch diff under `culture_rules`/ for prompt wording, character budgets, title formats, newspaper, section names, tag names, channel ids; any hit is moved to config or removed. Then the full pre-PR gate (black, isort, flake8, bandit, full pytest -n auto, web typecheck/vitest/Playwright, markdownlint, scan-secrets, teken doctor), ask-colleague review, then the PR through cicd with 0 Sonar issues.
- depends on: t6
- covers: c1, h1, c2, h2, c29, h21
- acceptance:
  - the diff grep finds no prompt text, section, tag, channel or title-format literal under `culture_rules`/
  - every CI job passes and SonarCloud shows 0 open issues on the PR

### t8 — Live test on the testing forum

- instruction: After the release is rolled out (operator go-ahead for every live write): admin-set cortex-spark2 params.briefs.headline (wording and length as config, operator-approved text); create a disabled test workflow (not newspaper-to-discord) that run.reads a real issue's render html, splits at h2, skips the masthead and empty-section parts by when, writes a headline per part with brief headline (`on_error` skip), and posts each part to bot-experiments-forum 1566568383082799214 with `thread_name` heading - date - headline and tags from the heading (a jobs step with the literal tag Jetson jobs), plus a fallback step titled heading - date when the headline was skipped. Run it by hand with the issue `run_id` and read the run record and the forum.
- depends on: t7
- covers: c4, h3, c15, h11, c21, h14, c33, h27, c24, h16, c25, h17, c27, h19
- acceptance:
  - one real issue yields one forum post per section with results in bot-experiments-forum, none for the masthead or an empty section
  - each post carries exactly its matching tag, `tags_missing` is empty, and each title is heading - date - headline at most 100 characters
  - no post lands in the Jetson AI Lab server during this task

### t9 — Live rev 3 on the Jetson forum and 3-issue check

- instruction: Only after the operator approves t8's result: save newspaper-to-discord rev 2's body in the rollout worktree, dry-run the rollback update, then write rev 3 (`expected_revision` 2) with the t8 steps pointed at forum 1563965995788271617 and the size-cut posts to #bot-experimentations removed. Read the next 3 issues' runs and the forum; file evidence per issue.
- depends on: t8
- covers: c23, h15, c35, h29, c26, h18, c28, h20
- acceptance:
  - rev 3 is written with `expected_revision` 2 after operator approval, with rev 2 saved and the rollback dry run passing
  - for 3 consecutive issues: one tagged post per section with results in the Jetson forum, titles at most 100 characters, 0 failed newspaper-to-discord runs
