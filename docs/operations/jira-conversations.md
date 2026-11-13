# A conversation with an agent in a Jira issue

A person and an agent talk in the comments of one Jira issue. Each comment from
the person starts one round: the agent reads the whole thread, thinks (and may
keep state on disk), and answers with one comment. This guide describes the
generic shape, built only from surfaces that exist: the Jira actor
([../actors/jira.md](../actors/jira.md)), a bridge agent step, a registered
runner command and ordinary rule fields. Nothing here is code. The rules and
workflow are configured live, never committed.

## The shape

Two rules and one workflow:

1. **Opening rule.** Trigger `jira.issue.created`, with the `users` filter set to
   the people allowed to start a conversation. It starts the workflow for the
   new issue.
2. **Reply rule.** Trigger `jira.comment.created`, with `users` set the same
   way. It starts the same workflow for the issue the comment is on.
3. **The workflow**, one round:

   ```text
   jira.read -> bridge agent step -> registered command -> jira.comment
   ```

   - `jira.read` reads the issue (`actor`, `issue` from the trigger's
     `data.key`): `summary`, `description`, `comments`.
   - The bridge agent step takes that thread as its prompt and answers. It is an
     ordinary agent step; see [../actors/llm.md](../actors/llm.md) and the
     agent actor guides for the bridge.
   - The registered command writes the round's durable state (below).
   - `jira.comment` posts the agent's answer to the issue
     (`{actor, issue, body}`).

Both rules name the same workflow, so the reply rule's only difference is its
trigger. Which of the two fired is visible in the trigger; the workflow reads
the issue by key either way.

## One round at a time

Give both rules the same `concurrency_key`:

```text
jiraconv:{trigger.data.key}
```

Only `{trigger.<path>}` placeholders are allowed, and `data.key` is on every
Jira event. The resolved key is global, so the prefix keeps these runs apart
from any other rule's. While a round runs, a new comment on the same issue does
not start a second run: it waits as one pending firing, and when several
arrive, the newest one fires once the run ends. Replies sent during a round
therefore **coalesce**, which is fine because every round re-reads the whole
thread with `jira.read` and does not depend on which comment fired it. Build
the round on `jira.read`, never on the event: the event's `comment_body` is cut
at 500 characters.

### Capping rounds

Set `max_attempts` on both rules (the smallest one among rules sharing the key
applies). After that many admitted runs for a key, further firings are skipped.
That caps the rounds per budget, so one issue cannot keep an agent busy without
end. The budget starts anew when the key's story starts anew: a rule with
`resets_attempt_budget: true` resets the key's budget, once per (key, event),
before its firing is admitted. Setting it on the opening rule (trigger
`jira.issue.created`) gives each new issue a fresh budget. A reply rule should
not set it, or every reply would reset the cap. `resets_attempt_budget` needs a
`concurrency_key`. Leave `counts_toward_budget` true; set it false only on a
follow-up rule that should share the key's one active run without spending the
budget (it is refused together with `max_attempts` on the same rule).

### Saying why a round failed

Give the reply rule an `on_failure` action, a `jira.comment` on the same issue
whose body names the error:

```json
{"kind": "jira.comment", "params": {
  "actor": "<jira-actor-id>",
  "issue": "trigger.data.key",
  "body": "This round failed ({{ run.error.code }}): {{ run.error.message }}"
}}
```

(the same pattern as [green-pr-merge.md](green-pr-merge.md)). Without it a
failed round is silent in the issue.

## The bot must not answer itself

The agent's answer is a comment, and a comment fires `jira.comment.created`.
Two things stop the loop, and you want both:

- Set `params.self_identity` on the Jira actor to the service account's
  accountId. **`self_authored` is set only when `self_identity` is set**; without
  it the bot's own comments carry no mark. An event the actor wrote itself
  does not fire a trigger unless the trigger sets `include_self`; leave
  `include_self` off.
- Set `users` on both triggers to the accountIds of the people allowed to talk to
  the agent. The filter never admits an event with no author or a self-authored
  one, and never the actor's own account.

If the service account is shared with another system, its comments from that
system are also the bot's: they are marked `self_authored` here, and `users`
keeps them out too.

## Where state lives

A round starts with an agent and no memory except what the thread and the disk
hold. Keep durable state (notes, drafts, a work-in-progress file) in a
directory per issue, written by a **registered command** (a runner actor's
command, as in [govee-lamp.md](govee-lamp.md)), never by the engine:

- The command's argument for the issue key has the runner argument type
  `segment`: one safe path segment (no `/`, `\`, NUL or control character, none
  of `, ( ) * ? [ ] { }`, not `.` or `..`, no leading `-`, at most 128
  characters), so the key cannot walk out of the directory. See [action-catalogue.md](action-catalogue.md).
- The command builds the absolute path itself from a fixed root plus that
  segment, for example `<state root>/<issue key>/`. The root is the command's
  own configuration, not an argument.
- Pass the issue key from the trigger (`trigger.data.key`) as the argument, so a
  rule's data never names a path.

### An agent that works in that directory

By default the bridge agent step reads and answers only. To let it edit files in
the issue's directory, the Jira conversation's agent actor opts in with
`params.sandbox: operate` (only on the actor; a step cannot raise itself to
operate), and the step supplies a `working_path` input, an absolute path under
that root. A `working_path` is accepted only under `operate`, and only already
normalised (no trailing slash, `.` part, doubled or leading `//` slash, or `..`)
with no whitespace, backslash, NUL or any of `, ( ) * ? [ ] { }`
(`working_path_invalid` otherwise, before the bridge is called). **This needs a
cultureagent bridge that implements the `operate` role**, which ships separately;
until the bridge in use does, such a step is refused. The directory the agent
works in is the same one the registered command writes.

## Security notes

- The people in `users` are trusted to steer the agent. Everything in a thread
  they did not write is untrusted. Jira Cloud lets anyone with comment access
  to the project comment, so `users` on the trigger is not enough by itself:
  have the workflow filter `jira.read`'s `comments` by `author` before they
  reach the agent, with the generic builtin `list.filter`
  ([building-blocks.md](building-blocks.md)). Its `items` input is the
  `comments` output of `jira.read`, any other input is a context value read as
  `{"var": "<input name>"}`, and its `where` config reads each comment's fields
  with `{"item": "<path>"}`. To keep the comments whose author is in an
  `answerers` input (a list of accountIds):

  ```json
  {"op": "in", "value": {"item": "author"}, "items": {"var": "answerers"}}
  ```

  The step's `items` output goes to the agent, so it sees only the allowlisted
  authors' text.
- `jira.read` and `jira.comment` allow every project when
  `connection.projects` is absent or empty. Set `projects` on the actor when
  the conversation should live in some projects only.
- An operating agent runs commands. Its Bash patterns are the residual risk:
  they limit what it may run, not what a permitted command can do. Pin the
  agent to the smallest set it needs and keep the working directory to one
  issue's state.
- The grant, the webhook token and the Access bypass are the Jira guide's
  business; nothing here widens them.

## An example: a spec-writing tool

The shape above is the whole mechanism; what the agent is for is configuration.
As one example, configured live on a host and not part of this repo's code, the
agent can be told to run a spec-writing CLI: devague. The agent's brief says
to take the person's comment as the next move in a spec session, the registered
command's directory holds the session's files for that issue (under the state root, one directory per issue key), and the agent's answer is the next question or the summary of
the frame so far. The opening rule starts a session from a new issue's
description; each reply continues it. None of it is in the engine: swap the
brief and the command and the same rules run a different conversation.
