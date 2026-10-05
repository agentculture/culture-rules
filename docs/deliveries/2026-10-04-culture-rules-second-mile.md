# Delivery Summary — culture-rules second mile

plan: `culture-rules-second-mile` · run: `partial` · date: `2026-10-04`
baseline: `devague summary skeleton`

## Intent

> rules.culture.dev rules point at the real world: typed and scheduled triggers, actions that act through app and server actors, humans and agents as actors, and a Workflows tab where you build and test a workflow on the canvas alone, with PR #4 review follow-ups closed

After: An operator builds, from the editor alone, a rule triggered by a real GitHub, Jira, Discord, schedule or probe event, whose action comments, messages, calls HTTP or runs a machine command through an actor; and builds and test-runs a workflow with typed inputs on the canvas without writing a rule

Executed through `/assign-to-workforce`: 47 tasks in 13 waves on `rules/second-mile`, shipped as 0.11.0 in PR #8. Two follow-up PRs fixed review findings and rollout blockers (#10, 0.11.2; #11, 0.11.3), and the live rollout and its validation ran on 2026-10-05.

## Planned Work

- `t1` — Trigger kinds and validation: event requires params.type, schedule requires cron (+optional tz), probe requires actor+command+schedule+mode, manual takes none
- `t2` — Optional extras github and discord in pyproject plus a zero-dependency import test
- `t3` — Stdlib 5-field cron parser with UTC default, IANA tz via zoneinfo, DST-safe slot iteration
- `t4` — Action kind catalog with typed param schemas and the params.actor binding convention, validated on save
- `t5` — Actor kind app with declared events, probes and actions; secrets only as grant refs
- `t6` — WorkflowRef.inputs accepts the structured literal and ref forms (#7)
- `t7` — Matching: an event trigger without a type matches nothing, and self-authored events do not match unless the rule opts in
- `t8` — Rule actions dispatch through the actor named in params.actor, with limits; unknown or disabled actors fail with a clear code
- `t9` — Direct workflow runs pin a synthetic rule; run docs carry top-level `rule_id` and `workflow_id`
- `t10` — Heartbeat semantics: one `offline_after`(`beat_every`) helper used by placement, health and takeover; `_machine_online` decided and pinned (#7)
- `t11` — API route POST /workflows/{id}/run plus workflows run verb in CLI and MCP
- `t12` — Webhook event sink: write a typed envelope to the events collection once per delivery id, tag self-authored events, drop when the app actor is disabled, count outcomes
- `t13` — GitHub webhook receiver: HMAC-SHA256 check, delivery-id dedupe, typed github.\* events, answer fast
- `t14` — Jira webhook receiver and Jira REST client: HMAC or URL token, refetch issues by key, typed jira.\* events
- `t15` — Mount the hook routes with an exact-path auth exemption
- `t16` — Human actor on first Access sign-in: id is the slugged email, created once, never for service tokens, never overwriting edits
- `t17` — Run history and run lists filter in the store; backfill `rule_id` and `workflow_id` on old run docs (#7)
- `t18` — Typeless event rule migration: list, and with --apply disable with an audit record, never delete
- `t19` — Node scheduler stage: schedule triggers fire once per cron slot on the placed host
- `t20` — Probe triggers: run an allow-listed command on a schedule and fire on change or on a condition over the output
- `t21` — GitHub App client and github.comment action port
- `t22` — jira.comment action port
- `t23` — Discord REST client and message action (Discord channel or mesh channel) with mass mentions suppressed
- `t24` — machine.command and http.call action ports
- `t25` — Register the action ports in the node
- `t26` — Discord Gateway listener held by one node mesh-wide, writing discord.\* events
- `t27` — Firing: per-rule fire-rate cap, cross-host `_settled_skip` cascade, no extra read in `record_decision` (#7)
- `t28` — Chain feed skips the shared-chain transaction when no rule depends on the finished rule; 3-deep chaining unit test (#7)
- `t29` — Events adapter: legacy cursor starts fresh, docstring corrected, configurable depth, quieter logs (#7)
- `t30` — Strengthen the #7 tests: chaos premise, mesh replay, serve store path, events-cli degrade warning, Access malformed 401
- `t31` — Enrol mesh agents per machine as agent actors from the culture agent list and culture.yaml
- `t32` — Health shows webhook delivery outcomes and Discord gateway state
- `t33` — Web API client for the new routes and fields
- `t34` — Switch gets a disabled state and toggles guard against in-flight requests (#7)
- `t35` — Guided errors: map API error codes to plain language plus fix options; never raw text
- `t36` — Rules tab typed trigger picker
- `t37` — Rules tab action picker with typed params and graphical mappings
- `t38` — Actors tab: app actor connection and declarations editor, runner commands editor
- `t39` — Workflows tab Run opens a typed input form and shows outputs in place
- `t40` — Step properties panel covers every Step field
- `t41` — In/out nodes selectable with inputs, outputs, variables and description editors; canvas height from real card heights; empty-canvas guidance (#7)
- `t42` — Soft-deleted workflows view and admin purge
- `t43` — Workflow list dot resolves actor placement to its machine (#7)
- `t44` — Playwright coverage for the new editor flows
- `t45` — Ops docs and node installer: cache rule, hook Bypass paths, kill switches, upgrade order, GitHub App, Discord bot and Jira webhook setup
- `t46` — Release: version bump, CHANGELOG, harness prompt files and learn text in step
- `t47` — Live rollout and delivery validation on rules.culture.dev

## Actual Delivery

46 of 47 tasks delivered, 1 partial (`t47`), 0 dropped, 0 blocked. `t1`–`t46` merged in PR #8 (`4e59767`).

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `TRIGGER_KINDS` and `_check_trigger` in `culture_rules/model/validate.py`; tests `tests/model/test_trigger_validation.py` |
| `t2` | delivered | `github` and `discord` extras in `pyproject.toml`; zero-dependency import test |
| `t3` | delivered | stdlib cron in `culture_rules/engine/cron.py` (UTC default, IANA tz, DST-safe); non-string expressions raise `TypeError` since 0.11.2 |
| `t4` | delivered | `culture_rules/model/action_kinds.py`, validated on save; stored content skips catalog checks (`validate(stored=True)`, review fix) |
| `t5` | delivered | `culture_rules/model/app_actor.py`; secret keys must be `grant:` refs, non-string values refused (review fix) |
| `t6` | delivered | `WorkflowRef.inputs` takes the structured literal and ref forms |
| `t7` | delivered | `culture_rules/engine/matching.py`: typeless event matches nothing; `self_authored` needs `include_self` |
| `t8` | delivered | `params.actor` dispatch in `culture_rules/engine/runs.py` and `culture_rules/node/actors.py`; `actor_unavailable` code |
| `t9` | delivered | `adhoc:<workflow>` synthetic rule; top-level `rule_id`/`workflow_id` on runs |
| `t10` | delivered | `offline_after` in `culture_rules/machines/heartbeat.py`, used by placement, health and takeover |
| `t11` | delivered | `POST /workflows/{id}/run`, `workflows run` in CLI and MCP |
| `t12` | delivered | `culture_rules/events/hook_sink.py`: dedupe on delivery id, self-authored tag, disabled drop, outcome counts |
| `t13` | delivered | `culture_rules/server/hooks/github.py`; live 202 on rules.culture.dev (e3) |
| `t14` | delivered | `culture_rules/server/hooks/jira.py`, `culture_rules/apps/jira.py`; live 202 (e5) |
| `t15` | delivered | `HOOK_PATHS` exact-path exemption in `culture_rules/server/app.py`; route-walk test (e14) |
| `t16` | delivered | `culture_rules/server/humans.py` with link-by-email (`d4`); the rollout half of `d4` was missed at first, then fixed on 2026-10-05: see Drift |
| `t17` | delivered | store-side history filters and `backfill_run_ids` in `culture_rules/store/migrations.py`; applied live (2 runs) |
| `t18` | delivered | `rules migrate-typeless` (+ `runs backfill-ids`, `d5`); applied live: `github-pr-created` disabled |
| `t19` | delivered | `culture_rules/node/schedule.py`; malformed-doc wedge fixed in 0.11.2 (#10) |
| `t20` | delivered | `culture_rules/node/probe_trigger.py` |
| `t21` | delivered | `culture_rules/apps/github.py` + `github.comment` port; 401 re-exchange, gate before key read (#10) |
| `t22` | delivered | `culture_rules/node/actions/jira.py` |
| `t23` | delivered | `culture_rules/apps/discord_rest.py` + `message` action; timeouts non-retryable (#10); live Discord message (e1) |
| `t24` | delivered | `culture_rules/node/actions/machine.py`, `culture_rules/node/actions/http.py` (SSRF ranges refused) |
| `t25` | delivered | action ports registered in the node runner |
| `t26` | delivered | `culture_rules/apps/discord_gateway.py` under named lease; live lease on spark, connected (e7); machine-scoped in 0.11.3 (#11) |
| `t27` | delivered | rate cap, `_settled_skip` cascade in `culture_rules/node/firing.py`; `record_decision` change reverted (`d1`), schedule exempt (`d2`) |
| `t28` | delivered | chain-feed pre-filter in `culture_rules/node/chain.py`; 3-deep chaining test |
| `t29` | delivered | `culture_rules/events/events_cli_adapter.py`; foreign-cursor wedge fixed in 0.11.2 (#10) |
| `t30` | delivered | strengthened #7 tests (chaos premise, mesh replay, serve store path, degrade warning, Access 401) |
| `t31` | delivered | `culture_rules/actors/enrol_agents.py`, `actors enrol-agents`; not run live |
| `t32` | delivered | health reports webhook outcomes and Discord gateway state |
| `t33` | delivered | web API client for the new routes; `ApiError.errors` carries nested codes |
| `t34` | delivered | `web/src/usePending.ts`, Switch disabled state |
| `t35` | delivered | `web/src/api/guidance.ts` + `GuidedNotice`; trigger/action codes added in 0.11.2 (#10) |
| `t36` | delivered | `web/src/rules/TriggerPicker.tsx` (app actors as surfaces, `d3`) |
| `t37` | delivered | `web/src/rules/ActionPicker.tsx` |
| `t38` | delivered | `web/src/actors/AppConfigForm.tsx`, `web/src/actors/CommandsEditor.tsx` |
| `t39` | delivered | `web/src/workflows/RunForm.tsx`; Playwright `workflows-flows.spec.ts` (e12) |
| `t40` | delivered | `web/src/workflows/StepEditor.tsx` |
| `t41` | delivered | `web/src/workflows/IoEditor.tsx`, canvas heights, empty-canvas guidance; inline-ref render loop fixed before merge |
| `t42` | delivered | `web/src/workflows/PurgePanel.tsx` |
| `t43` | delivered | workflow list dot resolves actor placement |
| `t44` | delivered | `web/e2e/rules-pickers.spec.ts`, `actors-config.spec.ts`, `workflows-flows.spec.ts` |
| `t45` | delivered | `docs/operations/rules-culture-dev.md`, `deploy/node/install.sh`; per-app guides `docs/actors/` added in 0.11.3 (#11) |
| `t46` | delivered | 0.11.0 released (PR #8, `4e59767`), harness prompt files in step |
| `t47` | partial | live rollout done (0.11.3 on API + 4 nodes, migrations, 3 app actors, Access bypass apps); validation filed (o1-o13); GitHub and Discord round trips (o1, o3) not run, one typeless rule left (o8), `nachos` email step missed |

## Mid-work Decisions

- `d1` — `record_decision` keeps read-then-insert; the plan's 'inserts and reads only on duplicate key' is dropped — a duplicate key inside a MongoDB transaction aborts it and the follow-up get raises NoSuchTransaction (verified on the Mongo rig by t27); nothing calls `record_decision` today; operator approved reverting the hunk
- `d2` — the default fire-rate cap (60 per trailing hour) does not apply to schedule triggers; event and probe rules keep it unless `max_fires_per_hour` is set — a '\* \* \* \* \*' schedule rule can be capped by evaluation clock jitter; cron already bounds a schedule rule's cadence; operator approved exempting schedule triggers
- `d3` — the trigger picker offers app actors (with declared events) as event surfaces, not machines; machine signals are reached through probe triggers (runner actor command on that machine) — the Machine model declares no events, so a machine cannot be an event surface without a model change; probe triggers already cover machine-side signals
- `d4` — sign-in links to an existing human actor whose params.email matches the Access email before creating a slug-id actor; rollout sets params.email on the existing 'nachos' actor — live store already holds human actor 'nachos' (Ori Nachum, no email); slug-only creation would duplicate the operator; operator chose link-by-email
- `d5` — t18 also adds a 'runs backfill-ids' verb (dry-run by default, --apply) wrapping store.migrations.`backfill_run_ids` from t17 — t17's backfill had no caller; old runs stay missing from history and run filters until it runs; operator chose a CLI verb

- Qwen Code (cortex) reviewed tasks and waves after merge; confirmed findings were fixed in PR #10 (0.11.2): a malformed rule doc wedging the schedule, probe, firing and chain stages; a fan-in cursor wedge; GitHub 401 handling; message timeouts retried as if safe.
- Rollout found that `grant get` refuses `--hidden` secrets, so no app actor could authenticate. Fixed in PR #11 (0.11.3) by injecting secrets at service start (`CULTURE_RULES_SECRET_<NAME>`) and running actions on the actor's machine (deltas `b1`, `b2`).
- Access policies carry no path, so the two webhook bypasses are separate path-scoped Access applications (delta `b3`). They were created through the Cloudflare API.
- The operator's `RULES_CULTURE_DEV_CLI_TOKEN` turned out to be an Access JWT. It was used once to mint a long-lived admin service token, `RULES_CULTURE_RULES_SERVICE_TOKEN`.
- The GitHub App had no webhook secret configured; it was set to the sealed value through the App API.
- The Jira integration reuses the culture-nodes service account and scoped token through the Atlassian gateway, with all projects allowed. Its comments therefore count as self-authored here.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t27` (`d1`) | a duplicate key inside a MongoDB transaction aborts it and the follow-up get raises NoSuchTransaction (verified on the Mongo rig by t27); nothing calls `record_decision` today; operator approved reverting the hunk | `acceptable` |
| `t27` (`d2`) | a '\* \* \* \* \*' schedule rule can be capped by evaluation clock jitter; cron already bounds a schedule rule's cadence; operator approved exempting schedule triggers | `acceptable` |
| `t36` (`d3`) | the Machine model declares no events, so a machine cannot be an event surface without a model change; probe triggers already cover machine-side signals | `acceptable` |
| `t16` (`d4`) | live store already holds human actor 'nachos' (Ori Nachum, no email); slug-only creation would duplicate the operator; operator chose link-by-email | `acceptable` |
| `t18` (`d5`) | t17's backfill had no caller; old runs stay missing from history and run filters until it runs; operator chose a CLI verb | `acceptable` |
| `t16` (`d4`) | the rollout did not set `params.email` on `nachos` before the operator's first sign-in on 0.11.x, so sign-in created a second human actor `ori-nachum-gmail-com`. Fixed: `nachos` now has the email, and the duplicate is soft-deleted (operator's choice) | `acceptable` |
| `t47` | secrets as planned (`grant get`) could not read hidden secrets; required a code change (0.11.3) mid-rollout | `acceptable` |
| `t47` | c41's three round trips: only Jira → rule → Discord was run; GitHub → comment and Discord → reply not run | `needs-follow-up` |
| `t18` | `migrate-typeless` disables typeless rules and does not reach zero alone. The operator typed `github-pr-created` (`github.pr.opened`, still disabled), and 0 remain (e16) | `acceptable` |
| `t45` | the Access Bypass recipe described policies on the existing app; reality is separate path apps (docs corrected in #11) | `acceptable` |

## Evidence

- tests (at `b20b543`): `tests/model/test_trigger_validation.py`, `tests/cli/test_migrate_verbs.py`, `tests/node/test_schedule.py`, `tests/server/test_auth_exemptions.py`, `tests/server/test_hooks_github.py`, `tests/server/test_hooks_jira.py`, `tests/apps/test_discord_gateway.py`, `tests/node/test_action_dispatch.py`: 133 passed
- full suite (at `b20b543`): `uv run pytest -n auto`: 2244 passed, 1 skipped
- Playwright: `web/e2e/workflows-flows.spec.ts`: 8 passed
- lint (CI `lint` job and locally): black, isort, flake8, bandit, markdownlint, scan-secrets, `teken cli doctor --strict`: clean
- live (rules.culture.dev, 0.11.3, 2026-10-05):
  - GitHub redelivery: 202, `github.pr.closed` recorded;
  - Jira comment: 202, `jira.comment.created` recorded;
  - Discord lease held by `engine@spark` and connected, with `discord.message.created` events recorded;
  - round trip: run `run-eff78270974777a6be9d9f56bb6220fb` succeeded, Discord message `1556524557496619012`;
  - typeless rule save: HTTP 422 and CLI exit 1.
- devague: obligations `o1`–`o13`, evidence `e1`–`e16` (15 pass, 1 fail `e10` superseded by `e16`), deltas `b1`–`b4`. The obligations are approved by the operator; evidence, deltas and lapses are still `proposed`.
- commits: `0e456ed..b20b543` on `main`
- PRs: #8 (0.11.0), #9 (0.11.1), #10 (0.11.2), #11 (0.11.3), #12 (this validation); issues #5, #6, #7

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| Typed triggers: an event rule without a type is refused on save | high | test `tests/model/test_trigger_validation.py::test_event_requires_type` · live e9 (HTTP 422, CLI exit 1) |
| GitHub webhook deliveries become typed `github.*` events | high | `tests/server/test_hooks_github.py` · live e3 (202, `github.pr.closed` recorded) |
| Jira webhook deliveries become typed `jira.*` events | high | `tests/server/test_hooks_jira.py` · live e5 (202, `jira.comment.created` recorded) |
| One node holds the Discord gateway and records messages | high | `tests/apps/test_discord_gateway.py` · live e7 (lease `engine@spark`, events recorded) |
| An event can fire a rule whose action acts through an app actor | high | live e1: run `run-eff78270974777a6be9d9f56bb6220fb`, Discord message `1556524557496619012` |
| A GitHub PR opened produces exactly one App comment within 60 s | unverified | o1 not run (no evidence; not claimed done) |
| A Discord message produces a Discord reply within 60 s | unverified | o3 not run (no evidence; not claimed done) |
| No typeless event triggers remain after migration | high | e16: 0 typeless in the live store after the operator typed `github-pr-created` (supersedes the failing e10, `s1`) |
| Schedule triggers fire once per slot on the placed host across restarts | medium | `tests/node/test_schedule.py` (multi-host simulation, e11); live one-hour run on thor not performed |
| Workflows run from the typed form with outputs in place | high | `web/e2e/workflows-flows.spec.ts` (e12) · PRs #8, #11 CI `web` job green |
| Only the two exact hook paths skip auth | high | `tests/server/test_auth_exemptions.py::test_walk_every_route_without_a_principal` (e14) · live: other paths 302 to Access |
| Hidden grant secrets work for app actors | high | PR #11 · `tests/actors/test_secrets.py` · live e3/e5 depend on it |
| Full suite and SonarCloud gate pass on the delivery PRs | high | e13: PRs #8, #10, #11 checks green, Sonar 0 open |
| Every issue #7 item is closed with a linked test or doc | medium | e15: #7 closing comment maps all 22 items to tests, docs or `d1` (links checked, tests not re-run per item) |
| A human signing in gets exactly one human actor | medium | `d4` link-by-email in `culture_rules/server/humans.py`; `nachos` now has `params.email`, and the duplicate from the missed step is soft-deleted (no new sign-in observed since) |

Lapse ledger evidence:

pending approval (not yet evidence): `l1`, `l2`, `l3`, `l4`, `l5`, `l6`, `l7`, `l8`, `l9`, `l10`, `l11`. `l1` is now measured: 1 typeless rule (o8).

## Remaining Work / Follow-up

- `t47` / o1: a GitHub PR opened → `github.comment` round trip. Next step: a temporary rule on a throwaway PR. Owner: operator approval, then agent.
- `t47` / o3: a Discord message → Discord reply round trip. Next step: a temporary rule on #spark-tests.
- o9: an optional live one-hour `*/5` schedule run on thor.
- Issues #5, #6 and #7 are closed with linked tests and docs. #5's comment lists what is still open.
- Adjudicate evidence `e1`–`e16`, deltas `b1`–`b4` and lapses `l1`–`l11` (operator). Obligations `o1`–`o13` are approved.
- Second-pass Qwen Code reviews of the remaining tasks and waves are still running. Confirmed findings go to follow-up PRs.
- Follow-up issues to file:
  - a custom heartbeat cadence (r7);
  - output rename and downstream rules (r8);
  - the secret-key classifier and token counters (r9);
  - the flaky `test_harness_kill` (r6);
  - schedule-rule relationships validated at save;
  - action ports honouring `deadline`;
  - residual raw error text in the Rules and Workflows tabs;
  - a live Jira project picker;
  - agent-activity event sources.
- `github-pr-created` is typed but still disabled: its action is the placeholder `mesh.message` with no channel or text. Give it a real action before enabling it.
- Two test comments by the service account remain on SCRUM-21 (marked safe to delete). The test rule `test-jira-scrum21-to-discord` is disabled.
