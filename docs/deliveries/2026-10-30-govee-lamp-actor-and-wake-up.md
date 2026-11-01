# Delivery Summary — Govee lamp actor and wake-up

plan: `govee-lamp-actor-and-wake-up` · run: `complete` · date: `2026-10-30`
baseline: `devague summary skeleton`

## Intent

> A Govee lamp is a culture-rules actor any workflow can drive, and a daily wake-up workflow
> fades it in at 06:20

After: The lamp is one enabled actor in the Actors tab; any workflow calls `lamp_`\* commands
by name with typed args, and a schedule rule fades the lamp in each morning at 06:20
Asia/Jerusalem.

The lamp is configuration only: a `runner` actor whose command registry govee-cli generates.
The wake-up was later moved by the operator to a 05:45–06:20 white fade (d2), and an 08:00
lamp-off was added (d3). Both rules are live and never committed.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Reinstall govee on spark from govee-cli main
- `t2` — Run govee serve always-on on spark with a culture-rules client token
- `t3` — Import the lamp runner actor into the live store and prove one step drives the lamp
- `t4` — Configure the wake-up workflow and its 06:20 schedule rule live, disabled
- `t5` — Docs: lamp setup guide in culture-rules, version bump and PR
- `t6` — Verify the first 06:20 wake-up run and file the evidence

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | govee reinstalled on spark from govee-cli main; `govee rules actor` runs (e1). Upgraded again to 0.12.0 after govee-cli #4 (d1). |
| `t2` | delivered | `govee-serve.service` is enabled and active as a user unit and survives a restart. The runner reaches the lamp through serve with the `culture-rules` token (e2, e3, e7). |
| `t3` | delivered | The live actor `spark-govee` holds govee-cli's generated commands: 8, then 9 after d1. One step drove the real lamp (e4, e5, e6, e8). |
| `t4` | delivered | The live rule and workflow `morning-wake-up` are configured on spark: 05:45 Asia/Jerusalem with `lamp_brightness` 1, `lamp_temp` 5000 and `lamp_fade_on_to` 100 over 2100 s (d2). The rule was enabled at once (d3), not left disabled. `morning-lamp-off` was added at 08:00 (d3). Every lamp step is idempotent (d4). See e9, e10 and e12. |
| `t5` | delivered | `docs/operations/govee-lamp.md`, the spec and the plan, version 0.38.2. PR #123, commit `3660074`. |
| `t6` | delivered | The first wake-up ran on 2026-10-31 at 05:45:03 IST (the d2 time) and succeeded. The lamp-off at 08:00 succeeded too (e13). Both ran again on 2026-11-01. |

## Mid-work Decisions

- `d1` — Before t4, govee-cli gained `govee light on --brightness N` and the rules-actor command
  `lamp_fade_on_to` {pct, seconds}. The actor was regenerated and updated live (9 commands).
  Reason: `lamp_fade_on` fades to the brightness the last fade-out remembered, so a wake-up
  could not choose its target. The operator chose this on 2026-10-30.
- `d2` — The wake-up starts at 05:45 Asia/Jerusalem and fades over 2100 s to full white
  (5000 K) at 06:20, in three steps. Reason: the operator asked for "a daily wakeup for 5:45
  to 6:20 with white morning hue". Brightness 1 comes first so the fade always starts from 1 %.
- `d3` — A second live rule, `morning-lamp-off`, turns the lamp off at 08:00. `morning-wake-up`
  was enabled before govee-cli #4 merged. Reason: the operator asked on 2026-10-30.
- `d4` — `config.idempotent` true on every live lamp step. Reason: code review of #123. Without
  it a node restart mid-step fails `unsafe_retry`, and every lamp command is an idempotent
  setter. The operator approved on 2026-10-30.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|------------------------|-----------------|
| `t4` (`d1`) | `lamp_fade_on` fades to the brightness the last fade-out remembered (1 % on 2026-10-30), so a wake-up cannot choose its target; govee-cli owns the command list (c5). The operator chose this option on 2026-10-30. | `acceptable` |
| `t4` (`d2`) | The operator asked for a 5:45 to 6:20 white fade. Brightness 1 first, so the fade always starts from 1 % (code review finding 4: on a lit lamp `lamp_fade_on_to` would dim). | `acceptable` |
| `t4` (`d3`) | The operator asked to apply the rule and add an 08:00 lamp-off. Until govee-cli #4 merged and spark-govee was updated, the wake-up's rise step would fail. Both happened before the first run. | `risky` |
| `t4` (`d4`) | Code review of #123: without it a node restart or lost claim mid-step fails the step `unsafe_retry`. The operator approved on 2026-10-30. | `acceptable` |
| `t6` | It verified the 05:45 run that d2 set, not a 06:20 run. o11's text still says 06:20. | `acceptable` |

## Evidence

- live: e1–e13 (e1–e15 and deltas b1–b4 are all approved): the actor, serve, the token, the runner commands, the wake-up
  and lamp-off rules, and the first morning on 2026-10-31.
- live: `morning-wake-up` runs `run-e1e2f1ed…` (2026-10-31 03:45:03Z) and `run-f8fbd81f…`
  (2026-11-01 03:45:06Z) succeeded. `morning-lamp-off` runs `run-d819b324…` (2026-10-31
  06:00:07Z) and `run-a26ee6aa…` (2026-11-01 06:00:03Z) succeeded.
- git: e14 — `git diff --name-only 3660074^ 3660074` changes 0 files under `culture_rules/`,
  `web/` or `api/`.
- git: e15 — `git grep` finds no actor JSON, wake-up workflow or rule committed in culture-rules or
  govee-cli `1c20c44`. Only a one-step example in `docs/operations/govee-lamp.md` and the
  generator in govee-cli.
- commits: `3660074` (#123, 0.38.2); govee-cli `1c20c44` (#4, 0.12.0).
- PRs: culture-rules #123; govee-cli #3, #4.

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| The lamp is a `runner` actor (`spark-govee`) any workflow drives by named `lamp_*` commands, generated by govee-cli | high | e4, e8 (stored commands equal the generated ones) · e5, e11 (the real lamp changed) |
| govee serve runs always-on on spark and the runner reaches the lamp through it with the `culture-rules` token | high | e2, e3, e7 |
| A daily schedule fades the lamp in each morning (05:45–06:20, d2) and turns it off at 08:00 (d3) | high | e10, e13 · runs on 2026-10-31 and 2026-11-01 |
| No culture-rules code changed, and no actor, workflow or rule is committed | high | e14, e15 · PR #123 |
| A long fade survives a node restart without failing `unsafe_retry` | medium | e12 shows the flag set; no restart during a fade was observed |

## Remaining Work / Follow-up

- Open risk: if govee serve is down at 05:45, the runner falls back to direct LAN and holds
  the rise step for the whole fade (timeout 3660 s) instead of failing fast. Nonblocking,
  not hit so far.
- Out of scope: after a fade-out the lamp rests at brightness 1. The wake-up's first step
  sets brightness 1, so the fade does not depend on it.
- From the session, not yet filed: the govee-cli test suite writes to the real stats DB.
  File it on govee-cli.
