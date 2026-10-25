# Discord app actor

A Discord bot listens to messages through the Discord Gateway, which raises
`discord.message.created` events, and posts messages with the
`discord.message` action ("Post a message on Discord"). There is no webhook, so no public path and no Cloudflare change.

This assumes you are signed in to Discord as an administrator of the server,
and that nothing else is prepared. Read [README.md](README.md) first for the
prerequisites and how secrets work.

The secret this guide creates:

| grant name (suggested) | What it is | Used by |
|---|---|---|
| `RULES_DISCORD_BOT_TOKEN` | the bot's token | engine node and API |

This table is the first bot's. More bots are covered in
[Several bots](#several-bots) below.

## 1. Create the application and bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications)
   and click **New Application**. Name it; the name becomes the bot's
   display name.
2. Under **General Information**, note the **Application ID**. On **Bot**, note
   the bot's **username**.
3. Open **Bot**:
   - Under **Privileged Gateway Intents**, turn on **MESSAGE CONTENT INTENT**
     and save. Without it, messages arrive with empty text.
   - Turn off **Public Bot** if only your server should be able to add it.
4. Still on **Bot**, click **Reset Token** and copy the token. Discord shows it
   only once.

## 2. Seal the token

On the serving host:

```bash
grant set RULES_DISCORD_BOT_TOKEN - --hidden     # paste the token, then Enter and Ctrl-D
```

## 3. Invite the bot to your server

**Installing the app is not enough.** An install with only the
`applications.commands` scope adds slash commands but not the bot user, and the
bot then sees no server. Invite it with the `bot` scope. Replace the two ids and
open this link:

```text
https://discord.com/oauth2/authorize?client_id=<Application ID>&scope=bot&permissions=68608&guild_id=<server id>&disable_guild_select=true
```

`68608` grants View Channels, Send Messages and Read Message History. A bot
that is to react to messages (planned, Release 2) also needs **Add
Reactions**: use `68672` (`68608` plus `64`). For slash commands (planned,
Release 3) add the `applications.commands` scope: `scope=bot%20applications.commands`.
Permissions are the bot's access; a bot that only reads and posts does not need
the extra bit. To find
the **server id**, turn on **User Settings**, **Advanced**, **Developer Mode**.
Then right-click the server icon and choose **Copy Server ID**. Channel ids are
copied the same way.

Optionally, under **Installation** in the portal, add `bot` to the **Guild
Install** scopes so the install button works next time.

**Private channels need the bot added by hand.** For each one: **Edit
Channel**, **Permissions**, **Add members or roles**, choose the bot, and allow
View Channel, Send Messages and Read Message History.

## 4. Give the services the token

The gateway listener and the `discord.message` action run on the engine node
of the actor's machine. The API also uses the token to list the bot's servers
and channels for the editor. Add the injection to both units on that machine:

```text
--inject CULTURE_RULES_SECRET_RULES_DISCORD_BOT_TOKEN=RULES_DISCORD_BOT_TOKEN
```

The README shows how; with `install.sh`, pass `--secret RULES_DISCORD_BOT_TOKEN`.
Then:

```bash
systemctl --user restart culture-rules-api culture-rules-node
```

## 5. Create the actor

In the editor's **Actors** tab, add an **App** actor and set **Machine** to the
serving host, or write the JSON with the CLI:

```json
{
  "id": "discord-bot",
  "name": "Discord bot",
  "kind": "app",
  "machine": "<serving host>",
  "params": {
    "surface": "discord",
    "events": ["discord.message.created"],
    "actions": ["discord.message"],
    "self_identity": "<bot username>",
    "connection": {
      "bot_token": "grant:RULES_DISCORD_BOT_TOKEN",
      "guild_id": "<server id>"
    }
  }
}
```

- `guild_id` limits listening to one server.
- `channels: ["<channel id>", ...]` would also limit listening and posting to
  those channels. Leave it out to use the whole server.
- `self_identity` is the bot's **username** (shown on the **Bot** page). Messages
  are matched on the author's username, so the bot's own posts are tagged
  `self_authored`.

## 6. Verify

```bash
# the bot is in your server (the token is injected, never printed)
grant run --inject D=RULES_DISCORD_BOT_TOKEN -- sh -c \
  'curl -s -H "Authorization: Bot $D" https://discord.com/api/v10/users/@me/guilds'
```

This should list your server. Then post a message in a channel the bot can see:
the node's log reports the actor as listening, and a `discord.message.created`
event is recorded. A rule with that trigger and a `discord.message` action
replies (see below).

## Events and data

`discord.message.created` carries `message_id`, `guild_id`, `channel_id`,
`author_id`, `author_name`, `bot`, `content` (truncated), `created_at` and
`url`. Exactly one node holds the gateway connection, through the lease
`discord-gateway:<actor id>`: the node on the actor's `machine`. Messages the
bot sends itself are tagged `self_authored`. Mass mentions (`@everyone`,
`@here`, roles) in posted messages are always suppressed.

A message is recorded only from a channel that an enabled rule with a
`discord.*` trigger selects. A rule with no `channels`, or `"all"`, selects
every channel. With no such rule, nothing is recorded. The selection is one
union for every Discord actor, and each actor's `connection.channels` and
`guild_id` still apply on top. Discord events expire after 30 days through a
MongoDB TTL index. Set `CULTURE_RULES_DISCORD_EVENT_RETENTION_DAYS` on the
listening node to change that. Other events never expire.

## Posting a message

In a rule, choose **Post a message on Discord** under "Then what happens?":

1. **Actor:** the Discord app actor (only actors that declare `discord.message`
   are offered).
2. **Server:** picked from the servers the bot is in. It is preselected when
   there is only one; `connection.guild_id` limits the list to that server.
3. **Channel:** picked from that server's text and announcement channels. A
   private channel the bot was not added to is marked "the bot was not added".
   To reply where a message came from, map the channel from the trigger
   instead: `trigger.data.channel_id`.
4. **Text.**

The editor reads the list from `GET /actors/{id}/discord/targets` (CLI:
`culture-rules actors discord-targets <id>`). The API resolves the bot token
for that call, so the API unit needs the token injection too (step 4). If the
list cannot be loaded, the editor asks for the channel id instead.

The stored action is
`{kind: "discord.message", params: {actor, guild, channel, text}}`.
**Send a message on the mesh** (`message`) is a separate action that posts to
a Culture mesh channel and takes no actor. A rule saved before 0.12.0 as
`message` with a Discord actor still posts to Discord, and the editor shows it
as a Discord message.

## Several bots

More than one bot can serve one culture-rules install, each as its own actor.
The target set is two bots:

| Actor id | Bot | Token grant | Servers |
|---|---|---|---|
| `discord-bot` | Culture.dev | `RULES_DISCORD_BOT_TOKEN` | `1413999242648748195` (JetsonBot experiments) and `<private server id>` (the operator's private server): a list |
| `jetson-bot` | jetson-bot | `DISCORD_BOT_TOKEN_JETSON_BOT` | `1326246312072581160` (Jetson AI Lab Research Group, public) only: a single id, never in experiments |

For each bot, repeat sections 1 to 5 with its own application:

- **Invite** with the `bot` and `applications.commands` scopes, and the
  permissions View Channels, Send Messages, Read Message History and Add
  Reactions (`68672`). The invite link in section 3 shows the shape; add
  `%20applications.commands` to `scope`.
- **Intents** (on **Bot**, **Privileged Gateway Intents**): MESSAGE CONTENT
  INTENT is required. Guild messages are a standard intent and are always on.
  Reactions (guild message reactions) are a standard intent too; the listener
  asks for them from Release 2 on (planned), and nothing to switch on in the
  portal.
- **One grant name per bot.** The pattern for a new bot is
  `DISCORD_BOT_TOKEN_<BOT>` (for example `DISCORD_BOT_TOKEN_JETSON_BOT`), and
  the actor's `connection.bot_token` is `grant:DISCORD_BOT_TOKEN_JETSON_BOT`.
  The first bot keeps its older name `RULES_DISCORD_BOT_TOKEN`. Inject each
  token into both units (section 4) under its own
  `CULTURE_RULES_SECRET_<NAME>` variable.
- **Unused grants.** The jetson-bot's `DISCORD_BOT_APPLICATION_ID_JETSON_BOT`
  and `DISCORD_BOT_PUBLIC_KEY_JETSON_BOT` also exist in grant. The public key
  is not used: culture-rules receives interactions over the gateway, not an
  HTTP interactions endpoint, so there is nothing to verify and no public URL.
  Do not inject either.

```json
{
  "id": "jetson-bot",
  "name": "jetson-bot",
  "kind": "app",
  "machine": "<serving host>",
  "params": {
    "surface": "discord",
    "events": ["discord.message.created"],
    "actions": ["discord.message"],
    "self_identity": "<bot username>",
    "connection": {
      "bot_token": "grant:DISCORD_BOT_TOKEN_JETSON_BOT",
      "guild_id": "1326246312072581160"
    }
  }
}
```

### `guild_id` as a list

`connection.guild_id` is one id or a list of ids. Unset or empty means every
server the bot is in. A list limits listening, posting and the editor's server
list to those servers.

**Rollout order matters.** A node or API older than this change reads a list
as matching no server, so the listener and the editor's lists go quiet.
Upgrade every node and the API first, and only then change a stored
`guild_id` from a single id to a list. This applies to Culture.dev's switch to
its two servers; jetson-bot's single id has no ordering constraint and works on
every version.

### Two bots, one message

The gateway event's delivery id is per actor. When two bots can see the same
message (both in one server and channel), each records its own
`discord.message.created` event, so a rule triggered by both runs twice. Keep
each actor to its own servers with `guild_id`, or limit the rule's trigger to
one actor's channels. A flow that renders the message with `markdown.render`
and pushes it still files one library item: both runs build the same page, and
the library keeps the first (the second push reports `created: false`).

## Filtering a trigger

A rule with a `discord.message.created` trigger takes these optional
parameters in the trigger's `params`:

| Param | Meaning |
|---|---|
| `include_bots` | `true` to admit messages from bots; off by default |
| `channels` | a list of channel ids, a bare id, or `"all"`; absent or empty is every channel |
| `users` | author ids or usernames, case-insensitive |
| `roles` | role ids |

With both `users` and `roles` set, either admits: a listed user, or anyone
holding a listed role. **A `roles` filter matches no message yet.** Roles are
read from `data.user_roles`, which message events do not carry in Release 1
(planned, Release 2), so a rule with only `roles` never fires, and with
`users` too it admits only the listed users.

## Deleting a message

Deleting a Discord message does not delete what a flow made from it. The
library item stays; archive it by hand in the library.

## What is planned

Not on disk yet, and not to be relied on:

- reactions as triggers and the attachment fetch (Release 2);
- slash and message commands, the ephemeral reply and a scheduled sweep
  (Release 3);
- roles on message events (`data.user_roles`, Release 2): until then a `roles`
  filter matches no message.

On disk today: the `discord.read` action (one message in full; it keeps to
the actor's `connection.channels` and `connection.guild_id`) and the
`markdown.render` step (the step panel's Built-in select). The disabled bundle
[`docs/rules/discord-library`](../rules/discord-library/README.md) shows the
flow: read, render, push.

A `discord.read` or `discord.message` action, as a workflow step or as a
rule's own action, names its bot in `params.actor` by id (`discord-bot`,
`jetson-bot`). The node dispatches through that value as given, so a reference
such as `inputs.actor` or `trigger.data.actor` is refused on save
(`action_actor_not_literal`). Pick the bot that is in the channel; to act only
on one bot's events, add a rule condition on `data.actor`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| The guild list is empty (`[]`) | The bot was installed without the `bot` scope; use the invite link |
| `Missing Access` on a channel | The channel is private; add the bot to its permissions |
| Messages arrive with empty `content` | MESSAGE CONTENT INTENT is off |
| No node is listening | The actor's machine's node lacks the token injection, or the `discord` extra |
| A step fails `actor_unavailable` | Its `params.actor` is not an enabled actor's id; a document stored before 0.30.1 may still hold a reference such as `inputs.actor` (it still loads and runs, but is refused on its next save until the actor is named by id) |
| A message produces no event | No enabled rule with a `discord.*` trigger selects its channel |
| Posting fails "not in actor allow-list" | `connection.channels` is set and excludes the channel |
| `401` from Discord | The token was reset after it was sealed; seal the new one and restart the node |

## On rules.culture.dev

| Item | Value |
|---|---|
| Actor id, machine | `discord-bot`, `spark` |
| Bot | `Culture.dev`, Application ID `1556270251979571320` |
| Servers | `["1413999242648748195", "<private server id>"]` (JetsonBot experiments and the operator's private server; a list, so upgrade every node and the API first) |
| Default channel | `#culture`, `1537538645337182239` (private: the bot must be added) |
