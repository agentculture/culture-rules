# A green PR merge prompt (an example)

When a PR's checks settle green, post a prompt with a **Merge** button in a Discord
channel; the operator's click squash-merges that PR at the commit the prompt was for.
This guide builds that flow from generic pieces (spec
`docs/specs/2026-10-31-green-pr-merge-prompt.md`, plan
`docs/plans/2026-10-31-green-pr-merge-prompt.md`). culture-rules ships the pieces only:
`github.pr`, `github.merge`, the `buttons` of `discord.message` (with a stored
`value`), the `discord.button.clicked` event with its `custom_id` filter and `value`,
and the click answer. The two rules and the two workflows below are **configuration,
created live with `--apply` and never committed**, so everything here is an example:
every id is a placeholder in backticks.

The pieces are documented in [the GitHub actor guide](../actors/github.md#reading-and-merging-a-pr)
and [the Discord actor guide](../actors/discord.md#buttons).

## Before you start

- A GitHub app actor (`github-app` below) whose `connection.repos` lists the repos, with
  Pull requests and Contents write on the App. The merge runs only as an App actor whose
  digest is pinned in `culture_rules/actors/trusted.py` (`TRUSTED_ACTOR_DIGESTS`), as a
  push does; `github-app` is pinned, so another actor, or `github-app` with changed
  security fields, fails `actor_not_trusted`. Adding a repo keeps it pinned.
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
bot's own post writes the value, and a clicker cannot change it. The stored button
also records who posted it, `posted_by: {run_id, workflow_id}` (the run whose step
posted it), which the click's event carries as `data.posted_by`. `github.merge`
merges only a button whose posting run's workflow is the pinned `merge_prompt`
workflow (step 3), so a button another workflow posts with the same `merge`
prefix merges nothing. The value is
kept for 30 days; a click after that carries `value: null`, so the merge has no
`number` or `sha` and `github.merge` refuses it without merging.

`tests/rules/test_green_merge_flow.py` drives this composition end to end on doubles of
GitHub and Discord, with the rules built in the test. A live setup still depends on your
repositories' rules (branch protection, required reviews), so try it on a throwaway repo
first (see [Verify](#4-verify)).

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
- **Workflow `merge_prompt`** (its id must be exactly `merge_prompt`, see
  [step 3](#3-pin-the-merge-workflow)): inputs `repository`, `number` (integer),
  `head_sha`, `title`, `author` and `url`, mapped from `trigger.data.repository`,
  `trigger.data.number`, `trigger.data.head_sha`, `trigger.data.title`,
  `trigger.data.author` and `trigger.data.url`. One step, `post`, a built-in action
  step (`config.builtin: action`) of kind `discord.message`, its inputs edges from the
  workflow inputs. The text and the button's `value` both come from those inputs:

  ```json
  {"kind": "discord.message", "params": {
    "actor": "<discord-actor-id>",
    "channel": "<channel-id>",
    "text": "Green: {{ inputs.repository }}#{{ inputs.number }} {{ inputs.title }} by {{ inputs.author }}\n{{ inputs.url }}\nhead {{ inputs.head_sha }}",
    "buttons": [{"label": "Merge", "style": "success", "custom_id": "merge",
                 "value": {"repo": "inputs.repository",
                           "number": "inputs.number",
                           "sha": "inputs.head_sha"}}]
  }}
  ```

  The engine resolves the three references before the post, so the stored value
  holds the PR's repo, number and head sha. The step's outputs are `message_id`
  and `custom_ids` (here one id, `merge:<token>`).
- **Action:** `noop`. Post the button from the workflow's step, never from the
  rule's action: the step is part of the pinned digest, the rule's action is not.

If you want the title or the draft flag read fresh rather than from the event, add a
`github.pr` step (`actor`, `repo`, `number`) before `post` and build the text from its
outputs; `head_repo != base_repo` there is also how it tells a fork.

## 2. Rule 2: the click

- **Trigger:** event `discord.button.clicked`, with params `users:
  ["<operator-discord-id>"]`, `channels: ["<channel-id>"]` and `custom_id: "merge:"`
  (case-sensitive; ending in `:`, it picks every id that starts `merge:`, so every
  posted `merge:<token>` and never `merge-later:<token>`). `users` is what limits who
  may merge; a click by anyone else is answered privately "This button is not set up
  here.", records nothing and leaves the Merge button enabled for you. Give other
  flows' buttons a different `custom_id`.
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

Going live pins **two** digests. `github.merge` runs only in a workflow trusted in the
`merge` role, and only on a button posted by a run of a workflow trusted in the
`merge_prompt` role. Both roles ship with no digest (`culture_rules/actors/trusted.py`,
`ROLE_MERGE` and `ROLE_MERGE_PROMPT`, empty in `TRUSTED_WORKFLOWS` and
`TRUSTED_WORKFLOWS_V2`). Until a release pins both live workflows' digests, every merge
fails `workflow_not_trusted` (the merge workflow) or `prompt_not_trusted` (the prompt
workflow). This is the same digest-pinning as the PR fixer's roles, described in
[`pr-fixer.md`](pr-fixer.md#trusted-workflows-d20-round-2-d21-roles). For each of the
two workflows:

1. Import it with its id, `merge_prompt` or `merge`, with `--apply`
   (`culture-rules workflows create` or `import`). The id must be the role's name: the
   editor's "Save a trusted workflow?" question matches by id.
2. Export it (`culture-rules workflows export`) and print its v2 digest, which leaves out
   the version, description and notes:

   ```bash
   python -m culture_rules.actors.trusted workflow-v2 < merge_prompt.json
   python -m culture_rules.actors.trusted workflow-v2 < merge.json
   ```

3. Add each printed `sha256:...` to its role of `TRUSTED_WORKFLOWS_V2` in
   `culture_rules/actors/trusted.py` (`merge_prompt`'s to `ROLE_MERGE_PROMPT`, `merge`'s
   to `ROLE_MERGE`), in one PR (which bumps the version as every PR does).
4. Release it and upgrade every node (`culture-rules rollout`, see
   [`rollout.md`](rollout.md)).

Any later change to either live workflow (a step, a port, a param) gives it a new digest
and it is untrusted again until a new pin ships: both changes need a re-pin. The run still
starts, and `github.merge` refuses it (a changed prompt workflow refuses the buttons its
new runs post). Keep the old digest while runs pinned to it may be in flight. Saving the
original definition back restores the trust.

## 4. Verify

- Enable both rules only after the release (both pins) is on every node.
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
