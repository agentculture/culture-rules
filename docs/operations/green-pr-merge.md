# A green PR merge prompt (an example)

When a PR's checks settle green, post a prompt with a **Merge** button in a Discord
channel; the operator's click squash-merges that PR at the commit the prompt was for.
This guide builds that flow from generic pieces (spec
`docs/specs/2026-10-31-green-pr-merge-prompt.md`, plan
`docs/plans/2026-10-31-green-pr-merge-prompt.md`). culture-rules ships the pieces only:
`github.pr`, `github.merge`, the `buttons` of `discord.message` (with a stored
`value`), the `discord.button.clicked` event with its `custom_id` filter and `value`,
and the click answer. The two rules and the workflow below are **configuration,
created live with `--apply` and never committed**, so everything here is an example:
every id is a placeholder in backticks.

The pieces are documented in [the GitHub actor guide](../actors/github.md#reading-and-merging-a-pr)
and [the Discord actor guide](../actors/discord.md#buttons).

## Before you start

- A GitHub app actor (`github-app` below) whose `connection.repos` lists the repos, with
  Pull requests and Contents write on the App.
- A Discord app actor (`<discord-actor-id>`) that lists `discord.button.clicked` in
  `params.events`, in the server and channel `<channel-id>`.
- The channel must be selected by an enabled rule, or the click is never recorded
  (rule 2's `channels` does that).
- Your Discord user id, `<operator-discord-id>`, for rule 2's `users` filter.

## The button carries its meaning in the store

The click workflow needs the PR's `repo`, `number` and `sha` as separate values.
They are not packed into the button's `custom_id`: no step can split a string,
and the id would be capped at 100 characters. Instead the prompt's button carries
a `value`, a JSON object built from the PR facts. The `discord.message` port
posts the button with the id `merge:<token>` (the given `custom_id` `merge`, a
`:` and a random token), and stores the value under that id in `discord_buttons`
before the message goes out. When the button is clicked, the gateway reads the
stored value and puts it in the click's event as `data.value`, so the click
workflow reads `trigger.data.value.repo`, `.number` and `.sha` directly. Only the
bot's own post writes the value, and a clicker cannot change it. The value is
kept for 30 days; a click after that carries `value: null`, so the merge has no
`number` or `sha` and `github.merge` refuses it without merging.

This composition is built only from documented pieces; no test drives the whole flow,
so try it on a throwaway repo first (see [Verify](#4-verify)).

## 1. Rule 1: the prompt

- **Trigger:** event `github.pr.checks_settled`. It already fires once per head SHA,
  so a second delivery for the same commit does not prompt twice. (`keyset.filter` and
  `keyset.add` cannot gate a rule's action, so they are not needed here.)
- **Condition:** green, not a draft, not a fork:

  ```json
  {"op": "and", "args": [
    {"op": "compare", "cmp": "==", "left": {"field": "data.conclusion"}, "right": {"literal": "success"}},
    {"op": "compare", "cmp": "==", "left": {"field": "data.draft"}, "right": {"literal": false}},
    {"op": "compare", "cmp": "==", "left": {"field": "data.head_repo"}, "right": {"field": "data.base_repo"}}
  ]}
  ```

  A comparison on a missing fact is false, so a PR whose facts are missing is never
  prompted.
- **Action:** `discord.message`, with no workflow. The text and the button's `value`
  both come from the trigger's PR facts:

  ```json
  {"kind": "discord.message", "params": {
    "actor": "<discord-actor-id>",
    "channel": "<channel-id>",
    "text": "Green: {{ trigger.data.repository }}#{{ trigger.data.number }} {{ trigger.data.title }} by {{ trigger.data.author }}\n{{ trigger.data.url }}\nhead {{ trigger.data.head_sha }}",
    "buttons": [{"label": "Merge", "style": "success", "custom_id": "merge",
                 "value": {"repo": "trigger.data.repository",
                           "number": "trigger.data.number",
                           "sha": "trigger.data.head_sha"}}]
  }}
  ```

  The engine resolves the three references before the post, so the stored value
  holds the PR's repo, number and head sha. The action's outputs are `message_id`
  and `custom_ids` (here one id, `merge:<token>`).

If you want the title or the draft flag read fresh rather than from the event, give
the rule a workflow with a `github.pr` step (`actor`, `repo`, `number`) and build the
text and the value from its outputs (`workflow.outputs.<name>`); `head_repo !=
base_repo` there is also how it tells a fork.

## 2. Rule 2: the click

- **Trigger:** event `discord.button.clicked`, with params `users:
  ["<operator-discord-id>"]`, `channels: ["<channel-id>"]` and `custom_id: "merge:"`
  (a prefix, case-sensitive). `users` is what limits who may merge; a click by anyone
  else is answered privately "This button is not set up here." and starts nothing.
  Give other flows' buttons a different `custom_id`.
- **Workflow `merge`** (its id must be exactly `merge`, see
  [step 3](#3-pin-the-merge-workflow)): inputs `repository` (string), `number`
  (integer) and `head_sha` (string), mapped from `trigger.data.value.repo`,
  `trigger.data.value.number` and `trigger.data.value.sha`. One step, `merge`, a
  built-in action step (`config.builtin: action`) of kind `github.merge` with params
  `actor: "github-app"`, `repo: "inputs.repository"`, `number: "inputs.number"`,
  `sha: "inputs.head_sha"` and `delete_branch: true`, its inputs edges from the
  workflow inputs.

  Workflow outputs `merged`, `sha` and `branch_deleted` come from the `merge` step.
- **Action:** `discord.reply` answers the clicker privately with the outcome, using the
  click's kept token (15 minutes):

  ```json
  {"kind": "discord.reply", "params": {
    "actor": "<discord-actor-id>",
    "event_id": "trigger.id",
    "text": "Merged as {{ workflow.outputs.sha }}; branch deleted: {{ workflow.outputs.branch_deleted }}."
  }}
  ```

  and `on_failure`, the same kind, with the text `Not merged ({{ run.error.code }}):
  {{ run.error.message }}` (for example `head_moved`, `pr_not_open`, `http_409`).

The click itself already turned the button into a disabled one, so a prompt is clicked
once. `github.merge` re-reads the PR and merges only at `sha`: a commit pushed after
the prompt is refused and nothing newer is ever merged.

## 3. Pin the merge workflow

`github.merge` runs only in a workflow trusted in the `merge` role, and that role ships
with no digest (`culture_rules/actors/trusted.py`, `ROLE_MERGE`, empty in
`TRUSTED_WORKFLOWS` and `TRUSTED_WORKFLOWS_V2`). Until a release pins the live
workflow's digest, every merge fails `workflow_not_trusted`. This is the same
digest-pinning as the PR fixer's roles, described in
[`pr-fixer.md`](pr-fixer.md#trusted-workflows-d20-round-2-d21-roles):

1. Import the workflow with its id `merge`, with `--apply`
   (`culture-rules workflows create` or `import`). The id must be `merge`: the editor's
   "Save a trusted workflow?" question matches by id.
2. Export it (`culture-rules workflows export`) and print its v2 digest, which leaves out
   the version, description and notes:

   ```bash
   python -m culture_rules.actors.trusted workflow-v2 < merge.json
   ```

3. Add the printed `sha256:...` to the `merge` role of `TRUSTED_WORKFLOWS_V2` in
   `culture_rules/actors/trusted.py`, in a PR (which bumps the version as every PR
   does).
4. Release it and upgrade every node (`culture-rules rollout`, see
   [`rollout.md`](rollout.md)).

Any later change to the live workflow (a step, a port, a param) gives it a new digest and
it is untrusted again until a new pin ships; the run still starts, and `github.merge`
refuses it. Keep the old digest while runs pinned to it may be in flight. Saving the
original definition back restores the trust.

## 4. Verify

- Enable both rules only after the release is on every node.
- On a throwaway repo, open a PR and let its checks go green: one prompt should appear
  in `<channel-id>` within about a minute, with the PR's repo, number, title, author and
  link, and one Merge button.
- Click as `<operator-discord-id>`: the message's button disables at once, the PR is
  squash-merged at the prompted commit, its branch is deleted, and you get a private
  reply. `culture-rules runs list` shows both runs, and the merge run's `merge` step
  holds `merged: true` and the merge commit.
- Click as another user, or push a new commit to a second prompted PR before clicking:
  the first is refused privately by the filter, the second fails `head_moved` and
  nothing is merged.
- A direct `culture-rules workflows run merge` fails `not_started_by_click`, which is
  the fence: no agent, schedule or API call can merge.
