# discord-library bundle

Files each message in a chosen Discord channel in the library as a Markdown
item. It ships **disabled**: the rule has `enabled: false`, the channel id is a
placeholder, and no step sends `visibility: public`.

| File | What it is |
|---|---|
| `rules/discord-to-library.json` | trigger `discord.message.created`, disabled |
| `workflows/discord-message-to-library.json` | `discord.read` (the whole message, with the bot that saw it), `markdown.render`, then `library.push` (kind `document`, format `md`) |

It ships no actors. It uses the Discord actor (`discord-bot`, grant
`RULES_DISCORD_BOT_TOKEN`, or `jetson-bot`, grant
`DISCORD_BOT_TOKEN_JETSON_BOT`; see
[the Discord guide](../../actors/discord.md)) and a library actor, here
`newspaper-library` from the newspaper bundle. That actor must list kind
`discord` and format `md` ([the library guide](../../actors/library.md)).

## Before enabling

1. Replace the placeholder `000000000000000000` in the rule's trigger
   `channels` with the real channel id (or a list of ids). The placeholder is a
   valid id that no channel has, so the rule never fires until you do; a
   channel name is refused on save, because only numeric ids (or `all`) match.
2. Add kind `document` and format `md` to the library actor.
3. Check the rule is placed on the machine that holds the Discord actor.
4. Import and enable it with the CLI, dry run first.

## How it behaves

- Enabling the rule is also what makes the gateway record its channel:
  messages from channels no enabled `discord.*` rule selects are never
  stored, and Discord events expire after 30 days.
- The text comes from `discord.read`, so a message longer than the event's
  2000 characters is filed whole. A message with only an attachment is filed
  with no text; filing the attachment itself is planned (Release 2), as are
  reactions, commands and the scheduled sweep (Release 3).
- If both bots are in the channel, each records its own event and the rule
  runs twice; both runs render the same page, so the library keeps one item
  (the second push reports `created: false`).

Deleting the Discord message later leaves the library item; archive it by hand.
