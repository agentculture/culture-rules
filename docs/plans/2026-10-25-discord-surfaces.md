# Build Plan — discord surfaces

slug: `discord-surfaces` · status: `exported` · from frame: `discord-surfaces`

> culture-rules listens and acts on Discord through two bots across two servers, and a Discord message can land in the library as an item — the first of the Discord automations

## Tasks

### t1 — \[R1\] Actor-scoped Discord delivery id

- instruction: Change only `culture_rules`/apps/`discord_gateway.py` `_on_message`: pass `delivery_id`=f"{`actor_id`}:{`message_id`}" to sink. Do not touch events/`hook_sink.py` `event_id_for`. Add a two-actor test with two fake gateways to tests/apps/`test_discord_gateway.py`.
- covers: c4, h3, c32, h26
- acceptance:
  - two Discord actors sinking the same message id record two events, each with source `app://<actor>` and its own data.actor and `self_authored` (tests/apps/`test_discord_gateway.py`)
  - one actor's redelivery of a message still returns duplicate
  - `event_id_for` and server/hooks/github.py are unchanged; GitHub and Jira ids are byte-identical (existing hook tests green)

### t2 — \[R1\] Discord trigger filters in matching: bots, channels, who

- instruction: Work in `culture_rules`/engine/matching.py `trigger_matches` next to the `include_self` check, and document the params in `culture_rules`/model/rule.py trigger validation if params are validated there. Tests in tests/engine/. Do not touch connection.channels (the actor allow-list).
- covers: c5, h4, c17, h13, c42, h33
- acceptance:
  - an event with data.bot true matches no trigger unless params.`include_bots` is true; a human message matches as before
  - params.channels as a list of ids admits only those channels; absent or "all" admits every channel
  - params.users (ids or usernames, case-insensitive) and params.roles (role ids, matched against data.`user_roles`) each admit only their members; both absent admits everyone
  - the filters apply only to discord.\* event types; GitHub/Jira triggers behave exactly as before

### t3 — \[R1\] connection.`guild_id` accepts a list

- instruction: Files: `culture_rules`/apps/`discord_gateway.py` (`_on_message` guild check), `culture_rules`/server/app.py (~1007), `culture_rules`/apps/`discord_rest.py` `list_targets` (accept a set of ids), web/src/actors/app-config.ts (`guild_id` becomes a list field). Normalise with one helper (e.g. in apps/`discord_rest.py`) returning a frozenset or None.
- depends on: t1
- covers: c19, h15
- acceptance:
  - the gateway guild filter, GET /actors/{id}/discord/targets, DiscordClient.`list_targets` and the web Guild field each accept a string (as before) or a list of ids, with tests for both forms
  - unset still means every guild the bot is in

### t4 — \[R1\] Discord event retention and unselected-channel drop

- instruction: TTL: use the store's ensure(..., `ttl_seconds`=0) pattern from events/ingest.py:337 with an `expires_at` set only on Discord events in `hook_sink` (surface == discord); check MemoryStore ignores TTL safely. Selection: GatewaySupervisor computes the union of channels from enabled rules (reuse the store find), refreshed each tick.
- depends on: t2, t3
- covers: c31, h25, c11, h8
- acceptance:
  - Discord events carry a date field with a TTL index (default 30 days, configurable) created by the store's ensure() path; non-Discord events never expire
  - the gateway does not sink a message from a channel that no enabled rule with a discord.\* trigger selects (a rule with no channels or "all" selects every channel)
  - tests cover two Discord actors end to end: two leases, two events for one message, no cross-bot firing

### t5 — \[R1\] Generic markdown.render builtin

- instruction: New module `culture_rules`/node/actions/`markdown_render.py` with a \*`_BUILTIN` constant and a Port like `json_read.py`; stdlib only. Tests in tests/node/. Registration in runner/describe is t8's job.
- covers: c6, h5, c23, h18, c26, h21
- acceptance:
  - markdown.render takes title, text, author, source, url, id and returns markdown and `content_base64`; no Discord-specific input
  - the same inputs render byte-identical output (no timestamps of the render); the id and url appear in the body
  - a workflow test feeds discord.message.created data through markdown.render into library.push against a fake library: the body has the content and url, and a second push returns created false

### t6 — \[R1\] Discord REST reads with 429 handling

- instruction: Only `culture_rules`/apps/`discord_rest.py` and tests/apps/`test_discord_rest.py`. Keep the token out of errors and reprs.
- depends on: t3
- covers: c38, h31
- acceptance:
  - DiscordClient.`get_message`(channel, message) returns content (untruncated, e.g. 3000 chars), author id/name, url and attachments (id, filename, `content_type`, size, url)
  - DiscordClient.history(channel, after, limit) pages oldest-first and is bounded by a page cap
  - every GET (targets included) retries a 429 after `retry_after` up to a bound, then raises a retryable DiscordError; tests with a fake transport

### t7 — \[R1\] discord.read action

- instruction: New module `culture_rules`/node/actions/`discord_read.py` modelled on message.py `_discord` (actor load, surface check, secrets.resolve). Tests in tests/node/.
- depends on: t6
- covers: c13, c38
- acceptance:
  - discord.read with params actor, channel, message returns the message fields and attachments from DiscordClient.`get_message`, resolving the actor's own grant token
  - a non-Discord actor or a channel outside connection.channels fails with a clear, non-retryable error

### t8 — \[R1\] Wire-up: kinds, phrases, runner, explain, OpenAPI

- instruction: Shared registry files only: model/`action_kinds.py`, model/describe.py, model/paragraph.py, node/runner.py, explain/catalog.py, api/openapi.json. Run the full suite once at the end.
- depends on: t2, t5, t7
- covers: c29, h24
- acceptance:
  - discord.read is in model/`action_kinds.py`, `ACTION_WORDS` and the paragraph phrases; markdown.render in `BUILTIN_WORDS`; both registered in node/runner.py
  - trigger params `include_bots`, channels, users, roles are accepted by validation and described in the paragraph
  - api/openapi.json matches the served schema (`test_openapi_contract`), the CLI/MCP parity test passes, and teken cli doctor . --strict is green

### t9 — \[R1\] Editor: Discord trigger filters, guild list, new steps

- instruction: web/src/rules/TriggerPicker.tsx, ActionPicker.tsx, web/src/actors/app-config.ts, api types regenerated from api/openapi.json. Follow the design canvas.
- depends on: t8
- covers: c21, h16
- acceptance:
  - the trigger editor for a discord.\* event offers channels (picked from discord/targets, or All), users, roles and an include-bots switch, and saves them in trigger params
  - the Actors form edits `guild_id` as a list; ActionPicker offers discord.read and markdown.render with their fields
  - vitest and a Playwright flow build a channel-message -> markdown.render -> library.push workflow; webglass gate green

### t10 — \[R1\] Docs and disabled example bundle

- instruction: Docs describe what is on disk; mark R2/R3 gestures planned until they land. Keep CLAUDE.md, QWEN.md, AGENTS.override.md, AGENTS.colleague.md in step when the project description changes.
- depends on: t3
- covers: c28, h23, c39, h32, c12, h9, c27, h22
- acceptance:
  - docs/actors/discord.md covers N bots: invite with bot + applications.commands, permissions incl. Add Reactions, intents (message content, guild messages, reactions), a grant name per bot, `guild_id` as a list, the rollout order (upgrade every node and the API before switching `guild_id` to a list), and that deleting a message leaves its item (archive by hand)
  - docs/actors/library.md notes the kind and pdf format Discord flows need
  - docs/rules/discord-library ships disabled, with placeholder channel ids and no visibility public; markdownlint clean

### t11 — \[R1\] Live rollout: second bot and the channel flow

- instruction: Operator hand-turns through ! steps (deploys need the operator's shell); stop on the first failing step; configure the flow live in the store, never committed.
- depends on: t4, t8, t9, t10
- covers: c3, h2, c36, h29
- acceptance:
  - the operator has sealed jetson-bot's token with grant, turned on its intents, re-invited both bots, and the release runs on every node and the API (culture-rules rollout)
  - the jetson-bot actor exists with `guild_id` as a list of both servers; ops health shows two discord-gateway leases
  - the library actor lists the Discord kind and pdf and the library key's kind list matches; a test channel message saves as a private Markdown item
  - grep of the release diff for 'jetson' in `culture_rules`/ has no hits

### t12 — \[R2\] Raw reaction event discord.reaction.added

- instruction: `culture_rules`/apps/`discord_gateway.py` only (intents, the Gateway protocol gains `on_reaction`, DiscordPyGateway handler, supervisor sink path) and its tests. The reaction event goes through the same channel selection as messages.
- depends on: t11
- covers: c33, h27, c13, h10
- acceptance:
  - the gateway registers `on_raw_reaction_add` with the reactions intent and sinks discord.reaction.added (guild, channel, message id, emoji name and id for custom emoji, user id, username, `user_roles`, bot) with delivery id actor:message:user:emoji
  - a reaction on a message not in the client cache still raises the event (fake raw payload test)
  - message events also carry `user_roles`

### t13 — \[R2\] Generic attachment.fetch through the vetted opener

- instruction: New module `culture_rules`/node/actions/`attachment_fetch.py` reusing the vetted pinned opener from node/actions/http.py (import its helpers, do not copy). Default allowed hosts cdn.discordapp.com and media.discordapp.net.
- depends on: t11
- covers: c18, h14, c37, h30
- acceptance:
  - attachment.fetch takes a list of {url, filename, `content_type`} and an allowed host list, and returns per item `content_base64` and format for pdf and epub, or skipped with a reason
  - a redirect, a non-allow-listed host, a host resolving to a private address and a file over 50 MiB are refused (tests with fakes)
  - images and other types are skipped with reason `unsupported_format`

### t14 — \[R2\] discord.react action

- instruction: Add `add_reaction` to `culture_rules`/apps/`discord_rest.py` and a port in `culture_rules`/node/actions/`discord_react.py`; tests for both.
- depends on: t11
- covers: c43
- acceptance:
  - discord.react with actor, channel, message, emoji adds the bot's reaction via DiscordClient.`add_reaction` (unicode and custom name:id), with 429 handling
  - a workflow step with a when condition reacts for one emoji and stays silent for another

### t15 — \[R2\] Wire-up and editor for reactions and attachments

- instruction: Shared registry and web files as in t8/t9; update docs/actors/discord.md (no longer planned).
- depends on: t12, t13, t14
- covers: c29
- acceptance:
  - discord.reaction.added is offered by the trigger picker with an emoji field (unicode or custom); attachment.fetch and discord.react are catalogued, described, registered and offered in the editor
  - a reaction -> discord.read -> markdown.render + loop(attachment.fetch -> library.push) -> discord.react workflow builds in the editor and passes a workflow test; OpenAPI, parity, teken green

### t16 — \[R3\] Slash and message commands over the gateway

- instruction: `culture_rules`/apps/`discord_gateway.py` (CommandTree on the client, interaction handler) plus a small module for the command definitions; app id from connection.`application_id`. Tests with fakes.
- depends on: t15
- covers: c14, h11, c34, h28
- acceptance:
  - the lease holder registers /save and a Save to library message command per guild, and re-syncs only when the definition hash changes (test: reconnects do not re-sync)
  - an interaction is acknowledged with an ephemeral deferral before the event is written, and discord.command.invoked carries command, options, target message id, user id, username, `user_roles`, channel, guild
  - the interaction token is never written to the event; it is kept in a short-TTL collection keyed by the event id for the reply action

### t17 — \[R3\] Ephemeral command reply action

- instruction: New port `culture_rules`/node/actions/`discord_reply.py` and a follow-up call in apps/`discord_rest.py`; tests with a fake transport.
- depends on: t16
- covers: c43, h34
- acceptance:
  - discord.reply with the triggering event id sends an ephemeral follow-up using the stored interaction token, visible only to the invoking user; an expired token fails non-retryably
  - a flow replies saved or skipped with a reason, only under its when condition

### t18 — \[R3\] Scheduled history sweep with a cursor

- instruction: New module `culture_rules`/node/actions/`discord_sweep.py` using DiscordClient.history; cursor documents in a new store collection with an atomic conditional update. Tests in tests/node/.
- depends on: t15
- covers: c16, h12
- acceptance:
  - discord.sweep reads messages newer than the stored cursor per flow and channel, bounded per run, and returns them for a loop
  - the cursor advances only after the run's pushes succeed, with compare-and-set so an overlapping run cannot move it backwards or skip messages
  - two runs with no new messages save nothing; a run after a failed run saves what it missed

### t19 — \[R3\] Wire-up and editor for commands, reply and sweep

- instruction: Shared registry and web files as in t8/t9.
- depends on: t17, t18
- covers: c29
- acceptance:
  - discord.command.invoked, discord.reply and discord.sweep are catalogued, described, registered and offered in the editor; OpenAPI, parity, teken green; docs/actors/discord.md no longer marks them planned

### t20 — \[R\*\] Live validation of all gestures on both bots

- instruction: Run after each release for its gestures, and fully after R3; file results with validate-delivery.
- depends on: t19
- covers: c1, h1, c24, h19, c25, h20, c22, h17, c7, h6, c8, h7
- acceptance:
  - in a private test channel on each bot: reaction, /save and the message command, a channel message and one schedule tick each save within 60 s (schedule within one period); item ids recorded as evidence
  - a pdf and an epub attachment each become one item; saving one message twice gives one item; zero rule runs fire from one bot's posts on the other's rules
  - no commit in ../culture-library, ../discord-bot-cli or ../jetson-ai-lab-cli for this work, and discord-bot-cli's live test passes while jetson-bot's gateway is held

## Risks

- [unknown_nonblocking] Two runs of one schedule flow could overlap if a sweep outlasts its period (frame v4); t18's compare-and-set cursor must hold under overlap (task t18)
- [unknown_nonblocking] Rolling upgrade: an older node or API reading a list `guild_id` matches nothing (c35); switch jetson-bot's `guild_id` to a list only after every node and the API run R1 (task t11)
- [follow_up] Registry files (`action_kinds`, describe, paragraph, runner, openapi, web pickers) are shared: only the per-release wire-up tasks t8, t15, t19 touch them, so module tasks stay file-disjoint
- [follow_up] No alert when a Discord gateway stays disconnected (frame v3)
