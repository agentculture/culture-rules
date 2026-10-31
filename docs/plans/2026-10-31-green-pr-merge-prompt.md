# Build Plan — Green PR merge prompt

slug: `green-pr-merge-prompt` · status: `exported` · from frame: `green-pr-merge-prompt`

> When a PR's checks all pass in any repo the culture-rules GitHub App sees, #spark on Discord gets a prompt with a green Merge button, and the operator's click squash-merges that PR at exactly the commit that went green

## Tasks

### t1 — Model: register `github.merge` and `github.pr`, the `buttons` param on `discord.message`, the event `discord.button.clicked` and its `custom_id` trigger filter

- instruction: Model layer only, so later tasks do not collide here: `action_kinds.py` (+ the module docstring: merging stays a human gate, fenced by the click and a trusted merge role), model/`discord_filters.py` (`custom_id` helper next to emoji), model/validate.py (~L540-570 guards), engine/matching.py (`_discord_admits`), model/describe.py. Templates: commits 73e1d29 (discord.react / reaction.added) and 9c5f7a6 (command filter). No ports, no gateway, no web.
- covers: c9, c24
- acceptance:
  - `culture_rules`/model/`action_kinds.py` catalogues github.merge (actor, repo, number:int, sha, `delete_branch`:bool optional) and github.pr (actor, repo, number:int); discord.message gains optional buttons (list); tests/model/`test_action_kinds.py` passes, with `test_no_merge_kind_is_catalogued` rewritten to pin that github.merge is the only merge kind
  - model/validate.py accepts `custom_id` (a string or list, prefix match) only on discord.button.clicked, refusing it elsewhere like command and emoji; engine/matching.py admits a click whose data.`custom_id` starts with a listed prefix; tests in tests/model/`test_discord_trigger_params.py` and tests/engine/`test_discord_trigger_filters.py`
  - model/describe.py gives github.merge, github.pr and the `custom_id` filter their phrases (tests/model/`test_describe.py`)

### t2 — `github.merge` port: squash-merge as the App at the prompted sha, fenced to an operator click in a trusted merge workflow, with optional branch delete

- instruction: New `culture_rules`/node/actions/`github_merge.py` mirroring GitHubPrHeadPort (node/actions/github.py ~290-345: actor resolution, allowlist, `_app_within` deadline) and `github_pr.py` chain checks; add GitHubApp.`merge_pull` and `delete_ref` in apps/github.py (update its docstring line 18). Which user clicked is the click rule users filter (configuration); the port checks the event type and the trust role, not a hard-coded user.
- depends on: t1
- covers: c6, h1, c7, h2
- acceptance:
  - tests/node/`test_github_merge.py` pins the call: PUT /repos/{repo}/pulls/{number}/merge with sha and `merge_method` squash; 409 (head moved) and 405 (not mergeable) fail non-retryable; an unknown outcome is never retried blindly (a once claim like github.comment `once_key`); `delete_branch` true deletes `refs/heads/<head>` only for a same-repo head after a merged response
  - the fence refuses before any secret read or network call: a run not started by a discord.button.clicked event, a run whose workflow is not trusted in role merge (`TRUSTED_WORKFLOWS`, as github.push d21 `_chain`), and a disabled source rule; each refusal has its own error code and test
  - node/runner.py registers action:github.merge (MissingExtraPort without the github extra); tests/node wiring test proves it

### t3 — `discord.message` buttons: post components and keep `message_id`

- instruction: apps/`discord_rest.py` `post_message` (346-383) gains an optional components arg; node/actions/message.py DiscordMessageAction passes params.buttons through ({label, style: success|primary|secondary|danger, `custom_id`}). No gateway changes here.
- depends on: t1
- covers: c8, h3
- acceptance:
  - tests show discord.message with buttons posts components \[{type:1, components:\[{type:2, style:3 for success, label, `custom_id`}\]}\]; a `custom_id` over 100 characters or more than 5 buttons is refused `bad_params` before posting; without buttons the body is byte-identical to today
  - the result still carries `message_id`

### t4 — Gateway: a button click becomes `discord.button.clicked`, answered at once with the button disabled

- instruction: apps/`discord_gateway.py`: add `BUTTON_EVENT` to `LISTENED_EVENTS`, an `on_interaction` handler for component interactions next to `build_command_tree` (340-380) and `_on_command` (1045-1098); reuse `remember_interaction` from apps/`discord_commands.py`; node/daemon.py lease note; node/actions/`discord_reply.py` only if it rejects non-command events. Channel admission (`_admitted`) stays as is.
- depends on: t1
- covers: c9, h4, c10, h5
- acceptance:
  - tests/apps/`test_discord_gateway.py` (with `discord_fakes`) turn a component interaction into one discord.button.clicked event with `custom_id`, `message_id`, `channel_id`, `guild_id`, `author_id`, `author_name`, `user_roles`, for an actor that declares the event in params.events; the delivery id is `actor:interaction:<id>`
  - every click is acknowledged inside the 3 s window: an admitted click edits the message with its buttons disabled (`UPDATE_MESSAGE`), a click no rule admits gets a private not-here answer and changes nothing
  - the interaction token is kept apart (`discord_interactions`, 15 minutes) so discord.reply with the click `event_id` answers the clicker privately; a test proves discord.reply accepts a button event

### t5 — `github.pr` read: the facts a configured prompt needs

- instruction: Port in `culture_rules`/node/actions/github.py beside GitHubPrHeadPort, registered in node/runner.py after t2 (same file). `checks_settle.py` stays unchanged (c14).
- depends on: t2
- covers: c11, h15
- acceptance:
  - tests/node show github.pr returns title, url, author, draft, state, `head_repo`, `base_repo`, `head_branch` and `head_sha` for a PR through GitHubApp.`get_pull`, read-only and safe to retry
  - tests/rules show a keyset.filter / keyset.add pair configured on repository@`head_sha` drops a second `checks_settled` success for the same commit, with no new code

### t6 — Editor: the new kinds, the buttons param and the `custom_id` filter in the pickers

- instruction: Templates: discord.react / emoji and command filter in ActionPicker.tsx (~L97) and TriggerPicker.tsx (~L313-321, onChange ~L388). No new page.
- depends on: t1
- covers: c24
- acceptance:
  - web/src/api/types.ts ActionKind includes github.merge and github.pr and the trigger params include `custom_id`; ActionPicker.tsx `ACTION_SPECS` edits them and discord.message buttons; TriggerPicker.tsx shows `custom_id` only for discord.button.clicked and clears it on a type change; actors/app-config.ts lists them; vitest (ActionPicker\*.test.tsx, TriggerPicker.discord.test.tsx) and typecheck pass

### t7 — Docs, harness files and version

- instruction: Keep the four harness files in step; the "no merge kind" wording becomes "one merge kind, fenced by an operator click and a trusted merge role".
- depends on: t2, t3, t4, t5, t6
- covers: c24
- acceptance:
  - docs/actors/github.md documents github.merge (fence, sha, squash, `delete_branch`, errors) and github.pr; docs/actors/discord.md documents buttons, discord.button.clicked, `custom_id` and the click answer; a short docs/operations guide shows how to configure a green-PR prompt from these pieces as an example, not committed rules
  - CLAUDE.md, AGENTS.override.md, AGENTS.colleague.md and QWEN.md say the same; the minor version is bumped with a CHANGELOG entry; markdownlint, scan-secrets and teken doctor --strict pass

### t8 — End-to-end proof and the surfaces-only check

- instruction: Template tests/rules/`chain_world.py` and tests/rules/`test_discord_reaction_flow.py`. Run the full suite once here (main agent), per parallel-test-load.
- depends on: t2, t3, t4, t5
- covers: c21, h12, c22, h13, c12, h7, c14, h8, c24, h16, c20, h11
- acceptance:
  - tests/rules/`test_green_merge_flow.py` (`chain_world` style, fake GitHub and Discord) drives `checks_settled` success -> github.pr -> prompt with a button -> operator click -> github.merge squash at the prompted sha -> branch delete -> discord.reply, with the rules built in the test, not committed
  - the same file shows a click by another user, a moved head (409) and an untrusted workflow merge nothing
  - git diff main holds no workflow or rule, and grep finds no channel id, repository, Discord user id or prompt text under `culture_rules`/ or web/src; the PR fixer, github.push and `checks_settle` tests pass unchanged in the full suite

## Deferred targets

- `c1` (announcement): When a PR's checks all pass in any repo the culture-rules GitHub App sees, #spark on Discord gets a prompt with a green Merge button, and the operator's click squash-merges that PR at exactly the commit that went green — deferred: the announcement holds once the live rules prompt and merge a real PR; configuration, after this code
- `h9` (honesty): the first real green PR after rollout is prompted in #spark and merged by one operator click at the prompted sha, read back from runs list, the Discord message and the PR's merge commit — deferred: same live readback as h14
- `c19` (audience): The operator, who merges AgentCulture PRs by hand today and reads #spark on Discord — deferred: the users filter naming the operator and the #spark channel are rule configuration (c24)
- `h10` (honesty): the click rule's users filter names the operator's Discord user id, and the prompt posts to #spark through the discord-bot actor — deferred: same as c19: configuration
- `c23` (success_signal): The first real green PR after rollout gets exactly 1 prompt in #spark within 60 s of `checks_settled`, one operator click merges it at the prompted sha (squash) and deletes the branch, and a click by anyone else, or on a moved head, merges nothing — deferred: configuration and live: the success signal is read back from the first real green PR after the operator configures the two rules live (c24); not a code task
- `h14` (honesty): the first real green PR after rollout is read back: runs list for both rules, the #spark message, the PR's squash merge commit equal to the prompted sha, and the deleted branch, filed as devague evidence — deferred: live readback after the rules are configured; filed as evidence then

## Risks

- [unknown_nonblocking] Repository rulesets or branch protection may refuse an App merge (required reviews, merge queue); found only at the first real merge (task t2)
- [unknown_nonblocking] discord.py component handling without a View: confirm `on_interaction` receives component interactions for messages posted over REST (task t4)
