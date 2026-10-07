# Actors

An **actor** is who or what performs work: an agent, a human, a runner, a
service, or an **app**. An app is a credentialed integration with an outside
surface. Actors are not a stage in a rule; a rule's action names one in
`params.actor`, and the action runs through it.

This folder has one guide per **app** actor, written for a first-time setup.
Each guide assumes you are signed in to that surface with admin rights and that
nothing else is prepared: no secrets, no webhook, no actor.

| Surface | Guide | Receives events by | Acts with |
|---|---|---|---|
| GitHub | [github.md](github.md) | App webhook to `POST /hooks/github` | `github.comment` |
| Discord | [discord.md](discord.md) | Gateway listener on one engine node | `discord.message` |
| Jira | [jira.md](jira.md) | System webhook to `POST /hooks/jira` | `jira.comment` |

## Before you start (all guides)

You need:

- **A shell on the serving host**: the machine that runs the culture-rules API
  (`culture-rules-api.service`) and its engine node (`culture-rules-node.service`).
  The app actors live on this machine. On rules.culture.dev that is `spark`.
- **The `grant` CLI on that host** (the agentculture secrets manager). Check it
  with `grant doctor`. Every secret below is created there, under the same user
  the services run as.
- **Admin in the culture-rules editor**. You create the actor in the **Actors**
  tab, so sign in at the editor's address as an admin.
- **Cloudflare Zero Trust admin** for the GitHub and Jira guides, which need a
  public bypass path for their webhooks. Discord does not.

## How secrets work here

These rules apply in every guide.

- **Seal each value in grant.** The actor stores only the reference
  `grant:<NAME>`. Typing a literal value into an actor is refused with
  `secret_literal`.
- **Seal secrets `--hidden`.** Then `grant get` refuses to print them. The flag
  cannot be changed after the secret is created.
- **Pasting a value you were given** (a token, a private key):

  ```bash
  grant set <NAME> - --hidden     # paste the value, then press Enter and Ctrl-D
  grant set <NAME> - --hidden < file.pem
  ```

- **Creating a random value** (webhook secrets and tokens):

  ```bash
  grant generate <NAME> --hidden --bytes 32 --encoding hex
  ```

- **Seeing a hidden value once**, to paste it into a web form. grant injects it
  into a one-off command in your own terminal:

  ```bash
  grant run --inject V=<NAME> -- sh -c 'printf "%s\n" "$V"'
  ```

  Copy it, then clear the terminal. Never paste it into a chat, issue or
  commit.
- **The services read hidden secrets only when grant injects them at start.**
  Each service unit injects every secret it uses as
  `CULTURE_RULES_SECRET_<NAME>`. `<NAME>` is the grant name upper-cased, with
  any character other than a letter, digit or `_` turned into `_`. A
  `grant:<NAME>` reference resolves to that variable. To add one:

  ```bash
  systemctl --user edit --full culture-rules-api     # or culture-rules-node
  # in ExecStart, after "grant run", add:
  #   --inject CULTURE_RULES_SECRET_<NAME>=<NAME>
  systemctl --user restart culture-rules-api
  ```

  A node installed with `deploy/node/install.sh` takes `--secret <NAME>`
  (repeatable) instead of a hand edit.

## What every app actor has in common

- **Kind and surface.** `kind: "app"` and `params.surface` is `github`, `jira`
  or `discord`. The shape is defined in `culture_rules/model/app_actor.py`.
- **Declared events.** `params.events` is the allow-list of event types the
  actor records; anything else is ignored. `params.actions` lists what it
  performs.
- **`machine` is the serving host.** A rule action naming the actor then runs
  there, where the secrets are injected. Only that machine's node holds a
  Discord gateway connection.
- **`params.self_identity` is the app's own login.** Events it authored are
  tagged `self_authored`, and they fire a rule only when the trigger sets
  `include_self`. With a `self_identity` set, every other event carries
  `self_authored: false`, which is how a human push resets a rule's attempt
  budget.

## Using an app in a rule

Give the rule an **Event** trigger with a type such as `github.pr.opened`,
`discord.message.created` or `jira.issue.created`. Narrow it with a condition
on the event data, such as `data.repository`, `data.channel_id` or
`data.project`. The action names the actor in `params.actor`.

Actors can also be written with the CLI, using an admin service token. Writes
are dry-run by default:

```bash
culture-rules actors create --body @actor.json          # dry run
culture-rules actors create --body @actor.json --apply
```
