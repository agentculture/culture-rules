# Actors

An **actor** is who or what performs work: an agent, a human, a runner, a
service, or an **app**. An app is a credentialed integration with an outside
surface. Actors are not a stage in a rule; a rule's action names one in
`params.actor`, and the action runs through it.

This folder documents how each **app** actor is set up end to end. That means
creating or inviting the app on the surface, sealing its secrets, defining the
actor, letting the services use the secrets, and verifying it live.

| Surface | Doc | Receives events by | Acts with |
|---|---|---|---|
| GitHub | [github.md](github.md) | App webhook to `POST /hooks/github` | `github.comment` |
| Discord | [discord.md](discord.md) | Gateway listener on one engine node | `message` |
| Jira | [jira.md](jira.md) | System webhook to `POST /hooks/jira` | `jira.comment` |

## What every app actor has in common

- **Kind and surface.** The actor is `kind: "app"`, and `params.surface` is one
  of `github`, `jira` or `discord`. The shape is defined in
  `culture_rules/model/app_actor.py`.
- **Declared events and actions.** `params.events` is the allow-list of event
  types the receiver records; anything else answers `202 ignored`.
  `params.actions` lists the action kinds the actor performs.
- **Secrets are references, never values.** Every secret-looking key under
  `params.connection` holds `grant:<NAME>`, and a literal is refused on save
  with `secret_literal`. Seal the values with `grant set <NAME> - --hidden`.
- **Hidden secrets are injected when a service starts.** `grant get` refuses a
  `--hidden` secret, so each service unit that needs one injects it as
  `CULTURE_RULES_SECRET_<NAME>`, for example
  `--inject CULTURE_RULES_SECRET_RULES_DISCORD_BOT_TOKEN=RULES_DISCORD_BOT_TOKEN`.
  A `grant:<NAME>` reference resolves to that variable first. Engine nodes take
  `deploy/node/install.sh --secret <NAME>`. The API unit lists the injections in
  its `ExecStart`; see `docs/operations/rules-culture-dev.md`.
- **Pin the actor to the machine that holds its secrets.** Set `machine` on the
  actor. A rule action naming that actor then runs on that machine, and only
  that machine's node holds a Discord gateway lease. On rules.culture.dev all
  app actors live on `spark`.
- **The bot's own identity.** `params.self_identity` is the app's own login: the
  GitHub bot login, the Discord bot user id, or the Jira accountId. Events it
  authored are tagged `self_authored` and never fire a rule unless the trigger
  sets `include_self`.

## Using an app in a rule

Rules react to these actors' events with an **Event** trigger and a type, for
example `github.pr.opened`, `discord.message.created` or `jira.issue.created`.
Narrow the trigger with a condition on the event data, such as
`data.repository`, `data.channel_id` or `data.project`. The action then names
the actor in `params.actor`.

Create or edit actors in the editor's **Actors** tab, or with the CLI. Writes
are dry-run by default:

```bash
culture-rules actors create --body @actor.json          # dry run
culture-rules actors create --body @actor.json --apply
culture-rules actors update <id> --body @actor.json --apply
```
