# discord-library bundle

Files each message in a chosen Discord channel in the library as a Markdown
item. It ships **disabled**: the rule has `enabled: false`, the channel id is a
placeholder, and no step sends `visibility: public`.

| File | What it is |
|---|---|
| `rules/discord-to-library.json` | trigger `discord.message.created`, condition `data.actor == "discord-bot"`, disabled |
| `workflows/discord-message-to-library.json` | `discord.read` (the whole message, as `discord-bot`), `markdown.render`, then `library.push` (kind `discord-message`, format `md`, visibility `private`) |

It ships no actors. It uses the Discord actor (`discord-bot`, grant
`RULES_DISCORD_BOT_TOKEN`, or `jetson-bot`, grant
`DISCORD_BOT_TOKEN_JETSON_BOT`; see
[the Discord guide](../../actors/discord.md)) and a library actor, here
`newspaper-library` from the newspaper bundle. That actor must list kind
`discord-message` and format `md` ([the library guide](../../actors/library.md)).

The read step names its bot by id (`discord-bot`). An action step's
`params.actor` is an actor id, never a reference: `inputs.actor` would name an
actor called `inputs.actor`, so it is refused on save. For a channel only
jetson-bot can see, change it to `jetson-bot`. The rule's condition names
the same bot (`data.actor`, the actor that recorded the event), so change both
together.

## Before enabling

1. Replace the placeholder `000000000000000000` in the rule's trigger
   `channels` with the real channel id (or a list of ids). The placeholder is a
   valid id that no channel has, so the rule never fires until you do; a
   channel name is refused on save, because only numeric ids (or `all`) match.
2. Add kind `discord-message` and format `md` to the library actor, and mint
   its library key with `--kind discord-message`.
3. Check the rule is placed on the machine that holds the Discord actor, and
   that the read step names a bot that is in the channel.
4. Import and enable it with the CLI, dry run first.

## How it behaves

- Enabling the rule is also what makes the gateway record its channel:
  messages from channels no enabled `discord.*` rule selects are never
  stored, and Discord events expire after 30 days.
- The text comes from `discord.read`, so a message longer than the event's
  2000 characters is filed whole. A message with only an attachment is filed
  with no text; to file its pdf, epub or Markdown attachments too, add
  `attachment.fetch` and a `library.push` per file (see
  `docs/actors/discord.md`, Saving attachments).
- If both bots are in the channel, each records its own event; the condition
  lets only the named bot's event run the rule, so the message is read and
  filed once. Without it the rule would run twice (the library would still
  keep one item, the second push reporting `created: false`).

Deleting the Discord message later leaves the library item; archive it by hand.

## Saving with a command

The same flow can start from a command instead of a message. Commands are the
bot's configuration: give its actor `discord.command.invoked` in
`params.events`, its `connection.application_id`, and the commands, for
example:

```json
"commands": [
  {"name": "save", "type": "slash", "description": "Save a message to the library",
   "options": [{"name": "message", "description": "A message link or id",
                "kind": "message"}]},
  {"name": "Save to library", "type": "message"}
]
```

Then a rule with the trigger `discord.command.invoked`, `command` set to those
names, maps `trigger.data.target_message_id` and `target_channel_id` to the
workflow's message and channel, and ends with `discord.reply` (`event_id` from
`trigger.id`) to answer the person privately. Its `concurrency_key` must
resolve for command events, e.g.
`discord-command:{trigger.data.target_message_id}`.

A concurrency key is global: rules whose keys resolve to the same string share
one run at a time and one waiting slot, and a newer waiting event replaces an
older one. Give each flow (and each bot) its own prefix, e.g.
`discord-message:<bot>:{trigger.data.message_id}` and
`discord-reaction:<bot>:{trigger.data.message_id}`, or a message's 📚 can
displace its own message event while another run holds the key.
