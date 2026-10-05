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

`68608` grants View Channels, Send Messages and Read Message History. To find
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

## Troubleshooting

| Symptom | Cause |
|---|---|
| The guild list is empty (`[]`) | The bot was installed without the `bot` scope; use the invite link |
| `Missing Access` on a channel | The channel is private; add the bot to its permissions |
| Messages arrive with empty `content` | MESSAGE CONTENT INTENT is off |
| No node is listening | The actor's machine's node lacks the token injection, or the `discord` extra |
| Posting fails "not in actor allow-list" | `connection.channels` is set and excludes the channel |
| `401` from Discord | The token was reset after it was sealed; seal the new one and restart the node |

## On rules.culture.dev

| Item | Value |
|---|---|
| Actor id, machine | `discord-bot`, `spark` |
| Bot | `Culture.dev`, Application ID `1556270251979571320` |
| Server | `1413999242648748195` (JetsonBot experiments), the whole server |
| Default channel | `#culture`, `1537538645337182239` (private: the bot must be added) |
