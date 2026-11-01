# Delivery Summary — Green PR merge prompt

plan: `green-pr-merge-prompt` · run: `complete` · date: `2026-10-31`
baseline: `devague summary skeleton`

## Intent

> When a PR's checks all pass in any repo the culture-rules GitHub App sees, #spark on Discord
> gets a prompt with a green Merge button, and the operator's click squash-merges that PR at
> exactly the commit that went green

After: Within a minute of a PR's checks settling green, #spark shows its repo, number, title,
author and link with a green Merge button; the operator's click squash-merges exactly that
commit, deletes the branch, and the operator sees the result privately.

The plan builds only the surfaces and integrations (c24). The prompt and merge rules are
configured live and never committed (c12). Since d3 the prompt posts to #pull-requests
(channel 1511667505574445066), a live edit, not a release.

## Planned Work

Quoted verbatim from the `devague summary` skeleton. `t2` had been left `proposed` in the plan
state; the operator re-confirmed it on 2026-11-01.

- `t1` — Model: register `github.merge` and `github.pr`, the `buttons` param on
  `discord.message`, the event `discord.button.clicked` and its `custom_id` trigger filter
- `t2` — `github.merge` port: squash-merge as the App at the prompted sha, fenced to an operator
  click in a trusted merge workflow, with optional branch delete
- `t3` — `discord.message` buttons: post components and keep `message_id`
- `t4` — Gateway: a button click becomes `discord.button.clicked`, answered at once with the
  button disabled
- `t5` — `github.pr` read: the facts a configured prompt needs
- `t6` — Editor: the new kinds, the buttons param and the `custom_id` filter in the pickers
- `t7` — Docs, harness files and version
- `t8` — End-to-end proof and the surfaces-only check

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | The kinds `github.merge` and `github.pr`, the `buttons` param, the event `discord.button.clicked` and the `custom_id` prefix filter are registered. PR #129 (0.39.0). |
| `t2` | delivered | `node/actions/github_merge.py` squash-merges at `sha`, refuses `head_moved`, holds a once claim and deletes a same-repo branch. The fence moved from pinned workflows (d2, #129 and #130) to a one-time button code (d3, PR #131, 0.40.0). |
| `t3` | delivered | `discord.message` posts up to 5 buttons and keeps `message_id`. A button's `value` is stored in `discord_buttons` (d1), and `code: true` adds a one-time code (d3). |
| `t4` | delivered | The gateway turns a click into one `discord.button.clicked` event with the stored value, poster and code, and answers inside 3 s by disabling the buttons. |
| `t5` | delivered | `github.pr` returns the PR facts the prompt reads (title, author, url, draft, fork, head and base). |
| `t6` | delivered | `web/src/rules/ActionPicker.tsx` edits buttons (value, and "gets a one-time code" since d3). The trigger picker has the `custom_id` filter. |
| `t7` | delivered | `docs/operations/green-pr-merge.md`, `docs/actors/github.md`, `docs/actors/discord.md`, the four harness files, CHANGELOG 0.39.0, 0.39.1 and 0.40.0. |
| `t8` | delivered | `tests/rules/test_green_merge_flow.py` gives the end-to-end proof and the surfaces-only checks. Two live merges back it (e1 and e2). |

## Mid-work Decisions

- `d1` — Buttons keep their meaning in the db: a valued button gets an opaque `custom_id` and
  its value is stored before the post. Reason: no existing piece can parse repo, number and
  sha out of a `custom_id`. The operator chose option B (store the meaning) on 2026-10-31.
- `d2` — The fence also pinned the posting workflow (`merge_prompt` role). Reason: code review
  of #129, finding 4. Reversed by d3.
- `d3` — A one-time button code replaced both workflow pins. Reason (operator, 2026-10-31):
  workflows are configuration, not code, so moving the prompt to #pull-requests must not need
  a code PR.
- Who can set up a merge stays as built (c26 and c27, operator 2026-11-01). Anyone who can
  press can merge, and #pull-requests is protected. Live, only admins can save rules and
  workflows: the operator via SSO and two admin service tokens, with no editors configured.
- The two parked risks closed live. r1: no ruleset refused the App's merge. r2:
  `on_interaction` received the clicks on REST-posted messages.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t3` (`d1`) | No existing piece can parse repo/number/sha out of a `custom_id`. The operator chose to keep the button's meaning in the db (option B). | `acceptable` |
| `t2` (`d2`) | Code review of #129, finding 4: the fence did not check who posted the button. The operator chose to pin the prompt too. Reversed by d3. | `acceptable` |
| `t2` (`d3`) | Operator 2026-10-31: workflows are configuration, not code. Both pins were replaced by a code issued on the button and checked when the click is consumed. The trust is the operator's click plus a valid unused code. | `risky` |

## Evidence

- tests: the six green-merge files (`tests/node/test_github_merge.py`,
  `tests/apps/test_discord_buttons.py`, `tests/apps/test_discord_gateway.py`,
  `tests/node/test_message_action.py`, `tests/rules/test_green_merge_flow.py` and
  `tests/rules/test_trusted_role_ids.py`) — pass (279 passed on main `23c3356`, 2026-11-01).
  Filed as e3–e10 against obligations o1–o8.
- tests: `web/src/rules/ActionPicker.discord.test.tsx` and
  `web/src/rules/TriggerPicker.discord.test.tsx` — pass (31 passed on main `23c3356`).
- live: e1 — embodiment#117 merged as `86aeabd` by the App, branch deleted (0.39.1,
  runs `run-b255b49f…` and `run-96767d8b…`).
- live: e2 — cultureagent#58 merged as `4fefe69` by `app/rules-culture-dev`. The click carried a
  one-time code and no workflow pin was checked. Branch deleted (0.40.0, runs `run-4bad199d…`
  and `run-fd49b0d8…`).
- commits: `afcaa65` (#129, 0.39.0), `b543dd1` (#130, 0.39.1), `23c3356` (#131, 0.40.0).
- PRs: #129, #130, #131. The live rollouts of 0.39.0, 0.39.1 and 0.40.0 each ended with
  `rollout verify` passing on all 4 nodes.

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| A green PR is prompted in the channel with a coded Merge button, and the operator's click squash-merges it at the prompted sha and deletes the branch | high | e2 (live, 0.40.0) · e1 (live, 0.39.1) · PR #131 |
| Only a stored click with this button's unused code, bound to repo, number and sha, can merge; every refusal comes before any secret or call | high | e3 · `tests/node/test_github_merge.py` (d3 section) · `tests/rules/test_green_merge_flow.py` |
| A moved head merges nothing; an unknown outcome is never re-sent; a refused merge hands the code back | high | e4 · `tests/node/test_github_merge.py` |
| The same-repo head branch is deleted after a merge, and a fork's is not | high | e5 · e1 and e2 (GitHub 404) |
| A click is answered inside 3 s with the buttons disabled | high | e10 · `tests/apps/test_discord_gateway.py` |
| Draft, fork and red PRs are not prompted | medium | e8 (tests only; no live draft or fork PR was observed) |
| No rule or workflow for the flow is committed | high | e9 · `tests/rules/test_green_merge_flow.py::test_the_delivery_commits_no_rule_or_workflow` |
| The 1-prompt-within-60-s part of the success signal (c23) | medium | e1 and e2 show 1 prompt each, about 1–2 s after `checks_settled`; prompts per PR were not counted over a longer window |

Evidence e1–e10, obligations o1–o8 and deltas b1–b4 are all approved by the operator.

## Remaining Work / Follow-up

- The editor gap (c27): the code still lets an editor save a `github.merge` step. If an editor
  is ever configured, make saving a merge step admin-only, as inline scripts already are.
- Prompts for a PR whose head moves repeat (agentirc#66 and cultureagent#59 each got two on
  2026-11-01). Each one is bound to its own sha, so an old button merges nothing. Leave as
  is unless the repeats become noise.
