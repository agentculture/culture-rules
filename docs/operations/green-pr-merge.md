# A green PR merge prompt (an example)

When a PR's checks settle green, post a prompt with a **Merge** button in a Discord
channel; the operator's click squash-merges that PR at the commit the prompt was for.
This guide builds that flow from generic pieces (spec
`docs/specs/2026-10-31-green-pr-merge-prompt.md`, plan
`docs/plans/2026-10-31-green-pr-merge-prompt.md`). culture-rules ships the pieces only:
`github.pr`, `github.merge`, the `buttons` of `discord.message`, the
`discord.button.clicked` event with its `custom_id` filter, and the click answer. The
two rules and the workflows below are **configuration, created live with `--apply` and
never committed**, so everything here is an example: every id is a placeholder in
backticks.

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

## The gap: a click cannot parse its `custom_id`

A button's `custom_id` is at most 100 characters, so it can hold `repo`, `number` and
a 40-character `sha` (for example `merge:<owner>/<repo>#<number>@<sha>`), but **no
existing step can split such a string back apart**: a reference or `{{ }}` template
only substitutes a whole value or pastes values in, there is no substring or split, and
`matches` only tests. `github.merge` needs `number` and `sha` as separate inputs, so a
packed `custom_id` is of no use to the click workflow.

What the pieces can do is carry a **lookup key** instead. The prompt rule's action may
read its own run's id (`run.id`), so the button's `custom_id` is that run id, and the
click workflow reads the prompt run's recorded step outputs back with the `run.read`
builtin (`run_id` input). That is the route below. The same action text can still show
the repo, number and sha to the operator; only the button carries the key. A run id is
`run-` and 32 hex digits (36 characters).

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
- **Workflow `green-pr-facts`:** inputs `repository` (string), `number` (integer) and
  `head_sha` (string), mapped from `trigger.data.repository`, `trigger.data.number` and
  `trigger.data.head_sha`. One step, `facts`, a code step with `config.builtin:
  select`, `config.sources: {"pr": "pr"}` and `config.ports: ["repository", "number",
  "head_sha"]`, so its inputs are `pr_repository`, `pr_number` and `pr_head_sha` (each
  an edge from the workflow input) and its outputs are the three values. It exists so
  the run records them for the click workflow.
- **Action:** `discord.message`. The text comes from the trigger's PR facts, and the
  one button's `custom_id` is the whole-value reference `{"$ref": "run.id"}`:

  ```json
  {"kind": "discord.message", "params": {
    "actor": "<discord-actor-id>",
    "channel": "<channel-id>",
    "text": "Green: {{ trigger.data.repository }}#{{ trigger.data.number }} {{ trigger.data.title }} by {{ trigger.data.author }}\n{{ trigger.data.url }}\nhead {{ trigger.data.head_sha }}",
    "buttons": [{"label": "Merge", "style": "success", "custom_id": {"$ref": "run.id"}}]
  }}
  ```

If you want the title or the draft flag read fresh rather than from the event, add a
`github.pr` step to the workflow (`actor`, `repo`, `number`) and use its outputs;
`head_repo != base_repo` there is also how it tells a fork.

## 2. Rule 2: the click

- **Trigger:** event `discord.button.clicked`, with params `users:
  ["<operator-discord-id>"]`, `channels: ["<channel-id>"]` and `custom_id: "run-"`
  (a prefix, case-sensitive). `users` is what limits who may merge; a click by anyone
  else is answered privately "This button is not set up here." and starts nothing.
  Use a different prefix per flow if other buttons also carry run ids.
- **Workflow `merge`** (its id must be exactly `merge`, see
  [step 3](#3-pin-the-merge-workflow)): inputs `prompt_run` (string), mapped from
  `trigger.data.custom_id`. Two steps:
  1. `prompt`, a code step with `config.builtin: run.read`, `config.step: "facts"` and
     `config.fields: ["repository", "number", "head_sha"]`, its `run_id` input an edge
     from `prompt_run`. It reads the prompt run's step outputs from the store and fails
     `run_not_found` or `run_not_succeeded` for a key that is not a finished run.
  2. `merge`, a built-in action step (`config.builtin: action`) of kind `github.merge`
     with params `actor: "github-app"`, `repo: "inputs.repository"`, `number:
     "inputs.number"`, `sha: "inputs.head_sha"` and `delete_branch: true`, its inputs
     edges from `prompt`'s outputs.

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
