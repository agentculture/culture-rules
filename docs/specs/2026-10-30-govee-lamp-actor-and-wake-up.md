# govee lamp actor and wake-up

> A Govee lamp is a culture-rules actor any workflow can drive, and a daily wake-up workflow fades it in at 06:20

## Audience

- The operator at home on spark, and any culture-rules workflow author who wants a lamp as a step

## Before → After

- Before: govee-cli can drive the lamp from its CLI, web console and MCP, but no culture-rules actor exists for it, so no rule or workflow can touch the lamp
- After: The lamp is one enabled actor in the Actors tab; any workflow calls `lamp_`\* commands by name with typed args, and a schedule rule fades the lamp in each morning at 06:20 Asia/Jerusalem

## Why it matters

- Lights become a reusable output of the mesh (CI red, wake-up, bedtime) by configuration alone, with govee-cli keeping ownership of how the lamp is spoken to

## Requirements

- The lamp is reached through a culture-rules runner actor whose params.commands registry (`culture_rules`/actors/code.py) holds govee-cli's named lamp commands (`lamp_on`, `lamp_off`, `lamp_brightness`, `lamp_color`, `lamp_temp`, `lamp_scene`, `lamp_fade_on`, `lamp_fade_off`), each argv the absolute govee path ending --source rules --apply --json; govee-cli generates the definition with 'govee rules actor' (govee-cli docs/culture-rules/README.md)
  - honesty: the actor's commands are exactly what 'govee rules actor' prints on spark, every argv\[0\] absolute and no argv a shell or interpreter, so `inline_guard` admits all 8
- The wake-up is a schedule rule (stdlib cron, IANA tz Asia/Jerusalem) at 06:20 daily that starts a workflow whose step calls the lamp actor's fade-in command; the step is placed on the machine the lamp's runner lives on (spark)
  - honesty: the rule's trigger is kind schedule with cron 20 6 \* \* \* and tz Asia/Jerusalem, and its workflow step is placed on spark where the runner and the lamp's LAN are

## Honesty conditions

- a `lamp_`\* step run from culture-rules changes the real H6076 lamp, seen in govee stats with source rules
- the delivered change touches no file under `culture_rules`/ or web/; only live store records (actor, workflow, rule) and docs change
- git diff of the delivery holds no govee actor JSON and no wake-up workflow or rule in either repo; the actor, workflow and rule exist only in the live store
- the operator, not a mesh agent, enables the wake-up rule; any workflow author can add a lamp step without touching govee-cli
- culture-rules actors references on the lamp actor lists the wake-up workflow, and the actor is enabled on machine spark
- before this delivery no culture-rules actor had a govee argv (checked live with actors list once a token is set)
- a second flow can use the lamp with only a new workflow step, no new actor and no code
- the first morning's run record and govee stats (source rules) are read back and attached as evidence, not assumed

## Success signals

- On 1 morning after enabling, the run at 06:20 Asia/Jerusalem succeeds within 60 s of the scheduled time, govee's event log shows the fade with source rules, and the lamp ends on

## Scope / boundaries

- govee-cli owns the lamp command list and the actor definition; culture-rules commits no govee actor JSON (the argv path is per machine, govee-cli README) and no wake-up bundle; schedules live in culture-rules' store, never in govee-cli

## Non-goals

- No culture-rules code change, no new actor kind, app surface or lamp.\* action kind: `ACTOR_KINDS` already has runner (`culture_rules`/model/actor.py:15) and a step calls a registered command by name, so the lamp actor is configuration only
- Wake-up details (target brightness, colour or temperature, fade length, weekdays only, snooze, skip when away) are not in this frame; they are workflow configuration set later

## Assumptions

- govee serve runs on spark as an always-on user unit (docs/systemd/govee-serve.service) so a long fade runs inside serve and the runner step returns at once, scenes see `GOVEE_API_KEY`, and the runner authenticates with a token saved by 'govee token new --name culture-rules --save'
- The installed govee on spark (~/.local/bin/govee 0.11.0) is reinstalled from merged govee-cli main before import; today it lacks the rules verb ('invalid choice: rules')

## Scope exploration

- `s1` — `govee-cli docs/culture-rules/README.md + govee_cli/cli/_commands/rules.py`: govee-cli (PR #3, merged) already ships 'govee rules actor', which prints a runner actor with 8 typed commands; fade commands carry a 3660 s timeout; argv\[0\] is absolute because runner commands start with PATH=/usr/local/bin:/usr/bin:/bin only
  - seeds: `c2`
- `s2` — `culture_rules/model/actor.py, model/app_actor.py APP_SURFACES`: `ACTOR_KINDS` = agent, human, service, daemon, runner, robot, app; `APP_SURFACES` = github, jira, discord, email, search; nothing lamp-specific is needed; the steps-generic memory forbids a flow-specific builtin
  - seeds: `c3`
- `s3` — `govee-cli README 'Run it with govee serve' + systemctl --user is-active govee-serve`: govee-serve is inactive on spark today (2026-10-30); without it a 20-minute fade blocks the runner step for its whole length and `lamp_scene` cannot see the API key
  - seeds: `c4`
- `s4` — `govee-cli README 'Schedules live in culture-rules, not here'`: govee-cli has no timers of its own; when a command fires is the rule's business
  - seeds: `c5`
- `s5` — `CLAUDE.md second mile: schedule trigger (stdlib cron, UTC or an IANA tz)`: a schedule trigger already exists; the newspaper bundle uses Asia/Jerusalem the same way
  - seeds: `c6`
- `s6` — `govee rules actor --json on spark`: installed binary refuses 'rules' even though the repo's 0.11.0 has it; the tool install predates PR #3's last commits
  - seeds: `c8`
- `s7` — `culture_rules/actors/code.py (runner)`: registered commands run shell=False with typed params, in a temp dir, minimal env; `supports_idempotency_key`=False so an unknown outcome is not re-run unless the step is declared idempotent

## Decisions

- Transport: a runner actor generated by govee rules actor (operator, 2026-10-30)
- govee serve runs always-on on spark; the runner authenticates with a saved token (operator, 2026-10-30)
- The wake-up workflow and rule are configured live only; nothing about them is committed (operator, 2026-10-30)

## Hard questions

- Does govee serve become an always-on unit on spark as part of this, or does the runner call the lamp directly over LAN (a long fade then holds the step)? (resolved: govee serve becomes an always-on user unit on spark)
