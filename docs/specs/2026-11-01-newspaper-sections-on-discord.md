# Newspaper sections on Discord

> The daily newspaper posts to Discord one message per section, routes the jobs section to #open-to-work, and can post a section as a tagged forum post — through generic step options, with every route, channel and tag in config

## Audience

- Jetson AI Lab Discord members who read the newspaper, filtering forum posts by topic tag; and the operator, who configures the routing

## Before → After

- Before: The newspaper reaches Discord as 1900-character pieces in #bot-experimentations, several sections per message, untagged and unsearchable by topic; discord.message cannot post to a forum
- After: Each day every newspaper section is its own forum post titled topic - date - a one-line LLM summary, tagged with its topic (Jetson optimization, Jetson jobs, Jetson news, Isaac ROS), so a reader filters the forum by tag

## Why it matters

- A reader interested in one topic (jobs, Isaac ROS) follows its tag instead of scanning a whole daily dump; posts become a searchable archive per topic

## Requirements

- Routing by section is a when-condition on a loop body step over an item field (the condition tree already has matches/in/compare, `culture_rules`/model/condition.py:14-15), because nested loops are refused (`culture_rules`/engine/runs.py:1803): so each split piece must carry its heading as a field of a single loop item
  - honesty: a flow test runs a `for_each` over parts where two body steps with when on item.heading post to different channels, and each part reaches exactly one channel
- markdown.`from_html` gains a generic option `split_at` (a heading tag h1-h6): a heading of that rank or higher always starts a new part, and a new output parts lists \[{heading, text, index}\], one whole part per heading with no size cap, beside the unchanged markdown, chunks and count; text before the first split heading is a part with an empty heading
  - honesty: a unit test renders an issue-shaped HTML (h1, p.date, three h2 sections) with `split_at` h2 and gets 4 parts: the masthead with heading empty, then each section whole with its heading, even when one section is longer than `max_chars`
- The editor's step catalog declares the new parts output of markdown.`from_html` (web/src/workflows/StepEditor.tsx:325-329), the setup guide docs/operations/newspaper.md:370-377 describes `split_at` and parts, and describe/runner registrations stay as they are (runner.py:334-337, describe.py:88)
  - honesty: web vitest/typecheck pass with parts declared in StepEditor, and docs/operations/newspaper.md names `split_at` and parts
- discord.message takes two optional generic params, `thread_name` and tags (a list of tag names): when given, the step reads the channel (GET /channels/{id}), requires a forum (type 15), resolves each tag name against the forum's `available_tags`, and creates a forum post (POST /channels/{id}/threads with name, `applied_tags` and message.content, mentions suppressed as today); outputs `message_id` and `thread_id`; without `thread_name` the step behaves exactly as today
  - honesty: a FakeTransport test sees GET /channels/{id} then POST /channels/{id}/threads with name, `applied_tags` as the resolved ids and message.content with `allowed_mentions` parse empty; the step returns `message_id` and `thread_id`; without `thread_name` the request is the same POST /channels/{id}/messages as today
- A forum post fails the same way a message does: a timeout is `outcome_unknown` and never retried (a retry could create a second post), 429 and 5xx retry, other 4xx refuse; `thread_name` on a non-forum channel or tags without `thread_name` fail `bad_params` before any post; a tag name the forum lacks is left off and listed in an output `tags_missing`; a `thread_name` longer than Discord's 100 characters is cut at a word boundary
  - honesty: tests cover: a timeout on the thread POST fails `outcome_unknown` and non-retryable; 429 retryable; a text channel with `thread_name` fails `bad_params` with no POST; an unknown tag posts with the known tags and lists the unknown in `tags_missing`; a 140-character `thread_name` is sent as at most 100 characters ending at a word
- Live tests of forum posts go first to the testing forum bot-experiments-forum 1566568383082799214 in the Experiments server 1413999242648748195 (operator-designated for exploration/testing); the Jetson AI Lab forum 1563965995788271617 is used only after a clean test
  - honesty: the first live forum posts land in bot-experiments-forum 1566568383082799214, and the Jetson forum is written only after the operator approves the test result
- A forum post whose text is longer than one Discord message (2000 characters) starts the thread with the first piece and posts the rest as replies in the same thread, split at line breaks, in order; outputs `message_id` (the starter), `thread_id` and the reply message ids
  - honesty: a test with a 4500-character text posts a starter of at most 2000 characters and then replies to the thread id in order, each at most 2000 characters, together equal to the text split at line breaks
- llm.complete reads briefs from configuration: an agent actor may declare named briefs (params.briefs, a map of name to system-prompt text, written by an admin like every actor param), and a step's brief selects one by name; the step can still only pick a brief, never write or edit one; the three briefs in code keep working unchanged; the headline prompt (one plain line of at most N characters, the length and wording) is actor config, not code
  - honesty: tests: a step with brief: <name> uses the text from the actor's params.briefs as the system message; a brief name neither the actor nor code defines fails `invalid_request` before any request; a step param holding prompt text is ignored or refused, never sent; the three code briefs still select as before
- The live flow titles each post heading - date - headline through config: the date read from the issue run (run.read of the claim step's date), the headline from an llm.complete step with brief headline on the part's text, and the title a template in `thread_name`; when the headline step is skipped or fails (`on_error` skip), a second send step routed by when posts the title heading - date
  - honesty: the test workflow run shows each post's title built from the part heading, the issue date and the headline output, and a run with the headline step skipped still posts heading - date
- The live newspaper-to-discord flow gets a new revision only after a clean test: a test workflow first posts one real issue's sections to the testing forum; then revision 3 posts each section as a tagged forum post to the Jetson AI Lab forum 1563965995788271617 and stops the size-cut posts to #bot-experimentations
  - honesty: newspaper-to-discord stays at rev 2 until the operator approves the testing-forum result; rev 3 is written with `expected_revision` 2

## Honesty conditions

- grep of the new code for newspaper, a section name, a Discord channel id or a tag name finds none; every route, channel, title and tag lives in the live workflow revision
- the live newspaper-to-discord revision is the only place channels, tag names and the title template appear; no committed docs/rules bundle holds them
- tag lookup happens in the node-side step (GET /channels/{id} with the bot token); no API route reads a bot token
- the existing tests/node/`test_markdown_html.py` passes unchanged, and output without `split_at` has no parts key
- the ActionPicker declaration and `action_kinds.py` both list `thread_name` and tags as optional, and the targets API response shape is unchanged (`test_openapi_contract` passes without an openapi edit for targets)
- the forum tags the readers filter on exist on both forums (probe 2026-11-01: four tags each)
- newspaper-to-discord rev 2 config and its runs 10-29..11-01 show chunked posts to 1407337283794767873
- after rev 3, the Jetson forum shows one post per section per issue with its topic tag
- Discord forum tag filtering shows only the posts carrying the chosen tag
- the numbers are read from the run records (posts per run, `tags_missing` empty, title length) and from the forum itself, not estimated
- the diff adds no prompt text and no newspaper, section, tag, channel or title-format literal under `culture_rules`/; the only new strings are param names, error codes and Discord API paths

## Success signals

- On the testing forum, one real issue yields 4 posts, each with exactly 1 matching tag and a title of the form topic - date - summary at most 100 characters; then on the Jetson forum for 3 consecutive issues, 4 tagged posts per issue (fewer only when a section had no results) and 0 failed newspaper-to-discord runs

## Scope / boundaries

- Routing, channels, thread titles and tag names are configuration on the live newspaper-to-discord workflow (a new revision), never code: no step learns the word newspaper, a section name, a channel or a tag (steps-generic-flows-are-config; the live workflow notes say 'Generic steps only (d5) ... Live config (d4), not committed')
- The API server cannot read a Discord bot token (GET /actors/jetson-bot/discord/targets answers `secret_unavailable` 409 on the LAN API): anything that must read a forum's tags runs on a node, inside the step, not in the API
- Without `split_at`, markdown.`from_html` output is byte-identical to today (the live newspaper-to-discord rev 2 keeps working unchanged until its own revision)
- discord.message's declaration (`culture_rules`/model/`action_kinds.py`:106-113) and the editor's hand-copied one (web/src/rules/ActionPicker.tsx:64-67) gain `thread_name` and tags as optional params; the Discord targets picker (server/app.py:1018-1054, `TEXT_CHANNEL_TYPES` (0,5) in `discord_rest.py`) is not changed: forum channels and their tags are not listed in the editor in this work
- No process or business-logic text is added to code in this work: no prompt wording, character budget, title format, section name, tag or channel; code gains only mechanisms (split at a heading, forum post with tags by name, long post continued in its thread, briefs read from actor config)

## Non-goals

- No sweep or read of forum channels, no creating or editing forum tags (the bot lacks Manage Channels), no forum support in the editor's targets picker, and no change to the daily-newspaper workflow or its keyset measurement

## Assumptions

- jetson-bot can already post in #open-to-work 1461808693225066640 (a text channel) and create posts in bot-experimentation-forum 1563965995788271617 (a forum, type 15) in guild 1326246312072581160; it cannot manage threads or channels, so it cannot create forum tags
- A forum post by jetson-bot does not loop back into the discord-\*-to-library flows: the gateway reports a post's message with `channel_id` = the thread id (apps/`discord_gateway.py` `_message_dict` l.228-244), channel filters compare `channel_id` exactly, and bot events are dropped unless `include_bots`/`include_self` (engine/matching.py ~245-247)
- Tag names equal three section headings (Jetson optimization, Jetson news, Isaac ROS) but the jobs heading is Jetson job opportunities and its tag is Jetson jobs: the live workflow handles that in config (a jobs send step with the literal tag, routed by when on item.heading, beside a general step whose tag is the part's own heading), with no mapping in code

## Scope exploration

- `s1` — `live workflow newspaper-to-discord rev 2 (workflows list, 2026-11-01)`: run.read(render.html) -> markdown.`from_html`(`max_chars` 1900) -> `for_each` posting each chunk with discord.message as jetson-bot to #bot-experimentations 1407337283794767873; succeeded after every issue 10-29..11-01; chunks are by size, so one message can hold several sections
  - seeds: `c2`
- `s2` — `Discord API probe with jetson-bot's grant token (read-only GET /channels, /guilds/roles, /members, 2026-11-01)`: open-to-work: text, view+send true; bot-experimentation-forum: forum, one tag 'Jetson optimization' (1566567803270598776), `require_tag` false, create posts true, `manage_threads` false; bot-experimentations: text, send true
  - seeds: `c3`
- `s3` — `culture_rules/model/condition.py + culture_rules/engine/runs.py:1803`: matches (re.fullmatch) and in exist; a `for_each` body cannot contain another loop, so a piece of a long section must still carry its section heading
  - seeds: `c4`
- `s4` — `culture-rules actors discord-targets jetson-bot (2026-11-01)`: 409 `secret_unavailable`: the token lives on the nodes; the API-side targets view cannot list forum tags today
  - seeds: `c5`
- `s5` — `culture_rules/node/actions/markdown_html.py (_Doc l.60-, _chunks l.~201, html_to_markdown l.~214, invoke l.~270)`: any heading opens a block, but `_chunks` packs blocks greedily across headings (tests/node/`test_markdown_html.py`:92-103), so no per-section split exists; `_pieces` already keeps a heading with its first line; bad config fails `bad_params` non-retryable
  - seeds: `c6`
- `s6` — `culture_rules/node/actions/newspaper_render.py (_masthead l.~349, _html l.~355, _body l.~320-340)`: issue html: h1 title + p.date (+ p.note) then <section><h2>name</h2> per section with ul/li results, p.note, p.empty; so `split_at` h2 gives the masthead as the first part and one part per section
  - seeds: `c6`, `c7`
- `s7` — `engine/runs.py _when 2255-2266, _gathered_inputs 2228-2246, _action_step_params 2000-2004; condition._resolve 191-203`: a body step's when sees item.heading through dotted paths when the step declares an item input port; params take inputs.item.text; so routing by part heading is pure config
  - seeds: `c4`
- `s8` — `web/src/workflows/StepEditor.tsx:325-329, docs/operations/newspaper.md:370-377, node/runner.py:334-337, model/describe.py:88, explain catalog`: markdown.`from_html` is declared in the StepEditor outputs and the newspaper guide; the explain catalog lists no builtins; tests in tests/node/`test_markdown_html.py` and `test_discord_wiring.py`:37
  - seeds: `c8`
- `s9` — `culture_rules/node/actions/sections.py (document.sections)`: an existing builtin splits at a heading (`split_on`) but outputs a heading, links and a plain excerpt, not the Markdown text Discord shows, so it is not a substitute; its `split_on` naming is the precedent for `split_at`'s values
  - seeds: `c6`
- `s10` — `culture_rules/node/actions/message.py (invoke l.~78-96, _discord l.~125-170) + culture_rules/apps/discord_rest.py (post_message l.386-434, _get_ok l.186, DiscordError l.69-94)`: `post_message` is the template: direct transport POST (never auto-retried), TimeoutError -> `outcome_unknown` non-retryable, 429 retryable, 5xx retryable, other 4xx refused; GET helpers retry 429; no channel caching; params validated before the secret is read; outputs `message_id` (+`custom_ids` with buttons)
  - seeds: `c9`
- `s11` — `culture_rules/model/action_kinds.py:106-113, web/src/rules/ActionPicker.tsx:64-67, server/app.py:1002-1054, apps/discord_rest.py list_targets l.194-226`: params are declared twice (model + editor copy); extra params are tolerated; the targets API returns id/name/visible only and filters to text types 0 and 5, so forums are invisible to the picker today
  - seeds: `c11`
- `s12` — `apps/discord_gateway.py _message_dict l.228-244, engine/matching.py ~245-247, discord.sweep`: no thread/forum handling in the gateway; a forum post arrives with the thread id as `channel_id`; bot posts are skipped by default; discord.sweep on a forum id would fail (forums have no /messages), so sweeping a forum is out of scope
  - seeds: `c12`
- `s13` — `tests/node/test_message_action.py (FakeTransport, l.57-470), docs/actors/discord.md (l.168 params, ## Buttons l.~325)`: tests use a FakeTransport per URL; the guide documents params and buttons; a forum-post section goes after Buttons
  - seeds: `c9`, `c10`
- `s14` — `Discord API probe, bot-experimentation-forum 1563965995788271617 (2026-11-01, after the operator added tags)`: `available_tags`: Jetson optimization 1566567803270598776, Jetson news 1566567904734879825, Isaac ROS 1566567942525816882; `require_tag` false; the names equal three of the four section headings; the jobs section has no tag (it goes to #open-to-work)
  - seeds: `c3`, `c9`
- `s15` — `Discord API probe, bot-experiments-forum 1566568383082799214 in guild 1413999242648748195 (2026-11-01)`: forum (type 15), tags Isaac ROS 1566568405421531207, Jetson optimization 1566568446286757969, Jetson news 1566568483850948658, `require_tag` false, jetson-bot can create posts, cannot manage threads; same tag names as the Jetson AI Lab forum but different ids, so resolving tags by name at post time lets one config move between forums by changing only the channel
  - seeds: `c15`, `c9`
- `s16` — `culture_rules/node/actions/llm.py (_BRIEFS l.166, _brief l.256-262, actor.params l.221-231) + docs/actors/llm.md:6,90,96`: briefs are a code map selected by a step's brief param; the guarantee that matters is that a step cannot supply prompt text, and actor params are already admin-only config (`base_url`, model, `api_key` read from actor.params), so actor-declared briefs keep that guarantee while moving prompt wording out of code
  - seeds: `c20`

## Decisions

- A tag name the forum does not have is left off and the post still goes out; the step reports the names it could not apply (operator, q1)
- Development and live tests use the testing forum bot-experiments-forum in the Experiments server; forum posts go to the forum channel; the jobs section is a forum post tagged Jetson jobs; one post per section, sections are not split further (operator, q2)
- Each forum post's title is topic - date - a one-line summary written by an LLM (operator, q3)
- \#open-to-work is not used in this work: the jobs section reaches Discord as a tagged forum post (reading of the operator's 'we don't split'); routing a section to a text channel stays possible in config later

## Open parks

- [follow_up] Moving the three existing code briefs (newspaper, transcript, recording) into actor config the same way
