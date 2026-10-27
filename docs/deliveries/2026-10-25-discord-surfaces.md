# Delivery Summary — discord surfaces

plan: `discord-surfaces` · run: `complete` · date: `2026-10-25` · updated: `2026-10-27`
baseline: `devague summary skeleton`

## Intent

> culture-rules listens and acts on Discord through two bots across two servers, and a
> Discord message can land in the library as an item — the first of the Discord automations

After: Two Discord app actors (discord-bot = Culture.dev, jetson-bot) listen across both
servers; a message saves to the library by a reaction, by a slash or message command, by being
posted in a chosen channel (or any channel), or by a scheduled sweep; its text and links land
as a Markdown item and its pdf/epub attachments as their own items

The plan has three releases. The operator approved gate 2 for Release 1 first (t1–t11), then
gave a fresh go for Releases 2–3 on 2026-10-26. Release 1 shipped in three PRs (#96
0.29.0, #98 0.30.0, #100 0.30.1; summarised in #101). Release 2 shipped in #102 (0.31.0) and Release
3 in #103 (0.32.0); four follow-ups came out of the live check t20: #104 (0.32.1), #105
(0.33.0), #106 (0.34.0) and #107 (0.35.0). 0.35.0 runs on every node and the API since
2026-10-27 (`rollout verify` ok), and t20 exercised every gesture live on both bots.

Every plan task is delivered. Until 2026-10-27 the run was recorded `partial` for `t11`
alone, on the premise that Culture.dev still had to join the operator's private server; the
operator then confirmed that JetsonBot Experiments *is* that private server, so `t11` was
already met (`e80` supersedes `e67`). One claim stays failing: `c8`, half checked.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — \[R1\] Actor-scoped Discord delivery id
- `t2` — \[R1\] Discord trigger filters in matching: bots, channels, who
- `t3` — \[R1\] connection.`guild_id` accepts a list
- `t4` — \[R1\] Discord event retention and unselected-channel drop
- `t5` — \[R1\] Generic markdown.render builtin
- `t6` — \[R1\] Discord REST reads with 429 handling
- `t7` — \[R1\] discord.read action
- `t8` — \[R1\] Wire-up: kinds, phrases, runner, explain, OpenAPI
- `t9` — \[R1\] Editor: Discord trigger filters, guild list, new steps
- `t10` — \[R1\] Docs and disabled example bundle
- `t11` — \[R1\] Live rollout: second bot and the channel flow
- `t12` — \[R2\] Raw reaction event discord.reaction.added
- `t13` — \[R2\] Generic attachment.fetch through the vetted opener
- `t14` — \[R2\] discord.react action
- `t15` — \[R2\] Wire-up and editor for reactions and attachments
- `t16` — \[R3\] Slash and message commands over the gateway
- `t17` — \[R3\] Ephemeral command reply action
- `t18` — \[R3\] Scheduled history sweep with a cursor
- `t19` — \[R3\] Wire-up and editor for commands, reply and sweep
- `t20` — \[R\*\] Live validation of all gestures on both bots

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | the gateway sinks an actor-scoped delivery id, so each bot records its own event for one message and one bot's redelivery is a duplicate (#96) |
| `t2` | delivered | `model/discord_filters.py`: `include_bots`, `channels` (list or "all"), `users` and `roles` (users OR roles, per `d1`) in matching (#96) |
| `t3` | delivered | `connection.guild_id` takes one id or a list in the gateway filter, the targets route and the editor (#96) |
| `t4` | delivered | Discord events carry a 30-day expiry with a TTL index; channels no enabled `discord.*` rule selects are not stored (#96) |
| `t5` | delivered | the builtin `markdown.render`: a deterministic page and its `content_base64` for `library.push` (#96) |
| `t6` | delivered | Discord REST reads retry a 429 after its `retry_after`, bounded per run (#96) |
| `t7` | delivered | the action kind `discord.read`: one message in full, with attachments, from REST (#96) |
| `t8` | delivered | catalogue, describe phrases, node runner, explain and OpenAPI entries for the new kind and builtin (#96) |
| `t9` | delivered | trigger editor for the Discord filters, `guild_id` as a list, and `markdown.render` offered as a code-step builtin in the step panel, not ActionPicker (`d3`) (#96) |
| `t10` | delivered | `docs/actors/discord.md`, `docs/actors/library.md` and the disabled bundle `docs/rules/discord-library` (#96); the bundle moved to kind `discord-message` and a literal actor, with an end-to-end test (#100, `d6`) |
| `t11` | delivered | 0.30.1 runs on all four nodes and the API; the jetson-bot actor exists; library key 10 and the library actor accept `discord-message`; a `#spark-tests` message saved as private Markdown item 72, and saving it again gave `created: false` (`d2`, `d4`–`d6`). Since Release 2–3 jetson-bot also listens (two gateway leases), and its MESSAGE CONTENT intent is verified live (`e61`). Culture.dev listens and saves in JetsonBot Experiments, which is the operator's private server (operator 2026-10-27; `e80`) |
| `t12` | delivered | `discord.reaction.added` from discord.py's raw reaction event (emoji name and id, the reactor, `user_roles`), the `emoji` trigger filter (one grammar, U+FE0F ignored), declared per actor (#102) |
| `t13` | delivered | the builtin `attachment.fetch` through http.call's vetted pinned opener; files written on the node and read by `library.push`'s `content_path` (`d7`); Markdown too (`d10`); a User-Agent the CDN accepts and the 4xx status on a skip (#104); placement on the node holding the files documented (#107, `l14`) (#102, #103) |
| `t14` | delivered | the action kind `discord.react`, guild-confined (#102); `remove` takes back the bot's own earlier marks (`d13`, #107) |
| `t15` | delivered | the node, catalogue, describe words and editor for reactions and attachments (#102); the progress marks ⏳ → ⌛ → ✅/⛔ are live configuration (`d13`) |
| `t16` | delivered | slash and message commands over the gateway: deferred within Discord's window, the token kept apart, synced per guild only on change (#103); `connection.command_guild_id` with withdrawal of servers left out (`d11`, #105); the commands themselves are each actor's `params.commands`, with the `command` trigger filter (`d12`, #106) |
| `t17` | delivered | the action kind `discord.reply`, a private answer through the kept interaction token (#103) |
| `t18` | delivered | `discord.sweep` (a channel since a stored cursor) and the generic builtin `cursor.advance` (compare-and-set) (#103); a loop body reads each message through a builtin's `config.with` (`d8`) |
| `t19` | delivered | the node, catalogue, describe words and editor for commands, reply and sweep (#103); the editor's Commands rows and Command filter (#106) |
| `t20` | delivered | every gesture ran live on both bots in JetsonBot Experiments (`d9`) on 2026-10-27: messages, 📚 with ⏳⌛✅, `/save` and Save to library, the 10-minute sweep, pdf/epub/md attachments, one item per message, 26 human-triggered runs and none from bot posts (`e66`); discord-bot-cli's live suite passed while jetson-bot's gateway was held, jetson-ai-lab-cli was not run (`e69`). Three live problems were fixed on the way: the CDN User-Agent (#104, `l11`), unplaced attachment steps (`l14`) and a shared concurrency key (`l15`) |

## Mid-work Decisions

- `d1` — t2's Discord who-filter changes after review: users and roles combine as EITHER (a
  listed user or anyone holding a listed role), not both; a bare-string channels/users/roles
  value (other than channels "all") is a one-item list instead of no filter; a users filter
  never admits an event with no author id or name — operator decision 2026-10-25 on the wave 1
  Qwen review: q5 reads as listed users OR opt-in roles; two fail-open edges found in
  matching.`_discord_admits`
- `d2` — server assignment changes: Culture.dev (actor discord-bot) listens in JetsonBot
  experiments (1413999242648748195) and the operator's private server; jetson-bot listens only
  in the public Jetson AI Lab Research Group (1326246312072581160), not in experiments. The
  spec's after-state 'two bots across both servers' becomes one bot per server set, three
  servers in all — operator decision 2026-10-25 while setting the jetson-bot grants
- `d3` — t9 offers markdown.render in the workflow step panel (a Built-in select on code
  steps, filling its typed ports) instead of in ActionPicker: markdown.render is a code-step
  builtin, not an action kind, and no picker offered builtins before — plan text assumed
  markdown.render was an action kind; the t9 agent found it is config.builtin on a code step
- `d4` — Posting in Jetson AI Lab is decided: jetson-bot posts each daily newspaper's headlines
  and section summaries (Discord-sized Markdown chunks) to channel 1405689195862818979 of the
  public server, through a live continuation rule on rules.run.succeeded of daily-newspaper; a
  new builtin newspaper.digest builds the chunks from the stored issue; ships with Release 1 as
  0.30.0 — operator request 2026-10-25: send the daily newsletter to that channel, as a
  rule/extension, test once with today's issue; library.culture.dev is behind Cloudflare
  Access so a link alone would not open for public members
- `d5` — d4's posting flow is built from generic steps instead of a newspaper-specific
  builtin: run.read (a step's outputs from the verified upstream run) and
  markdown.`from_html` (HTML as Markdown chunks) replace newspaper.digest; the newspaper
  specifics (render step, classes to drop, the Discord channel) live only in the live flow's
  config — operator rule 2026-10-25: the code shouldn't have the newsletter, that's
  configuration; code holds schedule, web search, passing data, previous run results, convert
  to Markdown, post to Discord
- `d6` — Discord items take the library kind discord-message (discord-react reserved for
  reactions), not document; culture-library must add the kind (culture-library#19), so the
  spec's 'culture-library unchanged' boundary no longer holds for kinds. Until it ships the
  live #spark-tests rule is disabled; the committed bundle and docs move from document to
  discord-message, and the bundle names its bot literally (an action step's actor is never a
  reference) — operator 2026-10-25: 'It shouldn't be a document. It's a message, or discord
  (or discord-message, so we can add later discord-react)'; first live test also showed
  actor: inputs.actor cannot resolve
- `d7` — attachment bytes never enter run state: attachment.fetch writes each fetched pdf/epub
  under a root on the node (an `output_root`, sha256-named, written atomically) and outputs its
  path, sha256, size and format instead of `content_base64`; library.push gains `content_path` +
  `content_root` (read only under the root, symlinks refused), as llm.complete reads a
  transcript; fetched files expire by a retention rule. The 50 MiB per-file limit stands —
  operator decision 2026-10-26: MongoDB caps a run document at 16 MiB, so base64 in step outputs
  fails a run at a ~12 MiB PDF (or a few smaller attachments in one loop) and t13's 50 MiB limit
  was unreachable; files on the node chosen over lowering the cap
- `d8` — a builtin code step's config.with maps references onto its inputs (e.g. {"text":
  "inputs.item.content"}), resolved as an action step's params are (whole strings, {{ }}
  templates, $ref/$literal) against the step's input ports only, and validated the same way
  (only `inputs.<declared port>`); an edge's value for the same port wins. Every builtin gains it,
  so a `for_each` body can render each swept message and the sweep flow is configuration only —
  operator decision 2026-10-26: edges carry whole ports and only action steps resolved
  inputs.item.\* references, so no loop body could hand a swept message's fields to
  markdown.render; config.with chosen over a rendering sweep (against the generic-steps rule)
  and dotted edge ports (engine, validation and editor wiring)
- `d9` — jetson-bot joins JetsonBot Experiments (1413999242648748195) for testing: t20's gesture
  checks run there for both bots (reaction, /save and Save to library, a channel message, a
  schedule tick, pdf/epub attachments, no cross-bot firing). jetson-bot's connection.`guild_id`
  becomes a list (the public Jetson AI Lab Research Group 1326246312072581160 and Experiments);
  in the public server it stays as it is (posting the newspaper), and no save flow is enabled
  there — operator decision 2026-10-26: d2 kept jetson-bot out of Experiments, but t20 needs a
  private test channel on each bot; the operator chose to test jetson-bot in Experiments over a
  reduced check or Culture.dev only
- `d10` — attachment.fetch also saves Markdown attachments: content type text/markdown (or a .md
  file name when the type is missing or octet-stream) as format md, under the same rules as pdf
  and epub (https from Discord's CDN, no redirects, no private addresses, at most 50 MiB, a file
  under the attachments root, sha256-named); images and other types stay `unsupported_format` —
  operator decision 2026-10-27 during the live Release 2 check: the operator's test upload was a
  Markdown runbook (mjolnir-orin-runbook.md), which the pdf/epub-only fetch would skip; the
  library actor already lists the md format
- `d11` — a Discord app actor's connection.`command_guild_id` (one id or a list, within
  `guild_id`) narrows where /save and Save to library are registered and answered; an invocation
  from another server is answered `NOT_HERE` and records no event; unset keeps every `guild_id`
  server. jetson-bot sets it to JetsonBot Experiments (1413999242648748195) so its commands stay
  out of the public server until its admins agree — operator 2026-10-27: jetson-bot's /save
  registered on Experiments only, then shared with the public server's admins to agree on the
  behavior; a generic actor option, the choice is configuration
- `d12` — Discord application commands are actor configuration, not code: a Discord app actor's
  params.commands lists its commands ({name, type slash|message, description, options \[{name,
  description, kind string|message, required}\], `not_here`}); the gateway builds and syncs the
  command tree from that list (the hash over it, so a removed command disappears), a kind
  message option parses a message link or id into `target_message_id`/`target_channel_id`, a
  message command targets the right-clicked message; a trigger filter command picks a flow's
  command; the unhandled answer is neutral by default with an optional per-command `not_here`
  text. /save and Save to library move to the disabled discord-library template as an example —
  operator 2026-10-27: /save should be generic; flows must not assume a specific command (flows
  are configuration, abilities are code). No live impact: neither bot has an `application_id`,
  so no commands are registered
- `d13` — discord.react takes remove (a list of emoji): the bot takes back its own earlier
  reactions after adding emoji (either alone allowed), via a new DiscordClient.`remove_reaction`
  (DELETE .../@me); the progress marks themselves (⏳ at start, ⌛ after a middle step, ✅ or ⛔ at
  the end, each switch removing the old) are the reaction flows' live configuration — operator
  2026-10-27 during t20: the workflow should add ⏳ as it starts, ⌛ at a meaningful middle step,
  then ✅, removing the old emoji at every switch; removing a reaction needs a generic ability,
  the marks are configuration
- `d2` speaks of the operator's private server as a third server; on 2026-10-27 the operator
  confirmed it is JetsonBot Experiments, so there are two servers in all.
- Release 2–3 decisions no deviation record covers:
  - `discord.py` joined the dev dependency group (#105): the command-tree tests had been
    skipped everywhere since Release 3 (`l12`).
  - The live attachment flows place `attachment.fetch`, the file loop and its push on spark,
    the node holding the attachments root (`l14`); the guide now says so (#107).
  - Every live Discord rule got its own concurrency key (`discord-<flow>:<bot>:{id}`):
    keys are global and coalesce, so a shared key lost a message event (`l15`); the template
    and its README follow (#107).
  - After t20, jetson-bot's four `#spark-tests` rules were disabled (operator, 2026-10-27);
    its commands stay registered in Experiments only (`d11`).
  - Each rollout of 0.31.0–0.35.0 again skipped `workflows migrate-notes`, for the reason
    below.
- The newspaper post went to `#bot-experimentations` (1407337283794767873), not `#bot-news`
  (1405689195862818979) as `d4` names: jetson-bot is denied Send Messages in `#bot-news`. The
  operator picked the channel; no deviation record covers it (`b1` records it).
- After `/code-review` of #100, the literal-actor check widened to a rule's action and
  `on_failure`, and became save-time only so stored documents still load (`b4`, approved).
- At the 0.30.1 rollout, `workflows migrate-notes` was **not applied**. It would have moved
  the live `newspaper-to-discord` workflow's plain-language step descriptions into notes and
  blanked them; that flow was written after the phrases change, so nothing in it needed to
  move. No later rollout step depends on it.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t2` (`d1`) | operator decision 2026-10-25 on the wave 1 Qwen review: q5 reads as listed users OR opt-in roles; two fail-open edges found in matching.`_discord_admits` | acceptable |
| `t11` (`d2`) | operator decision 2026-10-25 while setting the jetson-bot grants | acceptable |
| `t9` (`d3`) | plan text assumed markdown.render was an action kind; the t9 agent found it is config.builtin on a code step | acceptable |
| `t11` (`d4`) | operator request 2026-10-25: send the daily newsletter to that channel, as a rule/extension, test once with today's issue; library.culture.dev is behind Cloudflare Access so a link alone would not open for public members | acceptable |
| `t11` (`d5`) | operator rule 2026-10-25: the code shouldn't have the newsletter, that's configuration; code holds schedule, web search, passing data, previous run results, convert to Markdown, post to Discord | acceptable |
| `t11` (`d6`) | operator 2026-10-25: 'It shouldn't be a document. It's a message, or discord (or discord-message, so we can add later discord-react)'; first live test also showed actor: inputs.actor cannot resolve | needs-follow-up |
| `t11` | the newspaper posts in `#bot-experimentations` because `#bot-news` denies Send Messages | needs-follow-up |
| `t13` (`d7`) | operator decision 2026-10-26: MongoDB caps a run document at 16 MiB, so base64 in step outputs fails a run at a ~12 MiB PDF (or a few smaller attachments in one loop) and t13's 50 MiB limit was unreachable; files on the node chosen over lowering the cap | acceptable |
| `t19` (`d8`) | operator decision 2026-10-26: edges carry whole ports and only action steps resolved inputs.item.\* references, so no loop body could hand a swept message's fields to markdown.render; config.with chosen over a rendering sweep (against the generic-steps rule) and dotted edge ports (engine, validation and editor wiring) | acceptable |
| `t20` (`d9`) | operator decision 2026-10-26: d2 kept jetson-bot out of Experiments, but t20 needs a private test channel on each bot; the operator chose to test jetson-bot in Experiments over a reduced check or Culture.dev only | acceptable |
| `t13` (`d10`) | operator decision 2026-10-27 during the live Release 2 check: the operator's test upload was a Markdown runbook (mjolnir-orin-runbook.md), which the pdf/epub-only fetch would skip; the library actor already lists the md format | acceptable |
| `t20` (`d11`) | operator 2026-10-27: jetson-bot's /save registered on Experiments only, then shared with the public server's admins to agree on the behavior; a generic actor option, the choice is configuration | acceptable |
| `t16` (`d12`) | operator 2026-10-27: /save should be generic; flows must not assume a specific command (flows are configuration, abilities are code). No live impact: neither bot has an `application_id`, so no commands are registered | acceptable |
| `t15` (`d13`) | operator 2026-10-27 during t20: the workflow should add ⏳ as it starts, ⌛ at a meaningful middle step, then ✅, removing the old emoji at every switch; removing a reaction needs a generic ability, the marks are configuration | acceptable |
| `t13` | shipped (0.31.0, 0.32.0) unable to fetch any real attachment: Discord's CDN refuses Python's default User-Agent; fixed in #104 (`l11`, `b5` superseded by `b6`) | acceptable |
| `t20` | the live flows needed configuration fixes the plan did not foresee: placement of the attachment steps (`l14`) and per-flow concurrency keys (`l15`) | acceptable |

`d6` is needs-follow-up in its record; its follow-up landed: culture-library 0.17.0 shipped
the kind (#19), the `#spark-tests` rule was re-enabled, and #100 moved the bundle (`b2`, `b3`).

## Evidence

- tests: the validate-delivery run on main `632a822` (2026-10-25T23:34:15): 407 Python tests
  (`tests/apps`, `engine`, `node`, `events`, `rules`, `model`, `server`), 1 retention test
  against a real MongoDB and 48 editor vitest tests — all pass. The repo has no behavioural
  marker or folder; each claim's own tests are its behavioural tests (recorded on every
  evidence record).
- evidence records: `e1`–`e37` against obligations `o1`–`o16`, all `pass`, approved by the
  operator 2026-10-26. `o17` (`c28`) and `o18` (`c39`) carry no evidence and were approved as
  unchecked.
- CI: #100's checks green (test, web, lint, harness-smoke, version-check, GitGuardian);
  SonarCloud gate OK with 0 open issues.
- live: newspaper post run `run-2c77aee79f2c472da6493d372d69876d` (`b1`); Discord → library
  run `run-3f447a9aac568caa6b12ee3ba1235a60` → item 72 created, re-run
  `run-095cdaa3be5c4d4aa04ec1060c21028e` → item 72 `created: false` (`b2`); `rollout verify`
  for 0.30.1 on 2026-10-26 (4 nodes and the API on 0.30.1, 10 rules = snapshot).
- release diff: `git diff e936acf 632a822 -- culture_rules/` has no `jetson` hit.
- commits: `e936acf..632a822` (`6af7066`, `a2fc8b1`, `632a822`)
- PRs / issues: #96, #98, #100; culture-library#18, culture-library#19

Releases 2–3:

- tests: the validate-delivery run on main `feddca1` (2026-10-27T10:41:07): the 16 Release
  2–3 test files (`tests/rules/test_discord_reaction_flow.py`,
  `test_discord_command_and_sweep_flows.py`, `tests/node/test_attachment_fetch.py`,
  `test_discord_react.py`, `test_discord_reply.py`, `test_discord_sweep.py`,
  `test_discord_wiring.py`, `test_http_call.py`, `tests/apps/test_discord_commands.py`,
  `test_discord_gateway.py`, `test_discord_rest.py`, `tests/engine/test_builtin_with.py`,
  `test_discord_trigger_filters.py`, `tests/model/test_builtin_with_validation.py`,
  `test_discord_command_config.py`, `test_discord_trigger_params.py`): 510 tests, all pass,
  with discord.py installed (since #105 the command-tree tests run in CI too).
- evidence records: `e38`–`e62` against obligations `o19`–`o32` and `o17`, approved by the
  operator 2026-10-27; after the `/code-review` of #108, seventeen of them are superseded by
  `e63`–`e79` (run times and commits per run, the epub's existing item, culture-library's one
  commit, whole-file test refs). `e69` (`c8`) records **fail**: one of the two CLIs. `e67`
  (`c24`) recorded a fail on a wrong premise (a private server other than Experiments); `e80`
  supersedes it with **pass**, approved by the operator 2026-10-27.
- CI: #102–#107 merged with every check green and SonarCloud at 0 open issues.
- live (2026-10-27, on 0.32.1–0.35.0): 26 gesture runs on both bots, each triggered by a
  person (`e66`); pdf new item 92, Markdown new item 85, the epub matching item 67 that the
  library already held, a png skipped with ⛔;
  ⏳ → ⌛ → ✅ on both bots (`run-ec9bfd743757dd4f9e7ac62359ec1ace`,
  `run-51dc505850b54aee54a46e71d784a1fc`); `/save` and Save to library on both bots
  (`e49`); both sweeps filing items 87–89 (`e65`); Discord's command listing per app and
  server (`e51`); discord-bot-cli `6cd4970` live suite, 6 passed, while jetson-bot's gateway
  was held, jetson-ai-lab-cli not run (`e69`); `rollout verify` for 0.35.0 (4 nodes and the API, 17 rules).
- commits: `632a822..feddca1` (`6e83664`, `73e1d29`, `2e9a855`, `e63d160`, `494ed46`,
  `9c5f7a6`, `feddca1`)
- PRs: #101, #102, #103, #104, #105, #106, #107

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `c3` — a second Discord app actor (jetson-bot) works with no engine change | high | `e36` · live run `run-2c77aee79f2c472da6493d372d69876d` (posting) · since Release 2–3 both bots hold their own gateway lease and save live side by side (`e66`) |
| `c4` — each bot records its own event for the same message | high | `e1`–`e2` · `tests/apps/test_discord_gateway.py::test_two_actors_sinking_the_same_message_record_two_events` |
| `c5` — another bot's messages are skipped unless a rule opts in (`include_bots`) | high | `e4`–`e5` · `tests/engine/test_discord_trigger_filters.py::test_bot_blocked_unless_include_bots` |
| `c6` — a Discord message can become a library item | high | `e19`–`e21` · `tests/rules/test_discord_library_bundle.py::test_a_message_becomes_one_private_discord_message_item` · live item 72 |
| `c11` — tests cover two Discord actors together | high | `e18` · `tests/node/test_discord_stage.py::test_two_discord_actors_two_leases_two_events_and_no_cross_bot_firing` |
| `c12` — library automations are configured live; only a disabled example bundle is committed | high | `e35` · `tests/rules/test_discord_library_bundle.py::test_it_imports_disabled_with_the_placeholder_channel` |
| `c17` — a flow chooses its channels (a list or "all") | high | `e6`–`e9` · `tests/engine/test_discord_trigger_filters.py::test_channels_filter` |
| `c19` — one actor per bot; `guild_id` is one id or a list | high | `e13`–`e14` · `tests/apps/test_discord_gateway.py::test_a_guild_id_list_takes_messages_from_each_listed_guild` |
| `c26` — saving is idempotent (one item per message) | medium | `e22`–`e24` · `tests/node/test_markdown_render.py::test_same_inputs_are_byte_identical` (never shown red, `l1`) · `tests/rules/test_discord_library_bundle.py::test_the_same_message_again_files_no_second_item` · live item 72 `created: false` |
| `c27` — Discord content lands private by default | medium | a push that names no visibility sends none: `tests/node/test_library_push.py::test_upload_carries_file_title_author_and_kind` (`e33` cites a test that only checks the push completes, `l5`); the bundle sets `private` explicitly (`e34`); culture-library's own private default is not tested in this repo |
| `c28` — both bots invited with the scopes and intents the gestures need | high | `e61` · jetson-bot read a message in full live (`run-3946312e1c30bd678bb897bcdd943783`) and both apps registered their guild commands; both bots reacted live (`e47`); Culture.dev read full messages since item 72 |
| `c29` — the new kinds, events and builtins are first-class everywhere a kind lives (Releases 1–3) | medium | Release 1: `e29`–`e32` · `tests/node/test_discord_wiring.py::test_discord_read_is_a_catalogued_action_kind`; Releases 2–3: `e79`, `e56` · `tests/node/test_discord_wiring.py` · the editor's vitest suites, six of whose d12 cases were never shown red (`l13`) |
| `c31` — Discord events expire after 30 days; unselected channels are not stored | high | `e15`–`e17` · `tests/events/test_hook_sink.py::test_a_discord_event_carries_an_expiry_date_30_days_on` |
| `c32` — the per-actor event id changes only Discord's ids | high | `e3` · `tests/apps/test_discord_gateway.py::test_one_actors_redelivery_of_a_message_is_a_duplicate` |
| `c36` — the live library actor and key accept what Discord flows push | high | `e37` · library key 10 and actor `newspaper-library` (kinds include `discord-message`; formats `epub`, `md`, `pdf`, read back 2026-10-26 — `e37`'s text names only md), live item 72 |
| `c38` — REST reads honour 429 and are bounded; long messages are saved whole | high | `e25`–`e28` · `tests/apps/test_discord_rest.py::test_get_retries_429_after_retry_after_then_succeeds` |
| `c39` — a saved item is not removed when its message is edited or deleted | unverified | `o18` unchecked: no test asserts it; it holds today only because the gateway listens for new messages alone |
| `c42` — a save flow carries a who filter (users by id or name, roles, either one admits) | high | `e10`–`e12` · `tests/engine/test_discord_trigger_filters.py::test_users_filter_ids_or_names_case_insensitive` |
| `c13` — a reaction saves a message | high | `e70`, `e63` · `tests/rules/test_discord_reaction_flow.py` · live pdf item 92 saved by 📚 on both bots |
| `c33` — reactions arrive through the raw event | medium | `e71` · `tests/apps/test_discord_gateway.py`; never shown red (`l6`), and no live reaction on an uncached message was observed |
| `c18` — pdf, epub and Markdown attachments each become one item | medium | `e72`, `e64` · `tests/node/test_attachment_fetch.py` · live: pdf created item 92 and Markdown item 85; the epub matched item 67, already in the library (`created: false`), so the epub path never created a new item live; the claim's text predates `d7`/`d10` (files on the node, Markdown), recorded by `b7`/`b9`; shipped unable to fetch real attachments (`l11`, fixed in #104) and needs its steps placed on one node (`l14`) |
| `c37` — the fetch reuses http.call's vetted connection | high | `e73`, `e44` · `tests/node/test_attachment_fetch.py` · `tests/node/test_http_call.py::test_real_transport_names_culture_rules_as_its_user_agent` |
| `c43` — acknowledgement by reaction or private reply | high | `e74`, `e75`, `e47` · `tests/node/test_discord_react.py` · `tests/node/test_discord_reply.py` · live ⏳⌛✅ and replies |
| `c14` — slash and message commands per bot, answered in Discord's window | high | `e76`, `e49` · `tests/apps/test_discord_commands.py` · live, all four bot/command pairs; the tree tests were skipped until #105 (`l12`), the live runs carry it |
| `c34` — commands sync per guild only on change, only where allowed | high | `e50`, `e51` · `tests/apps/test_discord_commands.py::test_the_holder_registers_per_guild_and_reconnects_do_not_resync` · Discord's command listing |
| `c16` — a scheduled sweep saves a channel's new messages | high | `e77`, `e78`, `e65` · `tests/node/test_discord_sweep.py` · `tests/rules/test_discord_command_and_sweep_flows.py` · live items 87–89 |
| `c25` — the success signal on the live mesh | high | `e66` · 26 runs in 1–13 s, sweeps within their period, the pdf one new item and the epub one item (67, already held), re-saves `created: false`, no run from a bot's post |
| `c24` — the after-state: two actors, four gestures, Markdown plus files | high | `e80` (supersedes `e67`) · all four gestures live on both bots in JetsonBot Experiments, the operator's private server; jetson-bot also listens in the public server with no save flow there (`d9`) |
| `c41` — acknowledgement is per-flow configuration | high | `e59` · ✅/⛔ and ⏳⌛✅ set live without code |
| `c7` — culture-library unchanged | medium | `e68` · one culture-library commit in the window, `bb981c0` (0.17.1, status docs and eval generator), none for Discord; Release 1 needed the `discord-message` kind (`d6`, culture-library#19) |
| `c8` — discord-bot-cli and jetson-ai-lab-cli unchanged and not in conflict | low | `e69` (**fail**: half checked) · discord-bot-cli `6cd4970` `tests/test_live_discord.py`: 6 passed while jetson-bot's gateway was held, no disconnect, no rule run; jetson-ai-lab-cli has no live suite and was not run; neither CLI changed |

Lapse ledger evidence (all approved):

| Lapse | Code | What |
|-------|------|------|
| `l1` | `control-absent` | t5 task agent wrote `markdown_render` tests and module together and never ran the tests red first, so the tests were never shown to fail without the code |
| `l2` | `provenance-missing` | t2 task agent handed back the literal text 'placeholder' instead of a report: no test command, pass summary or shortcut list was reported for commit 9d23356 |
| `l3` | `control-absent` | main agent merged t10 (a new committed docs/rules workflow) on tests/rules alone and deferred the full suite to after t4; 12 corpus tests (paragraph fixtures, fold parity, workflow count guards) failed and were caught only at the wave-3 full run |
| `l4` | `grader-unverified` | main agent ran 'grant set `LIBRARY_PUSH_API_KEY` --hidden < file' without the '-' value, which grant treats as a metadata-only update, so key 6 stayed sealed and key 7's only copy was shredded; the follow-up check fetched a public item, which any valid key passes, so it could not tell key 6 from key 7. Found only when the push failed `forbidden_kind` and the sealed token's hash matched key 6 in /data/library.db |
| `l5` | `grader-unverified` | evidence `e33` (`o13`, `c27`) says its test asserts a push with no visibility sends none, but `test_without_visibilities_a_push_without_visibility_still_works` only checks the push completes; the absence is asserted by `test_upload_carries_file_title_author_and_kind` |
| `l6` | `control-absent` | t12 task agent never ran the two discord-library gateway tests (reactions intent, uncached raw payload) red: they were skipped on the first run because discord.py was not installed, and only ran after the implementation |
| `l7` | `control-absent` | d7 library.push agent wrote the `action_kinds` `any_of` test in the same step as the catalogue change, so tests/model/`test_action_kinds.py`'s new test was never shown red |
| `l8` | `control-absent` | main agent edited the `discord_react.py` docstring after its last flake8 run and opened PR #102 on a gate (full suite, bandit, teken, scan-secrets, markdownlint) that left flake8 out; the 151-character line failed CI's lint job |
| `l9` | `control-absent` | main agent opened PR #102 without running the Playwright e2e suite after rewording the trigger picker's roles hint in t15; e2e/discord-flow.spec.ts still asserted the old text and CI's web job failed. Also the pre-PR gate never checked SonarCloud's issue list, and CI's test job gates on zero unresolved issues (23 test-code smells, mostly composite assertions) |
| `l10` | `grader-unverified` | t17 task agent reported scripts/scan-secrets.py clean, but tests/node/`test_discord_reply.py`:19 (SECRET = a literal) was flagged; found by the main agent's full gate before the Release 3 PR |
| `l11` | `assumption-for-measurement` | attachment.fetch shipped in 0.31.0 and 0.32.0 tested only against a fake opener; nobody fetched a real Discord CDN URL before the release claimed pdf/epub/md saving, and the CDN answers 403 to Python's default User-Agent, so every live attachment was skipped as `http_status` (found on 0.32.0 when the operator reacted to a Markdown upload, run run-bf2d0566ce592105e46d6db670ced7d0) |
| `l12` | `control-absent` | the discord.py command-tree tests (`build_command_tree`, `sync_commands`, the /save and Save to library callbacks) skip without discord.py, and the dev group never listed it, so they never ran locally or in CI since Release 3 (0.32.0); found while adding the withdrawal test for PR #105 |
| `l13` | `control-absent` | d12 editor task agent: six of its new vitest cases (no commands written for a non-Discord surface, the stored example accepted, no description asked of a message command, left-over options ignored on a message command, commands unchecked on another surface, no command field on message/reaction triggers) passed before any implementation existed: negative guards never seen red |
| `l14` | `assumption-for-measurement` | Release 2's attachment flow (d7: files on the node) assumed every step runs on the node holding the attachments root: the docs and the live reaction flow left fetch and the file pushes unplaced, and no test runs a flow across two nodes. Live on 2026-10-27, save-on-reaction-jetson's fetch ran on spark2 and failed `path_refused` (run-001693e4b4f2824843d132cea01ffa0b); fixed by placing fetch, files and the push on spark in the four live flows |
| `l15` | `assumption-for-measurement` | t20 live configuration gave every Discord rule (message, reaction, command, for both bots) the same concurrency key template discord:{`message_id`}, copied from the committed bundle, without checking that keys are global and coalesce: Culture.dev's message event for the epub post (1564565129729941696) was deduplicated behind jetson-bot's run, then displaced by the 📚 events, so its message flow never ran; fixed by a per-flow, per-bot key prefix |

How the lapses bear on the claims: `l1` caps `c26` at medium, because its render tests were
never shown red; the #100 bundle tests (red on the old bundle) and live item 72 back the
dedupe end to end. `c6` stays high on those bundle tests. `l2` (`c42`) is covered by the
validate-delivery re-run of the filter tests. `l3` was caught by the full suite before merge.
`l5` caps `c27` at medium. `l4` is
closed: key 10 was hash-verified against grant, the node env and `api_keys` row 10 (`b2`).

Releases 2–3: `l6` caps `c33` at medium (its gateway tests were never shown red, and no live
reaction on an uncached message was seen). `l11` and `l14` cap `c18` at medium: the fetch
shipped untested against the real CDN, and it works only when its steps are placed on the
node holding the files; both are fixed and the live items back the claim. `l12` left the
command-tree tests skipped until #105; `c14` and `c34` stay high on the live runs of all four
commands and Discord's own command listing. `l15` was a live configuration error (a shared
concurrency key) with no claim of its own; it cost one message run in t20 and is fixed in the
live rules and the template. `l8`–`l10` are process lapses caught by the gate or CI before merge.
`l7` and `l13` are tests never shown red that nothing caught: `l7`'s any-of test backs no
claim on its own, and `l13`'s editor cases cap `c29` at medium, as `l1` and `l6` cap theirs.

## Remaining Work / Follow-up

- `c8` — jetson-ai-lab-cli has no live suite; checking it while jetson-bot's gateway is held
  needs a read of the public server. Asked in jetson-ai-lab-cli#26 (its Discord access is
  REST-only, `Intents.none()`, so no conflict is expected); `e69` stays a fail until it reports.
- `c39` — add a test that an edit or delete event leaves the item (or keep it documented as
  manual archive only).
- `d11` — jetson-bot's commands stay in Experiments until the public server's admins agree;
  then add the public server to its `command_guild_id`.
- `t11` — `#bot-news` denies jetson-bot Send Messages; the newspaper posts in
  `#bot-experimentations` until a server admin grants it.
- A step that writes files and the steps that read them must share a node; nothing warns when
  `attachment.fetch` is unplaced (`l14`). A save-time warning, or the builtin pinning itself,
  would make it safe by default.
- A dry-run `workflows create` does not validate the body on the server: a string `version`
  passed the dry run and was refused on `--apply` (422). Likewise `actors update` (Release 1).
- Concurrency keys are global and coalesce (`l15`); the editor could warn when two enabled
  rules' key templates can resolve to the same string.
- Three mongo race tests in `tests/node/test_concurrency_completion_race.py` time out on server
  selection when two full suites run at once; they pass alone and in CI.
- A CLI verb for a node's grant injections, and a node restart verb, so every rollout step is a
  `culture-rules` verb.
- A library item's title is the Discord message id; a readable title needs a step setting.
- `workflows migrate-notes` moves any description whose notes are empty, so it would blank
  plain-language descriptions written after the phrases change; make it tell old text from new,
  or retire it. Every rollout from 0.30.1 to 0.35.0 skipped it.
