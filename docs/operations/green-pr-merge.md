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
bot's own post writes the value, and a clicker cannot change it. The prompt's
button also asks for a **one-time code** (`code: true`, [step 3](#3-the-one-time-code)),
stored with the value and carried by the click as `data.code`; `github.merge` merges
only with it. The value and the code are kept for 30 days; a click after that carries
neither, and `github.merge` refuses it without merging.

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
- **Workflow `merge_prompt`** (any id; nothing is pinned): inputs `repo`, `number` (integer) and `sha`,
  mapped from `trigger.data.repository`, `trigger.data.number` and
  `trigger.data.head_sha`. The settled event carries no title, link or author name, so
  the workflow reads the PR first. Two built-in action steps (`config.builtin: action`):
  - `pr`, kind `github.pr` (`actor: "github-app"`, `repo: "inputs.repo"`,
    `number: "inputs.number"`), its inputs edges from the workflow's `repo` and
    `number`, outputs `title`, `url` and `author`;
  - `post`, kind `discord.message`, with inputs `repo`, `number` and `sha` from the
    workflow inputs and `title`, `url` and `author` from `pr`:

  ```json
  {"kind": "discord.message", "params": {
    "actor": "<discord-actor-id>",
    "channel": "<channel-id>",
    "text": "{{inputs.repo}}#{{inputs.number}} is green: {{inputs.title}} by {{inputs.author}} {{inputs.url}}",
    "buttons": [{"label": "Merge", "style": "success", "custom_id": "merge",
                 "value": {"repo": "inputs.repo",
                           "number": "inputs.number",
                           "sha": "inputs.sha"},
                 "code": true}]
  }}
  ```

  The engine resolves the three references before the post, so the stored value
  holds the PR's repo, number and the settled head sha (not `pr`'s head, which may have
  moved on). The step's outputs are `message_id` and `custom_ids` (here one id,
  `merge:<token>`). This is the workflow `tests/rules/test_green_merge_flow.py` builds.
- **Action:** `noop`.

## 2. Rule 2: the click

- **Trigger:** event `discord.button.clicked`, with params `users:
  ["<operator-discord-id>"]`, `channels: ["<channel-id>"]` and `custom_id: "merge:"`
  (case-sensitive; ending in `:`, it picks every id that starts `merge:`, so every
  posted `merge:<token>` and never `merge-later:<token>`). `users` is what limits who
  may merge; a click by anyone else is answered privately "This button is not set up
  here.", records nothing and leaves the Merge button enabled for you. Give other
  flows' buttons a different `custom_id`.
- **Workflow `merge`** (any id; nothing is pinned): inputs `repository` (string), `number`
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

## 3. The one-time code

Nothing is pinned for this flow: both workflows are configuration, so moving the prompt
to another channel, changing its text or adding a step is an edit (deviation d3 of the
plan). What gates the merge is your click and the button's code:

- the prompt's `discord.message` issues the code when it stores the button (`code: true`),
  bound to the value it stores: the PR's repo, number and sha;
- the gateway copies it onto the click's event (`data.code`), as it copies the value; a
  clicker cannot supply one;
- `github.merge` reads it from the stored click and merges only when it was issued
  (`code_not_found`), belongs to the clicked button and holds this merge's repo, number
  and sha (`code_mismatch`), and is unused (`code_used`); it then uses it up, by
  compare-and-set. The same run's retry passes; a second click, a replayed click, or a
  button another workflow posted without a code merges nothing.

A merge that merged nothing (a missing App secret, a closed PR, a moved head, a merge
GitHub refused, e.g. for branch protection) hands the code back, so you can click the same
button again once the cause is fixed.

**Upgrading from 0.39:** buttons posted before 0.40.0 have no code, and their clicks fail
`code_not_found`. Add `code: true` to the live prompt's button (an edit) during the
rollout; a PR prompted before it needs a fresh green run to be prompted again.

Who may click is rule 2's `users` filter. Keep it to yourself: with no workflow pinned,
any workflow that posts a coded `merge` button for a PR is offered to you, and your click
is the approval.

## 4. Verify

- Enable rule 2 before rule 1, so the channel is selected before the first prompt.
- On a throwaway repo, open a PR and let its checks go green: one prompt should appear
  in `<channel-id>` within about a minute, with the PR's repo, number, title, author and
  link, and one Merge button.
- Click as `<operator-discord-id>`: the message's button disables at once, the PR is
  squash-merged at the prompted commit, its branch is deleted, and you get a private
  reply. `culture-rules runs list` shows both runs, and the merge run's `merge` step
  holds `merged: true` and the merge commit.
- Click as another user, or push a new commit to a second prompted PR before clicking:
  the first is refused privately by the filter, the second fails `head_moved` and
  nothing is merged. A second click on a merged prompt fails `code_used`.
- A direct `culture-rules workflows run merge` fails `not_started_by_click`, which is
  the fence: no agent, schedule or API call can merge.
