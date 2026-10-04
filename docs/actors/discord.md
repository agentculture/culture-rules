# Discord app actor

A Discord bot, `Culture.dev`, listens to messages through the Discord Gateway
and posts messages with the `message` action.

| Item | Value on rules.culture.dev |
|---|---|
| Actor id | `discord-bot` (machine `spark`) |
| Application / bot user id | `1556270251979571320` |
| Server (guild) | `1413999242648748195` (JetsonBot experiments), the whole server |
| Default channel | `#culture`, `1537538645337182239` |
| Secret (grant, hidden) | `RULES_DISCORD_BOT_TOKEN` |

## 1. Create the application and bot

1. In the [Discord Developer Portal](https://discord.com/developers/applications),
   choose **New Application**.
2. Open the **Bot** page and **Reset Token** to get the bot token; store it in
   grant (step 3). Under **Privileged Gateway Intents**, enable **MESSAGE
   CONTENT**, or the listener receives messages without their text.
3. Optional: under **Installation**, add the `bot` scope to **Guild Install**
   so the install button adds the bot user and not only slash commands.

## 2. Invite the bot to the server

**Installing the app is not enough.** An install with only the
`applications.commands` scope adds slash commands, not the bot user, and the
bot then sees no server. Invite it with the `bot` scope and these permissions:
View Channels, Send Messages and Read Message History (permission integer
`68608`).

```text
https://discord.com/oauth2/authorize?client_id=<application-id>&scope=bot&permissions=68608&guild_id=<server-id>&disable_guild_select=true
```

Open the link signed in as a server admin and authorise it.

**Private channels need the bot added explicitly.** For each one, open
**Edit Channel**, **Permissions**, **Add members or roles**, add the bot or its
role, and allow View Channel, Send Messages and Read Message History. A channel
the bot cannot open answers `Missing Access`.

## 3. Seal the secret

```bash
grant set RULES_DISCORD_BOT_TOKEN - --hidden     # paste the bot token
```

## 4. Let the node use the secret

The gateway listener and the `message` action run on the engine node of the
actor's machine. Inject the token into that node's unit:

```text
--inject CULTURE_RULES_SECRET_RULES_DISCORD_BOT_TOKEN=RULES_DISCORD_BOT_TOKEN
```

With `install.sh`, pass `--secret RULES_DISCORD_BOT_TOKEN`. Restart the node.

## 5. Define the actor

```json
{
  "id": "discord-bot",
  "name": "Discord (Culture.dev bot)",
  "kind": "app",
  "machine": "spark",
  "params": {
    "surface": "discord",
    "events": ["discord.message.created"],
    "actions": ["message"],
    "self_identity": "1556270251979571320",
    "connection": {
      "bot_token": "grant:RULES_DISCORD_BOT_TOKEN",
      "guild_id": "1413999242648748195"
    }
  }
}
```

`guild_id` limits listening to one server. `channels: [...]` would further
limit both listening and posting to those channel ids; leave it out to use the
whole server. One string counts as one channel.

## Events and data

`discord.message.created` carries `message_id`, `guild_id`, `channel_id`,
`author_id`, `author_name`, `bot`, `content` (truncated), `created_at` and
`url`. Exactly one node
holds the gateway connection, through the named lease `discord-gateway:<actor
id>`: the node on the actor's `machine`. Messages the bot itself sends are
tagged `self_authored`.

The action is `message` with `{actor: "discord-bot", channel: "<channel id>",
text}`. Mass mentions (`@everyone`, `@here`, roles) are always suppressed.
Without `actor`, `message` posts to a Culture mesh channel instead.

## Verify

```bash
# Is the bot in the server, and can it open the channel? (token injected, never printed)
grant run --inject D=RULES_DISCORD_BOT_TOKEN -- sh -c \
  'curl -s -H "Authorization: Bot $D" https://discord.com/api/v10/users/@me/guilds'
```

Post a message in a channel the bot can see. The node's cycle report lists the
actor under `listening`, and a `discord.message.created` event lands in the
store.

## Troubleshooting

| Symptom | Cause |
|---|---|
| The bot is in no server (`[]`) | It was installed without the `bot` scope; use the invite link above |
| `Missing Access` on a channel | The channel is private; add the bot to its permissions |
| Messages arrive with empty `content` | The MESSAGE CONTENT intent is off |
| No node is listening | The actor's `machine` node was not started with the token injection, or the `discord` extra is missing |
| A message fails "not in actor allow-list" | `connection.channels` is set and excludes that channel |
