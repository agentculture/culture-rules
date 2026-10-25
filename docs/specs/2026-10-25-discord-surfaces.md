# discord surfaces

> culture-rules listens and acts on Discord through two bots across two servers, and a Discord message can land in the library as an item — the first of the Discord automations
> instruction: culture-rules ops health shows two discord-gateway leases; a test post in each server yields an item

## Audience

- The operator, who builds Discord automations in the culture-rules editor (Workflows tab, Actors tab) and the CLI/MCP, and the two bots' members in JetsonBot experiments and Jetson AI Lab Research Group whose saved messages land in culture-library
  - instruction: editor e2e (Playwright) builds a reaction flow; CLI builds the same with --apply

## Before → After

- Before: Today one bot (Culture.dev, actor discord-bot on spark) listens in one server and can only react to new messages with a text post; nothing in Discord can reach the library, and jetson-bot is reached only by one-shot REST CLIs
  - instruction: culture-rules actors list on the live API (read on 2026-10-25)
- After: Two Discord app actors (discord-bot = Culture.dev, jetson-bot) listen across both servers; a message saves to the library by a reaction, by a slash or message command, by being posted in a chosen channel (or any channel), or by a scheduled sweep; its text and links land as a Markdown item and its pdf/epub attachments as their own items
  - instruction: live validation task records the item ids per gesture and bot

## Why it matters

- Discord is where links, papers and notes are shared in both communities; saving them by hand loses them, and Discord is the first of several surfaces whose content should flow into the library (later other destinations) through generic, reusable steps
  - instruction: review the step params: render takes text/author/source/url, fetch takes urls + allowed hosts

## Requirements

- A second Discord app actor (jetson-bot) works with no engine change: GatewaySupervisor keys sessions by actor id with lease `discord-gateway:<actor id>`, each actor resolves its own `grant:<NAME>` bot token, discord.message resolves params.actor, and /actors/{id}/discord/targets and ops/health gateways() are per actor; the work is a new grant secret, its injection on the listening node and API, the actor, and docs
  - instruction: grep the diff of release 1 for 'jetson' in `culture_rules`/ -> no hits outside docs and examples
  - honesty: jetson-bot listens and posts after only a grant secret, its injection and an actor document; the gateway, message action and targets code carry no actor-specific branch
- Each bot records its own event for the same message: `event_id_for`(surface, `delivery_id`) in events/`hook_sink.py`:99 hashes only the message id, so when both bots sit in one server the second sink is a duplicate and the event is owned by whichever bot wrote first (source, data.actor and `self_authored` become order-dependent); the event id must include the actor id
  - instruction: unit test in tests/apps/`test_discord_gateway.py` with two fake gateways; `event_id_for` takes the actor id
  - honesty: Two Discord actors receiving the same message id record two events, each with its own source `app://<actor id>`, data.actor and `self_authored`; one actor's redelivery still dedupes
- Bot-to-bot loops are prevented: the gateway records data.bot but never filters it, and `self_authored` only matches the actor's own `self_identity`, so Culture.dev's posts fire jetson-bot rules and vice versa; another bot's messages are skipped by default unless a rule opts in (like `include_self`)
  - instruction: engine matching test: bot-authored event skipped by default, fires with the opt-in param
  - honesty: With the guard on, a message authored by any bot (data.bot true) fires no rule unless the rule opts in; a human message fires as before
- A Discord message can become a library item: library.push takes only `content_base64` (`action_kinds.py` library.push) and no builtin turns trigger text into base64, so a step composes Markdown from the message (content, `author_name`, channel, url, message id) and outputs `content_base64` for library.push with format md
  - instruction: workflow test: discord.message.created -> render step -> library.push against a fake library; body contains content and url
  - honesty: A trigger message's content, author, channel and url become a Markdown file whose `content_base64` library.push accepts with format md, with no file on disk
- Tests cover two Discord actors: two sessions and two leases, both bots seeing one message (two events), and one bot not firing on the other's posts; today every gateway test uses a single `discord_actor`()
  - instruction: uv run pytest tests/apps/`test_discord_gateway.py` -k two -v lists the multi-actor tests and they pass
  - honesty: The suite has tests for two Discord actors: two leases, two events for one message, no cross-bot firing
- The library automations themselves (which channel, which kind and visibility) are configured live in the store, not committed; only the generic step and, at most, a disabled example bundle are committed
  - instruction: grep docs/rules for discord bundles: enabled false; channel ids are placeholders
  - honesty: No committed file names a live channel id for a library flow other than the docs' 'On rules.culture.dev' table; any bundle ships disabled
- A reaction saves a message: the gateway raises discord.reaction.added (guild, channel, message id, emoji name/id incl. custom emoji, reacting user, bot flag) with the reactions intent, and a rule filters on the emoji; the workflow fetches the reacted message (text, author, url, attachments) over REST since the reaction event carries no content
  - instruction: gateway fake test for the reaction event; live: react on a test message, an item appears
  - honesty: Adding the chosen emoji (unicode or custom) to a message raises one discord.reaction.added event and the flow saves that message's text, url and attachments
- A slash command (e.g. /save) is registered per bot and handled; the interaction is answered within Discord's 3-second window and becomes an event (discord.command.invoked: command, options, user, channel, guild, target message when it is a message command) that rules trigger on
  - instruction: live: invoke both commands in the test channel; the runs list shows the rule run
  - honesty: A /save slash command and a 'Save to library' message command appear in both servers for each bot, the user gets an ephemeral acknowledgement within 3 s, and a discord.command.invoked event fires a rule
- A time-based flow saves on a schedule: a schedule trigger runs a step that reads a channel's messages since the last run over REST (GET /channels/{id}/messages?after=), with a stored cursor per flow and channel so a rerun neither misses nor repeats a message
  - instruction: unit test with a fake REST history: cursor advances only after a successful push
  - honesty: A schedule flow reads only messages newer than its stored cursor per channel; two consecutive runs with no new messages save nothing, and a run after a failure saves what the failed run missed
- A flow chooses its channels: opt-in channels or 'all' the bot can see, per flow (a trigger/step setting picked from /actors/{id}/discord/targets), independent of the actor-level connection.channels allow-list, which also limits posting
  - instruction: test the trigger/step channel filter both ways and that connection.channels is untouched
  - honesty: A flow set to chosen channels ignores other channels; set to 'all' it takes every channel the bot can see; neither changes where the actor may post
- Attachments reach the library: the message events and the REST read carry attachments (id, filename, `content_type`, size, url); a step downloads pdf/epub only from Discord's CDN (urls expire about 24 h), at most 50 MiB, and hands `content_base64` and format to library.push; other types are skipped with a reason
  - instruction: fetch step tests with fake CDN responses: pdf, epub, png, oversize, non-CDN url
  - honesty: A pdf and an epub attached to a saved message become one library item each with the right format; an image or a file over 50 MiB is skipped with a reason in the run; only Discord CDN hosts are fetched
- One actor per bot, not per server: jetson-bot is one actor spanning both servers, because two actors on one token would open two gateway sessions that each receive every message; connection.`guild_id` becomes a list (or stays unset for every server the bot is in) in the gateway filter, server/app.py:1007, `discord_rest`.`list_targets` and web app-config.ts:86
  - instruction: tests for list and string `guild_id` in each of the four places; live: one lease for jetson-bot
  - honesty: connection.`guild_id` accepts a list of ids (and a single string as before) in the gateway filter, the targets endpoint, `list_targets` and the web form; one jetson-bot actor serves both servers on one gateway session
- Saving is idempotent: the rendered Markdown is deterministic from the message (message id and url in the body, no save time), so the same message saved by two gestures, two users or a rerun produces one item (library sha256 dedupe, created: false); attachments dedupe by their bytes
  - instruction: fake-library test plus the live double-save check
  - honesty: Saving one message twice gives one library item
- Discord content lands private by default: when a library.push step names no visibility none is sent and culture-library stores the item private (library.py sends visibility only when given; library core defaults private); no Discord flow, example bundle or editor default sets public, since the Jetson AI Lab server is community content
  - instruction: grep the diff and bundles for visibility public on Discord flows -> none
  - honesty: No Discord flow, bundle or editor default sends visibility public
- Both bots are re-invited with the scopes the gestures need: bot plus applications.commands, and permissions View Channels, Send Messages, Read Message History, Add Reactions (for an acknowledgement reaction); intents message content (privileged), guild messages, guild message reactions; docs/actors/discord.md documents this for N bots
  - instruction: markdownlint passes and the operator follows the doc to re-invite both bots
  - honesty: docs/actors/discord.md documents the scopes, permissions and intents for both bots and the live table names both actors
- The new kinds and events are first-class everywhere a kind lives: model/`action_kinds.py`, describe phrases (model/paragraph.py), the editor's trigger and action pickers with the channel picker, api/openapi.json if the API changes, and an explain/learn entry for any new CLI verb, with the CLI/MCP parity test green
  - instruction: uv run pytest -n auto; uv run teken cli doctor . --strict
  - honesty: openapi contract test, CLI/MCP parity test and teken doctor --strict pass, and each new kind has a describe phrase
- Discord message content stored in the events collection expires: today every gateway message is sunk as a plaintext event with no TTL (only handoffs and quarantine carry TTL indexes), and jetson-bot would add every message of a ~120-channel community server; Discord events get a retention (TTL on a date field, e.g. 30 days) and channels outside every flow's selection are not sunk at all
  - instruction: ensure() a TTL index on Discord events; test that an unselected channel produces no event
  - honesty: A Discord event older than the retention is gone from the events collection, and a message in a channel no enabled flow selects is never written
- The per-actor event id changes only Discord's ids: github.py:363 checks redelivery with `event_id_for`(SURFACE, delivery), so the gateway passes an actor-scoped delivery id (e.g. `<actor>:<message id>`) instead of changing `event_id_for`; old Discord events keep their ids, and at most a message redelivered across the upgrade lands twice
  - instruction: grep `event_id_for` callers unchanged; github redelivery test still green
  - honesty: GitHub and Jira event ids are byte-identical before and after; only new Discord events carry the actor in their id
- Reactions are received with discord.py's raw reaction event (`on_raw_reaction_add`), not `on_reaction_add`, which fires only for messages in the client cache; a reaction on an old message still saves it
  - instruction: fake-gateway test with a raw reaction payload for an uncached message id
  - honesty: Reacting to a message posted before the node connected still raises discord.reaction.added
- Commands are synced by the lease holder only when their definition changes (per guild, so they appear at once, not after global propagation), and an interaction is acknowledged (ephemeral deferral) before the event is written; during a lease handover gap Discord shows the interaction failed, and the user retries
  - instruction: test: sync called once for an unchanged definition across reconnects; the ack happens before sink
  - honesty: Reconnecting does not re-register unchanged commands, and every handled interaction is acknowledged within 3 s
- The live library actor and key accept what Discord flows push: newspaper-library's kinds are newspaper, transcript, todo and formats epub, md, so the kind for Discord items (document or article) and the format pdf are added to the actor and to the culture-library key's kind list (config, at rollout)
  - instruction: culture-rules actors get newspaper-library shows the added kind and pdf; a pdf push returns 201
  - honesty: A Discord text item and a pdf attachment push succeed against library.culture.dev with the live actor and key
- The attachment fetch reuses http.call's vetted connection (exact-host allowlist of Discord's CDN hosts, every resolved address vetted, pinned connect, redirects refused, no proxies) rather than a new HTTP path
  - instruction: fetch step tests: redirect refused, private-range resolution refused, non-CDN host refused
  - honesty: The fetch step refuses a redirect, a non-allow-listed host and a CDN name resolving to a private address
- REST reads (message fetch, history sweep, targets) honour Discord 429 `retry_after` and are bounded per run (pages and channels), and the saved text comes from the REST message, so a message longer than the event's 2000-character cut (Nitro allows 4000) is saved whole
  - instruction: fake REST test: a 429 then success; a 3000-char message saved whole
  - honesty: A sweep over many channels survives a 429 without losing its cursor, and a 3000-character message is saved untruncated
- A save flow carries a who filter: unset means anyone; users lists Discord user ids or usernames; roles lists role ids, and a role the server lets members self-assign is the opt-in; reaction and command events carry the acting user's id, username and role ids so the condition can check them. Discord does not expose members' emails to bots, so users are matched by id or username, not email
  - instruction: condition tests: unset passes everyone; a users list and a roles list each admit only their members; events carry `user_roles`
  - honesty: A member outside the configured users and roles cannot trigger a save, and with no filter every member can
- Two small outbound Discord actions make acknowledgement configurable: discord.react (add an emoji to a message, mass-mention free) and an ephemeral reply to a command interaction; a flow uses them as ordinary steps with a when condition (e.g. only for opt-in users or one emoji)
  - instruction: action tests against a fake REST client; describe phrases for both
  - honesty: A flow can ack with ✅ for one emoji and stay silent for another, and a command user sees an ephemeral saved/skipped reply only they can see

## Honesty conditions

- Both actors hold a gateway lease on the live mesh at once and a message in each server saves to the library
- No file in ../culture-library changes in this work
- No file in ../discord-bot-cli or ../jetson-ai-lab-cli changes, and their live tests still pass while culture-rules holds jetson-bot's gateway
- Every flow in the spec is buildable from the editor and from the CLI/MCP
- The before state matches the live mesh on 2026-10-25: one discord app actor (discord-bot, guild 1413999242648748195)
- The Markdown render and attachment fetch steps take no Discord-specific input beyond a message mapping, so another surface can reuse them
- All four gestures save a test message on the live mesh, from both bots
- The live validation measures each number in the success signal and records it as evidence
- Deleting a saved Discord message leaves its library item in place, and the doc says how to archive it

## Success signals

- On the live mesh, each of the 4 gestures (reaction, command, channel message, schedule) saves a test message to the library within 60 s of the gesture (the schedule within one period), a pdf and an epub attachment each become 1 item, saving the same message twice yields 1 item (created: false), and 0 events fire from one bot's posts on the other's rules
  - instruction: validate-delivery files evidence with the timings, item counts and the cross-bot run count

## Scope / boundaries

- culture-library is unchanged in the first slice: a .md upload with an existing kind (document or article), title and author from the message, and the message URL and id in the body (which also avoids sha256 dedupe of identical text) already works; there is no link/bookmark/note kind, no source or tag metadata, no server-side URL fetch, and images are refused (415)
  - instruction: git -C ../culture-library log over the delivery window shows no commit for this frame
- discord-bot-cli and jetson-ai-lab-cli stay unchanged and do not conflict: both use the jetson-bot token over REST with Intents.none() and a login-act-close run, so culture-rules holding jetson-bot's gateway session duplicates nothing
  - instruction: run discord-bot-cli's live test once after jetson-bot's actor is enabled
- A saved item is not removed when its Discord message is edited or deleted (c10 excludes those events); removal is a manual archive in culture-library
  - instruction: docs/actors/discord.md states it

## Non-goals

- aws-agentcore-chat-bot (Mike Science Bot, its own gateway session on EC2) is not one of the two bots and is not integrated
- Out of scope for this work: message edit/delete events, threads, embeds, and file upload from discord.message; in scope (operator 2026-10-25): reactions, slash and message commands, the schedule, channel messages, attachments, and the acknowledgement actions discord.react and the ephemeral command reply
- No rule posts into the Jetson AI Lab Research Group server in this work; culture-rules supports posting there (multi-guild targets and discord.message per actor), and what, when and how is decided later (operator 2026-10-25)

## Assumptions

- The two bots are Culture.dev (app 1556270251979571320, already the discord-bot actor on spark) and jetson-bot (the Jetson AI Lab Research Group app whose token discord-bot-cli and jetson-ai-lab-cli read as `DISCORD_BOT_TOKEN`); the two servers are 1413999242648748195 (JetsonBot experiments) and 1326246312072581160 (Jetson AI Lab Research Group)
- Slash commands ride the gateway connection the node already holds (discord.py app commands / `INTERACTION_CREATE`), not an HTTP interactions endpoint, so there is still no public path and no Cloudflare change (docs/actors/discord.md: 'There is no webhook, so no public path')
- During a rolling upgrade an older node or API reading a list `guild_id` compares str(list) and drops every message or lists no targets; the actor's `guild_id` is switched to a list only after every node and the API run the new release (rollout order)
  - instruction: rollout doc step: upgrade all nodes, then edit the jetson-bot actor

## Scope exploration

- `s1` — `jetson-bot, discord-bot-cli, jetson-ai-lab-cli, docs/actors/discord.md`: jetson-bot has no code (README, config.yaml permissions 2254325255101632, privacy policy); discord-bot-cli live tests use guild 1413999242648748195 and jetson-ai-lab-cli targets guild 1326246312072581160 with the same `DISCORD_BOT_TOKEN`; Culture.dev is the documented culture-rules bot (discord.md 'On rules.culture.dev')
  - seeds: `c2`
- `s2` — `culture_rules/apps/discord_gateway.py, node/actions/message.py, server/app.py, ops/health.py, actors/secrets.py`: per-actor everywhere: `_wanted`/`_sessions` keyed by actor id, `LEASE_PREFIX`+`actor_id`, `_discord`() loads the actor's own token, targets endpoint resolves that actor's token, gateways() lists one entry per lease; secrets accept any grant name injected as `CULTURE_RULES_SECRET_<NAME>`
  - seeds: `c3`
- `s3` — `culture_rules/events/hook_sink.py:99`: event id is `hook_<surface>``_`<sha256(`delivery_id`)\[:24\]>, no actor; the gateway passes `delivery_id` = `message_id`, so two bots in one guild collapse to one event
  - seeds: `c4`
- `s4` — `culture_rules/apps/discord_gateway.py _on_message, engine/matching.py trigger_matches`: data.bot is carried but unfiltered; `trigger_matches` skips only `self_authored` unless `include_self`; with two bots in one server each sees the other's posts as foreign
  - seeds: `c5`
- `s5` — `culture_rules/node/actions/library.py, model/action_kinds.py, model/describe.py builtins`: library.push needs `content_base64` (≤50 MiB); producers of it are checklist.render, transcript.render and newspaper.\*; no generic text/markdown render step exists; refs/templates can place trigger.data.content but not encode it
  - seeds: `c6`
- `s6` — `../culture-library core/models.py KINDS, core/ingest.py, server/routes_library.py`: KINDS = book, article, newspaper, document, transcript, diary, spec, plan, devague-record, todo; formats by extension epub/pdf/md/txt/json; item columns id, sha256, kind, title, author, format, size, visibility, `created_at`; no URL fetching in core/ or server/
  - seeds: `c7`
- `s7` — `../discord-bot-cli CLAUDE.md 'Discord domain', ../jetson-ai-lab-cli CLAUDE.md`: `discord_client`.run is login -> action -> close, no daemon, no gateway subscription, Intents.none(); jlab runs through discord-bot-cli's venv, REST only; no discord/bot unit or process on spark
  - seeds: `c8`
- `s8` — `../aws-agentcore-chat-bot src/community_bot/discord_bot.py, deploy/ec2`: a separate gateway Q&A bot (Bedrock/Ollama, one channel + threads), systemd unit on EC2; unrelated to the library flow
  - seeds: `c9`
- `s9` — `culture_rules/apps/discord_gateway.py message_data, apps/discord_rest.py post_message, server/hooks/`: `message_data` carries guild/channel/message/author/bot/content/`created_at`/url only; `GATEWAY_EVENT` is only discord.message.created; `post_message` sends content + `allowed_mentions` only; server/hooks holds github.py and jira.py
  - seeds: `c10`
- `s10` — `tests/apps/test_discord_gateway.py, tests/node/test_discord_stage.py`: the only second actor in `test_discord_gateway.py` is non-Discord (ignored); `test_discord_stage.py` is two nodes contending for one actor's lease; no multi-bot case
  - seeds: `c11`
- `s11` — `docs/operations/library-items.md, docs/specs/2026-10-24-gwen-reads-the-library.md`: library-items: generic steps committed, the flows that use them are configured live, never committed
  - seeds: `c12`
- `s12` — `culture_rules/apps/discord_gateway.py gateway_intents, DiscordPyGateway.connect`: intents are default + guilds + `guild_messages` + `message_content`; only `on_message` is registered; no reaction or interaction handler; `GATEWAY_EVENT` is the single discord.message.created
  - seeds: `c13`, `c14`
- `s13` — `docs/actors/discord.md intro, culture_rules/server/hooks/`: Discord is ingested only through the gateway: 'There is no webhook, so no public path and no Cloudflare change'; hooks hold github.py and jira.py only
  - seeds: `c15`
- `s14` — `culture_rules/apps/discord_rest.py`: REST client has `list_targets` (/users/@me/guilds, /guilds/{id}/channels, /channels/{id}) and `post_message` only; no message read or history call; standard library only
  - seeds: `c13`, `c16`
- `s15` — `culture_rules/node/actions/message.py _discord, apps/discord_gateway.py _on_message`: connection.channels is one actor-level allow-list used both to drop inbound messages and to refuse posts ('not in actor allow-list'), so per-flow channel choice cannot reuse it
  - seeds: `c17`
- `s16` — `../culture-library core/ingest.py, culture_rules/node/actions/library.py MAX_BYTES`: library accepts epub/pdf/md/txt/json by extension, 50 MiB, images 415; library.push takes `content_base64` ≤50 MiB and format from the actor's formats; `message_data` carries no attachments
  - seeds: `c18`
- `s17` — `culture_rules/apps/discord_gateway.py _on_message, server/app.py:1007, apps/discord_rest.py list_targets, web/src/actors/app-config.ts:86`: `guild_id` is a single string compared with str(); unset means every guild the bot is in; sessions are per actor, so a second actor on the same token is a second gateway session
  - seeds: `c19`
- `s18` — `challenge pass / security/privacy lens: culture_rules/store/mongo.py ensure(ttl_seconds), events/ingest.py, apps/discord_gateway.py _on_message`: only handoffs (actors/handoff.py:323) and quarantine (events/ingest.py:337) have TTLs; gateway sinks every message of the guild; jetson-ai-lab-cli encrypts its cache, culture-rules events are plaintext
  - seeds: `c31`
- `s19` — `challenge pass / adjacent-systems lens: culture_rules/events/hook_sink.py:99,184-237, server/hooks/github.py:363`: `event_id_for` is shared with the GitHub redelivery check; changing its signature would change GitHub ids
  - seeds: `c32`
- `s20` — `challenge pass / cheap probe: discord.py 2.7.1 in the uv env`: `app_commands`.CommandTree and `context_menu` exist; Intents has reactions/`guild_reactions`; `on_reaction_add` depends on the message cache (discord.py behaviour)
  - seeds: `c33`
- `s21` — `challenge pass / failure-modes lens: apps/discord_gateway.py fencing + GatewayOptions ttl 30s`: the listener is fenced at two thirds of the TTL; another node takes over only after expiry, so up to ~30 s of no listener during handover
  - seeds: `c34`
- `s22` — `challenge pass / migration/reversibility lens: discord_gateway._on_message, server/app.py:1007, discord_rest.list_targets`: each compares against str(guild); a list in an old reader silently matches nothing; reversible by setting the string back
- `s23` — `challenge pass / overlooked-actors lens: jetson-ai-lab-cli CLAUDE.md (public channels, ~120), library.push`: every community member can react/run commands; nothing in the spec limits who triggers a save
- `s24` — `challenge pass / adjacent-systems lens: live actor newspaper-library (read 2026-10-25), docs/actors/library.md`: kinds \[newspaper, transcript, todo\], formats \[epub, md\]; library key minted with a kind allow-list
  - seeds: `c36`
- `s25` — `challenge pass / security lens: culture_rules/node/actions/http.py docstring 1-40`: http.call already pins the vetted address, refuses redirects (RedirectRefused) and ignores proxies
  - seeds: `c37`
- `s26` — `challenge pass / operations lens: apps/discord_rest.py (_retry_after used only by post_message), discord_gateway.MAX_CONTENT 2000`: only `post_message` parses 429; `list_targets` raises on 429; event content is cut at 2000
  - seeds: `c38`
- `s27` — `challenge pass / observability lens: ops/health.py gateways(), hook_stats per actor, run outputs`: per-actor gateway state and hook stats exist; skipped attachments land as run reasons (h14); nothing further found - residual: no alert when a gateway stays disconnected
- `s28` — `challenge pass / concurrency lens: engine/named_lease.py, discord_gateway leases, the schedule cursor (c16)`: one lease per actor fences listeners; the sweep cursor is per flow+channel and advances after a successful push (h12); residual: two overlapping sweep runs of the same flow if a run outlasts its period

## Decisions

- Delivered in three releases, each shippable alone: (1) two bots - per-actor event id, bot-loop guard, `guild_id` list, jetson-bot actor, and the channel-message -> Markdown item flow; (2) reactions and attachments - discord.reaction.added, message read, pdf/epub fetch; (3) commands and schedule - slash and message commands over the gateway, the history sweep with its cursor
  - instruction: the plan's tasks carry a release tag 1/2/3 and each release passes CI and the live check on its own
- Who may save is set by the flow: anyone on the server by default; a flow may restrict it to specific users, or to users holding a tag (role) they opt into
- Acknowledging a save in Discord is configurable per flow (e.g. only for opt-in users and specific reactions)

## Hard questions

- May culture-rules post into the Jetson AI Lab Research Group server, or is jetson-bot listen-only there (a channels allow-list, no discord.message)? jetson-ai-lab-cli is read-only by design. (resolved: Operator 2026-10-25: rules may post in the Jetson AI Lab server, but deferred: culture-rules must support posting there now; what, when and how is decided later)
- Guild model for jetson-bot: one actor spanning both servers (`guild_id` unset or made a list), or one actor per server? `guild_id` is a single string in the gateway filter, server/app.py:1007, `list_targets` and web app-config.ts:86, and two actors on one token would open two gateway sessions that each receive every message. (resolved: Operator 2026-10-25: confirmed: one actor per bot (jetson-bot spans both servers), `guild_id` becomes a list)
- What gesture saves a message to the library: every message in a dedicated channel (works with today's discord.message.created), a reaction such as 📚 on any message (needs a new discord.reaction.added event and the reactions intent), or a slash command (needs an interactions receiver)? (resolved: Operator 2026-10-25: support all of them: a reaction (incl. a custom emoji added later), time-based (on a schedule), every message in chosen channels (channels opt-in or 'all'), and slash commands)
- What gets saved: the message text only, the links in it, or its attachments (pdf/epub)? Attachments need attachment URLs on the event and a download step; the library refuses images. (resolved: Operator 2026-10-25: text and links as Markdown, and also pdf/epub attachments)
- Who may save to the library from the public Jetson AI Lab server: anyone who reacts or runs /save, or only listed roles/users (an allow-list on the flow)? Otherwise any member can push content into the library. (resolved: Operator 2026-10-25: the setup is the approval: anyone on the server may save unless the flow is configured with specific users; also an option for users with tags (roles), as opt-in approval rather than only an allow-list)
- Should the bot acknowledge a save in Discord (a reaction such as ✅ or an ephemeral reply)? A reaction acknowledgement needs a small discord.react action, which c10 does not list. (resolved: Operator 2026-10-25: the bot should be able to acknowledge, and culture-rules configures when (e.g. only for opt-in users and specific reactions))

## Open parks

- [unknown_nonblocking] Two runs of one schedule flow could overlap if a sweep outlasts its period; the cursor update must be compare-and-set or runs serialised
- [follow_up] No alert when a Discord gateway stays disconnected (ops/health shows it but nothing pages the operator)

## Resolved vagueness

- [unknown_blocking] jetson-bot's application id and whether its MESSAGE CONTENT privileged intent is on are not in any committed file; the gateway delivers empty content without it, and jetson-bot is used only over REST today — resolved: Operator 2026-10-25: an operator setup step at rollout: the operator enables MESSAGE CONTENT (and confirms the reactions intent) for jetson-bot and records its Application ID while re-inviting both bots per docs/actors/discord.md; the spec needs no value
- [unknown_nonblocking] Which host holds the jetson-bot token today (env `DISCORD_BOT_TOKEN` for the CLIs, not a grant secret) and which grant name to seal it under for the listening node and API — resolved: Operator 2026-10-25: no host uses the jetson-bot token today; the operator seals the bot secrets with grant
