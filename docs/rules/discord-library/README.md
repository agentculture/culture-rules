# discord-library bundle

Files each message in a chosen Discord channel in the library as a Markdown
item. It ships **disabled**: the rule has `enabled: false`, the channel id is a
placeholder, and no step sends `visibility: public`.

| File | What it is |
|---|---|
| `rules/discord-to-library.json` | trigger `discord.message.created`, disabled |
| `workflows/discord-message-to-library.json` | `markdown.render`, then `library.push` (kind `discord`, format `md`) |

It ships no actors. It uses the Discord actor (`discord-bot`, grant
`RULES_DISCORD_BOT_TOKEN`, or `jetson-bot`, grant
`DISCORD_BOT_TOKEN_JETSON_BOT`; see
[the Discord guide](../../actors/discord.md)) and a library actor, here
`newspaper-library` from the newspaper bundle. That actor must list kind
`discord` and format `md` ([the library guide](../../actors/library.md)).

## Before enabling

1. Replace `CHANNEL_ID_PLACEHOLDER` in the rule's trigger `channels` with the
   real channel id (or a list of ids).
2. Add kind `discord` and format `md` to the library actor.
3. Check the rule is placed on the machine that holds the Discord actor.
4. Import and enable it with the CLI, dry run first.

## Not on disk yet

- `markdown.render` is built in code, but it is not registered in the node's
  runner or offered in the editor until task t8 of the plan lands. Until
  then the workflow's first step does not run.
- The `discord.read` action (full message or channel history) and the
  per-channel retention drop land with t7 and t4. This bundle uses only the
  fields the event carries (`content` is truncated).
- Reactions, attachments, commands and the scheduled sweep are planned for
  Releases 2 and 3.

Deleting the Discord message later leaves the library item; archive it by hand.
