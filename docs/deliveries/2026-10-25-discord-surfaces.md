# Delivery Summary — discord surfaces

plan: `discord-surfaces` · run: `partial` · date: `2026-10-25`
baseline: `devague summary skeleton`

## Intent

> culture-rules listens and acts on Discord through two bots across two servers, and a
> Discord message can land in the library as an item — the first of the Discord automations

After: Two Discord app actors (discord-bot = Culture.dev, jetson-bot) listen across both
servers; a message saves to the library by a reaction, by a slash or message command, by being
posted in a chosen channel (or any channel), or by a scheduled sweep; its text and links land
as a Markdown item and its pdf/epub attachments as their own items

The plan has three releases. The operator approved gate 2 for **Release 1 only** (t1–t11);
Releases 2–3 (t12–t19) and the all-gesture validation t20 come back for a fresh go/no-go. This
run is therefore `partial` by design: Release 1 shipped in three PRs (#96 0.29.0, #98
0.30.0, #100 0.30.1) and has run live on every node and the API since the 0.30.1 rollout of
2026-10-26 (`rollout verify` ok).

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
| `t11` | partial | 0.30.1 runs on all four nodes and the API; the jetson-bot actor exists; library key 10 and the library actor accept `discord-message`; a `#spark-tests` message saved as private Markdown item 72, and saving it again gave `created: false`. **Missing:** Culture.dev is not yet set up for the operator's private server (no server id yet), and jetson-bot's MESSAGE CONTENT intent is unverified. jetson-bot is post-only (`events: []`), so health shows one gateway lease, not two (`d2`, `d4`–`d6`) |
| `t12` | blocked | Release 2: awaits the operator's go/no-go |
| `t13` | blocked | Release 2: awaits the operator's go/no-go |
| `t14` | blocked | Release 2: awaits the operator's go/no-go |
| `t15` | blocked | Release 2: awaits the operator's go/no-go |
| `t16` | blocked | Release 3: awaits the operator's go/no-go |
| `t17` | blocked | Release 3: awaits the operator's go/no-go |
| `t18` | blocked | Release 3: awaits the operator's go/no-go |
| `t19` | blocked | Release 3: awaits the operator's go/no-go |
| `t20` | partial | only the Release 1 gesture is validated live (a channel message on discord-bot → item 72, and its dedupe); jetson-bot's live check is posting the newspaper (`b1`), not saving. Reactions, commands, the schedule sweep and attachments wait for Releases 2–3 |

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
| `t11` | Culture.dev is not configured for the operator's private server (server id not supplied); jetson-bot's MESSAGE CONTENT intent is unverified; the newspaper posts in `#bot-experimentations` because `#bot-news` denies Send Messages | needs-follow-up |
| `t12`–`t19` | not started: gate 2 approved Release 1 only | needs-follow-up |
| `t20` | only the Release 1 slice is validated live; the other gestures depend on `t12`–`t19` | needs-follow-up |

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

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `c3` — a second Discord app actor (jetson-bot) works with no engine change | high | `e36` · live run `run-2c77aee79f2c472da6493d372d69876d` |
| `c4` — each bot records its own event for the same message | high | `e1`–`e2` · `tests/apps/test_discord_gateway.py::test_two_actors_sinking_the_same_message_record_two_events` |
| `c5` — another bot's messages are skipped unless a rule opts in (`include_bots`) | high | `e4`–`e5` · `tests/engine/test_discord_trigger_filters.py::test_bot_blocked_unless_include_bots` |
| `c6` — a Discord message can become a library item | high | `e19`–`e24` · `tests/rules/test_discord_library_bundle.py::test_a_message_becomes_one_private_discord_message_item` · live item 72 |
| `c11` — tests cover two Discord actors together | high | `e18` · `tests/node/test_discord_stage.py::test_two_discord_actors_two_leases_two_events_and_no_cross_bot_firing` |
| `c12` — library automations are configured live; only a disabled example bundle is committed | high | `e35` · `tests/rules/test_discord_library_bundle.py::test_it_imports_disabled_with_the_placeholder_channel` |
| `c17` — a flow chooses its channels (a list or "all") | high | `e6`–`e9` · `tests/engine/test_discord_trigger_filters.py::test_channels_filter` |
| `c19` — one actor per bot; `guild_id` is one id or a list | high | `e13`–`e14` · `tests/apps/test_discord_gateway.py::test_a_guild_id_list_takes_messages_from_each_listed_guild` |
| `c26` — saving is idempotent (one item per message) | high | `e19`–`e24` · `tests/node/test_markdown_render.py::test_same_inputs_are_byte_identical` · live item 72 `created: false` |
| `c27` — Discord content lands private by default | high | `e33`–`e34` · `tests/node/test_library_push.py::test_without_visibilities_a_push_without_visibility_still_works` |
| `c28` — both bots invited with the scopes and intents the gestures need | low | `o17` unchecked: Culture.dev reads full messages live (item 72); jetson-bot's MESSAGE CONTENT intent is unverified, and the command and reaction scopes belong to Releases 2–3 |
| `c29` — the new kind and builtin are first-class everywhere a kind lives | high | `e29`–`e32` · `tests/node/test_discord_wiring.py::test_discord_read_is_a_catalogued_action_kind` |
| `c31` — Discord events expire after 30 days; unselected channels are not stored | high | `e15`–`e17` · `tests/events/test_hook_sink.py::test_a_discord_event_carries_an_expiry_date_30_days_on` |
| `c32` — the per-actor event id changes only Discord's ids | high | `e3` · `tests/apps/test_discord_gateway.py::test_one_actors_redelivery_of_a_message_is_a_duplicate` |
| `c36` — the live library actor and key accept what Discord flows push | high | `e37` · library key 10 and actor `newspaper-library` (kind `discord-message`), live item 72 |
| `c38` — REST reads honour 429 and are bounded; long messages are saved whole | high | `e25`–`e28` · `tests/apps/test_discord_rest.py::test_get_retries_429_after_retry_after_then_succeeds` |
| `c39` — a saved item is not removed when its message is edited or deleted | unverified | `o18` unchecked: no test asserts it; it holds today only because the gateway listens for new messages alone |
| `c42` — a save flow carries a who filter (users by id or name, roles, either one admits) | high | `e10`–`e12` · `tests/engine/test_discord_trigger_filters.py::test_users_filter_ids_or_names_case_insensitive` |
| `c13` — reactions save a message | unverified | Release 2, not built (`t12`–`t15`) |

Lapse ledger evidence (all approved):

| Lapse | Code | What |
|-------|------|------|
| `l1` | `control-absent` | t5 task agent wrote `markdown_render` tests and module together and never ran the tests red first, so the tests were never shown to fail without the code |
| `l2` | `provenance-missing` | t2 task agent handed back the literal text 'placeholder' instead of a report: no test command, pass summary or shortcut list was reported for commit 9d23356 |
| `l3` | `control-absent` | main agent merged t10 (a new committed docs/rules workflow) on tests/rules alone and deferred the full suite to after t4; 12 corpus tests (paragraph fixtures, fold parity, workflow count guards) failed and were caught only at the wave-3 full run |
| `l4` | `grader-unverified` | main agent ran 'grant set `LIBRARY_PUSH_API_KEY` --hidden < file' without the '-' value, which grant treats as a metadata-only update, so key 6 stayed sealed and key 7's only copy was shredded; the follow-up check fetched a public item, which any valid key passes, so it could not tell key 6 from key 7. Found only when the push failed `forbidden_kind` and the sealed token's hash matched key 6 in /data/library.db |

How the lapses bear on the claims: `l1` (`c6`, `c26`) and `l2` (`c42`) leave those claims at
high, because the tests were later run red on the old code (the #100 bundle test) or re-run
whole in the validate-delivery run. `l3` was caught by the full suite before merge. `l4` is
closed: key 10 was hash-verified against grant, the node env and `api_keys` row 10 (`b2`).

## Remaining Work / Follow-up

- `t11` — Culture.dev in the operator's private server: needs the server id, then the
  `culture-guilds` step (`guild_id` as a list; every node is on 0.30.1, so the list is safe).
- `t11` / `c28` — turn on and confirm jetson-bot's MESSAGE CONTENT intent before it listens
  anywhere.
- `t11` — `#bot-news` denies jetson-bot Send Messages; the newspaper posts in
  `#bot-experimentations` until a server admin grants it.
- `c39` — add a test that an edit or delete event leaves the item (or keep it documented as
  manual archive only).
- `t12`–`t19`, `t20` — Releases 2–3 need a fresh go/no-go from the operator.
- A CLI verb for a node's grant injections (jetson-bot's token was added by editing a systemd
  drop-in), and a node restart verb, so every rollout step is a `culture-rules` verb.
- The `actors update` dry run accepted a body without `id`, which `--apply` then refused (422).
- A library item's title is the Discord message id; a readable title needs a step setting.
- `workflows migrate-notes` moves any description whose notes are empty, so it would blank
  plain-language descriptions written after the phrases change; make it tell old text from new,
  or retire it.
- The `pyproject.toml` description still mentions a Rules tab, which the editor no longer has.
