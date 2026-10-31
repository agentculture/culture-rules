# Build Plan — govee lamp actor and wake-up

slug: `govee-lamp-actor-and-wake-up` · status: `exported` · from frame: `govee-lamp-actor-and-wake-up`

> A Govee lamp is a culture-rules actor any workflow can drive, and a daily wake-up workflow fades it in at 06:20

## Tasks

### t1 — Reinstall govee on spark from govee-cli main

- instruction: Operator shell step on spark: uv tool install --force --from git+<https://github.com/agentculture/govee-cli@main> govee-cli (or from a fresh checkout of origin/main; the local main is 77 commits behind). Check with command -v govee and govee rules actor --json. Ask before running.
- acceptance:
  - govee rules actor --json on spark exits 0 and prints a runner actor with 8 commands (o9)
  - govee --version on spark matches pyproject.toml on govee-cli origin/main

### t2 — Run govee serve always-on on spark with a culture-rules client token

- instruction: Install govee-cli's docs/systemd/govee-serve.service as a user unit (linger is already yes for spark), daemon-reload, enable --now. Then govee token new --name culture-rules --save as user spark, the same user culture-rules-node.service runs as. `GOVEE_API_KEY` stays in serve's env file only. Ask before starting the unit.
- depends on: t1
- acceptance:
  - systemctl --user is-enabled govee-serve and is-active govee-serve both succeed, and it is still active after systemctl --user restart govee-serve (o5)
  - govee token list shows a token named culture-rules saved to the client-token file (o6)

### t3 — Import the lamp runner actor into the live store and prove one step drives the lamp

- instruction: Generate with govee rules actor --bin "$(command -v govee)" --machine spark into the scratchpad, read it, then culture-rules actors create --body @file (dry run), then --apply. Needs `CULTURE_RULES_TOKEN` injected from grant (hidden admin token, inject only). The scratch workflow is live only and is deleted after; nothing is committed.
- depends on: t2
- covers: c1, h5, c2, h1, c11, h9
- acceptance:
  - before the import, culture-rules actors list --json holds no actor with a govee argv (h9)
  - culture-rules actors show of the new actor has params.commands equal to govee rules actor --json output: 8 commands, absolute argv\[0\], each ending --source rules --apply --json (o2)
  - a scratch workflow run with one `lamp_color` step on spark succeeds (no InlineCommandDenied) and govee events shows the change with source rules (o1, o3)
  - a `lamp_fade_on` step with seconds 600 returns in under 10 s while govee light fade-status shows the fade running (o5)

### t4 — Configure the wake-up workflow and its 06:20 schedule rule live, disabled

- instruction: Create the workflow and rule through the CLI, dry run then --apply. Leave wake-up details (fade length, colour, brightness) as operator-supplied values; ask for them, don't invent them. Do not enable the rule: the operator does.
- depends on: t3
- covers: c6, h4, c9, h7, c10, h8, c12, h10
- acceptance:
  - culture-rules rules show of the wake rule: trigger kind schedule, cron 20 6 \* \* \*, tz Asia/Jerusalem; the workflow's `lamp_fade_on` step is placed on spark (o8)
  - culture-rules actors references on the lamp actor lists the wake-up workflow, and the actor is enabled on spark (o10)
  - the wake-up workflow adds no actor and no code: it reuses the t3 actor (h10)
  - the rule is created disabled; the operator enables it (h7)

### t5 — Docs: lamp setup guide in culture-rules, version bump and PR

- instruction: Branch rules/govee-lamp in ../.worktrees.culture-rules/govee-lamp. Commit the spec, the frame, the plan and the guide. Mention the guide in docs/actors/README.md's table only if it fits the table's shape. Open the PR with the cicd skill.
- depends on: t3
- covers: c5, h6
- acceptance:
  - a new docs/operations/govee-lamp.md describes the setup on disk (reinstall, serve unit, token, actor import) and links govee-cli's docs/culture-rules/README.md instead of copying the actor JSON
  - git diff main of the PR touches no file under `culture_rules`/, web/ or api/openapi.json, and holds no govee actor JSON and no wake-up workflow or rule (o4, o7)
  - markdownlint-cli2 over the tree, scripts/scan-secrets.py and teken cli doctor . --strict pass; the version is bumped (patch) with a CHANGELOG entry

### t6 — Verify the first 06:20 wake-up run and file the evidence

- instruction: Read back the next morning with culture-rules runs list --json and govee stats --json/events; attach with devague evidence.
- depends on: t4
- covers: c13, h11
- acceptance:
  - the first run after the operator enables the rule starts within 60 s of 06:20 Asia/Jerusalem and succeeds (runs list)
  - govee events for that morning show the fade with source rules and the lamp ends on (o11)
  - both readings are filed as devague evidence on the plan, not assumed

## Risks

- [unknown_nonblocking] If govee serve is down at 06:20 the runner falls back to direct LAN, so a long fade holds the step for its whole length (timeout 3660 s) instead of failing fast (task t4)
- [out_of_scope] After a fade-out the lamp rests at brightness 1; how `lamp_fade_on` behaves from that state is a wake-up detail for the workflow config, outside this plan
- [unknown_nonblocking] The runner starts commands with only PATH and LANG; govee finds its client token through the passwd home of user spark (expanduser), confirmed by reading `govee_cli`/auth/tokens.py, but not yet run under the node (task t3)
