# Green PR merge prompt

> When a PR's checks all pass in any repo the culture-rules GitHub App sees, #spark on Discord gets a prompt with a green Merge button, and the operator's click squash-merges that PR at exactly the commit that went green

## Audience

- The operator, who merges AgentCulture PRs by hand today and reads #spark on Discord

## Before → After

- Before: A green PR waits until the operator opens GitHub (or asks Claude) to squash-merge it; culture-rules has no merge kind at all, and Discord messages carry no buttons
- After: Within a minute of a PR's checks settling green, #spark shows its repo, number, title, author and link with a green Merge button; the operator's click squash-merges exactly that commit, deletes the branch, and the operator sees the result privately

## Why it matters

- Merging is the last manual hop of every PR; one click from Discord keeps the human gate while removing the trip to GitHub

## Requirements

- A new action kind `github.merge` squash-merges a PR as the GitHub App through `PUT /repos/{repo}/pulls/{number}/merge` with `sha` set to the commit the prompt was for, so a head that moved since the prompt is refused by GitHub (409) and nothing newer is ever merged
  - honesty: a test pins the merge call body: PUT /repos/{repo}/pulls/{number}/merge with sha and `merge_method` squash; a 409 (head moved) and 405 (not mergeable) fail the step without retry, and an unknown outcome is never retried blindly (`once_key`)
- Merging stays a human gate: `github.merge` runs only in a run started by a `discord.button.clicked` event from the operator, so no agent or scheduled workflow (the PR fixer included) can ever merge
  - honesty: tests show github.merge refuses (before any network call) a run not started by discord.button.clicked, a click by a user other than the operator, and a workflow not trusted in the merge role; `test_no_merge_kind_is_catalogued` is rewritten to pin exactly this fence, and the "no merge kind" docstrings say so
- `discord.message` takes an optional list of buttons (label, style, `custom_id` of at most 100 characters) and still returns the posted `message_id`
  - honesty: a test shows discord.message posts components with a success-style button and refuses a `custom_id` longer than 100 characters; a message without buttons posts the same body as today
- The Discord gateway turns a button click into a `discord.button.clicked` event (`custom_id`, `message_id`, channel, author id and name, roles) for an actor that declares it in `params.events`, acknowledges every click within 3 seconds, and a new trigger filter `custom_id` (prefix) selects the button
  - honesty: a gateway test with the discord fakes turns a component interaction into one discord.button.clicked event carrying `custom_id` and `author_id`, acknowledges it within the 3 s window even when no rule admits it, and the `custom_id` prefix filter is validated and matched like command
- After a click the clicker sees the outcome privately (merged, refused and why, or not allowed), and the prompt's button is disabled once the PR is merged
  - honesty: the click is answered with the button disabled at once, and the merge rule reply reaches the clicker through the stored interaction token within its 15 minutes
- The surfaces give a configured prompt what it needs: the repository, PR number, title, author, link, draft flag and fork status of a green PR (through a PR read or `checks_settled` enrichment), and existing building blocks (keyset.filter/add or a once key) let the configuration post once per repository and head commit
  - honesty: a test shows the PR read (or enriched `checks_settled`) yields title, url, author, draft and head/base repo for a PR; a rules test shows a keyset (or once key) configured on repository@`head_sha` drops a second success for the same commit

## Honesty conditions

- the first real green PR after rollout is prompted in #spark and merged by one operator click at the prompted sha, read back from runs list, the Discord message and the PR's merge commit
- git diff main of the delivery holds no rule or workflow for the prompt or the merge; they are created live with --apply
- the PR fixer, github.push and `checks_settle` tests pass unchanged
- the click rule's users filter names the operator's Discord user id, and the prompt posts to #spark through the discord-bot actor
- before this delivery `ACTION_KINDS` holds no merge kind (`test_no_merge_kind_is_catalogued`) and discord.message's body has no components
- an end-to-end rules test (`chain_world` style) drives `checks_settled` success -> prompt with button -> operator click -> github.merge squash at the prompted sha -> branch delete -> private reply, with fakes for GitHub and Discord
- the human gate holds: no path merges without a discord.button.clicked event from the operator (h2's refusal tests)
- the first real green PR after rollout is read back: runs list for both rules, the #spark message, the PR's squash merge commit equal to the prompted sha, and the deleted branch, filed as devague evidence
- the delivery diff holds no workflow or rule, and no flow-specific code: no #spark channel id, repository, Discord user id or prompt text appears under `culture_rules`/ or web/src (grep), and the new pieces are generic kinds, an event, a filter and params

## Success signals

- The first real green PR after rollout gets exactly 1 prompt in #spark within 60 s of `checks_settled`, one operator click merges it at the prompted sha (squash) and deletes the branch, and a click by anyone else, or on a moved head, merges nothing

## Scope / boundaries

- The two rules (the green prompt and the click-to-merge) are configured live in the store and never committed; culture-rules ships only the generic pieces (`github.merge`, buttons, the click event and filter)
- The PR fixer, `github.push` and its trust chain, and the checks settler stay unchanged
- Only the surfaces and integrations are coded (github.merge with its fence and branch delete, Discord buttons, the click event, its `custom_id` filter and click answer, and any PR read the prompt needs); the workflow itself, its prompt text, its PR filters (drafts, forks) and its once-per-commit guard are configuration built on them (operator, 2026-10-31)

## Non-goals

- No waiting or approval step in the engine: the prompt and the merge are two separate rules joined only by the click event, and the button's `custom_id` carries the repository, PR number and commit

## Scope exploration

- `s1` — `culture_rules/apps/github.py, node/actions/github.py, github_pr.py`: No merge call or port exists (apps/github.py:18, `github_pr.py`:5); GitHubPrHeadPort (node/actions/github.py ~290-345) is the deadline-bounded template and `_head_facts` already reads `head_sha`/state/merged for a pre-merge check; the App holds Contents RW + Pull requests RW (docs/actors/github.md:41), enough for merge
  - seeds: `c6`
- `s2` — `action_kinds.py docstring, tests/model/test_action_kinds.py:201`: The codebase states 'There is deliberately no merge kind: merging stays a human gate' (model/`action_kinds.py`:30, apps/github.py:18, node/actions/`github_pr.py`:5) and `test_no_merge_kind_is_catalogued` pins it; github.push's trust chain (`github_pr.py`, d21 `_chain`) is the precedent for refusing a write unless the run came from the right source
  - seeds: `c7`
- `s3` — `node/actions/message.py, apps/discord_rest.py:346-383`: DiscordMessageAction posts {content, `allowed_mentions`} through DiscordClient.`post_message` and already returns `message_id`; adding components is a body field plus a param spec in model/`action_kinds.py`:93-100; no `custom_id` length check exists yet
  - seeds: `c8`
- `s4` — `apps/discord_gateway.py, engine/matching.py:226-258, model/validate.py:503-567`: discord.py client with a CommandTree only (`discord_gateway.py`:464-513): no `on_interaction` or component handling; `LISTENED_EVENTS` (:133) and params.events gate events per actor; `_admitted` (:1100-1111) sinks an event only if an enabled rule selects its channel; the users filter matches `author_id`/`author_name`, so carrying those selects the operator unchanged; emoji/command filters are the template for `custom_id`
  - seeds: `c9`
- `s5` — `apps/discord_rest.py, node/actions/discord_reply.py`: No channel-message edit exists (DiscordClient has `post_message`, `edit_interaction_response`, reactions, reads); disabling a button needs an `UPDATE_MESSAGE` response at ack time or a new edit; discord.reply answers through the interaction token, which lives 15 minutes (`discord_commands.py`:88-90)
  - seeds: `c10`
- `s6` — `node/checks_settle.py ~909-972, docs/actors/github.md:167-183`: `checks_settled` carries repository, `head_sha`, `pr_numbers`, number (first PR only), conclusion and best-effort PR facts (draft, `pr_author`, `head_repo`...) but no title or url; `rearm_settle` can re-emit success for the same SHA (up to 3 generations); fork PRs can arrive with `pr_numbers` \[\] and number null; drafts and bot authors are not filtered
  - seeds: `c11`
- `s7` — `memory: steps generic, flows are config; commit is code and logic`: Flows are live configuration (keysets, library items, the govee wake-up precedent); generic steps only, no flow-specific builtins
  - seeds: `c12`
- `s8` — `engine/runs.py:137-160`: Wait steps only sleep (config.seconds) with an optional `head_unchanged` guard; no wait-for-event exists; github.push -> github.pr.synchronize and discord.reply's `event_id` are precedents for chaining rules through events
  - seeds: `c13`
- `s9` — `api/openapi.json, schemas/, web/src`: openapi.json and schemas/ enumerate no action kinds or event types (no contract change); the editor lists kinds in web/src/rules/ActionPicker.tsx `ACTION_SPECS`, web/src/api/types.ts ActionKind and actors/app-config.ts, and trigger filters in TriggerPicker.tsx

## Decisions

- Every repo the culture-rules GitHub App is installed on is offered; there is no repo allowlist (operator, 2026-10-31)
- Only the operator may approve: the click rule is filtered to the operator's Discord user id (operator, 2026-10-31)
- The Merge button squash-merges (operator, 2026-10-31)
- There is only a Merge button; ignoring the prompt is the no (operator, 2026-10-31)
- github.merge is fenced twice: the run must have been started by a discord.button.clicked event from the operator, and its workflow must be trusted in a merge role, as github.push's chain does (operator, 2026-10-31)
- Draft PRs and fork PRs get no prompt; bot-authored PRs do (operator, 2026-10-31)
- After a merge the PR's same-repo head branch is deleted (operator, 2026-10-31)
- The gateway answers the click by turning the button into a disabled 'Merging…' at once; the merge rule then replies privately with the outcome (operator, 2026-10-31)

## Hard questions

- How strictly is github.merge fenced: trusted-workflow role plus click-event origin (like github.push's chain), or click-event origin alone? (resolved: Click + trusted workflow: github.merge refuses unless the run was started by a discord.button.clicked event from the operator and its workflow is trusted in a merge role (operator, 2026-10-31))
- Which green PRs get a prompt: drafts, fork PRs (often no PR number), PRs by bots or agents? (resolved: Skip drafts and fork PRs; bot-authored PRs are offered (operator, 2026-10-31))
