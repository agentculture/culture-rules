# The Govee lamp as a runner actor

The Govee H6076 floor lamp is a `runner` actor: a set of named commands that run
[govee-cli](https://github.com/agentculture/govee-cli) on the machine next to the lamp.
culture-rules gains no code for it; a workflow step calls a command by name through
`machine.command` (spec `docs/specs/2026-10-30-govee-lamp-actor-and-wake-up.md`).

govee-cli owns the actor definition. `govee rules actor` prints it, and the commands, their
params and the import steps are documented in govee-cli's
[docs/culture-rules/README.md](https://github.com/agentculture/govee-cli/blob/main/docs/culture-rules/README.md).
No definition is committed here, because every `argv[0]` is the absolute path of `govee`
on that machine.

The spec and plan are the contract as confirmed. The approved deviations in
`.devague/deliveries/govee-lamp-actor-and-wake-up.json` changed it: govee-cli gained
`lamp_fade_on_to` (d1, 9 commands), the wake-up runs from 05:45 to 06:20 (d2), a lamp-off
rule runs at 08:00 (d3), and the lamp steps are idempotent (d4). This guide describes the
result.

The setup below is the one on `spark`, which runs the lamp.

## 1. Install govee-cli

As the user the engine node runs as, from a checkout of govee-cli:

```bash
uv tool install --force --reinstall /path/to/govee-cli
govee rules actor --help        # the `rules` verb needs 0.11.0 or later
```

`lamp_fade_on_to` (fade on to a named level) needs 0.12.0 or later. `uv tool install` puts
`govee` in `~/.local/bin` unless `UV_TOOL_BIN_DIR` or `XDG_BIN_HOME` says otherwise; the
unit below runs `%h/.local/bin/govee`, so change its `ExecStart` if `command -v govee` is
elsewhere.

## 2. Keep `govee serve` running

While `govee serve` runs, every lamp command routes through it: fades run inside serve and
outlive the command, so a 35-minute fade does not hold the workflow step, and the cloud API
key only has to live in serve's environment. Without serve, a long fade blocks its step until
it finishes.

On spark serve is a user unit, `~/.config/systemd/user/govee-serve.service`, copied from
govee-cli's `docs/systemd/govee-serve.service`:

```bash
systemctl --user daemon-reload
systemctl --user enable --now govee-serve
systemctl --user status govee-serve
```

The unit reads `~/.config/govee-cli/env` (mode 0600, one `NAME=value` per line):

| Variable | Needed for |
|----------|------------|
| `GOVEE_API_KEY` | `lamp_scene` (scenes are cloud only) and the cloud fallback when the LAN path fails. Without it every other command still works over the LAN. |
| `GOVEE_TRUSTED_NETWORKS` | Optional. A device on a trusted network needs no token: with `lan,tailscale`, any host on the home LAN's /24 or on the tailnet can drive the lamp. Leave it unset to require a token from every client. |

On spark the file holds only `GOVEE_TRUSTED_NETWORKS=lan,tailscale` (carried over from the
serve it replaced), so scenes do not work there yet. Only one serve can own the port (8742): stop any
serve started by hand before enabling the unit, or the unit fails with "Address already in
use".

## 3. Save a client token for the runner

Runner commands start with only `PATH=/usr/local/bin:/usr/bin:/bin` and `LANG`, so there is
no `GOVEE_TOKEN` in their environment. Save a token to the file govee reads instead, once,
as the same user:

```bash
govee token new --name culture-rules --save
govee token list
```

## 4. Create the actor

Generate the definition on the lamp's machine into a file only you can write, read it, then
create it. Writes are dry runs until `--apply`:

```bash
actor_json="$(mktemp -d)/govee-actor.json"
govee rules actor --bin "$(command -v govee)" --machine spark > "$actor_json"
culture-rules actors create --body @"$actor_json"
culture-rules actors create --body @"$actor_json" --apply
```

On spark the actor is `spark-govee`. When a new govee-cli adds commands, regenerate the
definition and update the actor the same way (`culture-rules actors update spark-govee
--body @"$actor_json"`, then `--apply`), and check that the stored
`params.commands` equal the generated ones.

## Using it in a workflow

A step is a builtin code step that runs a `machine.command` action, placed on the lamp's
machine:

```json
{
  "kind": "code",
  "placement": {"machine": "spark"},
  "config": {
    "builtin": "action",
    "idempotent": true,
    "action": {
      "kind": "machine.command",
      "params": {"actor": "spark-govee", "command": "lamp_temp", "args": {"kelvin": 5000}}
    }
  }
}
```

Set `config.idempotent: true` on every lamp step. Each command sets a state, so running it
twice is harmless, but the runner cannot deduplicate a retry: without the flag, a node
restart or a lost claim while the step runs fails it with `unsafe_retry` instead of running
it again.

A fade step (`lamp_fade_on`, `lamp_fade_off`, `lamp_fade_on_to`) also wants `"timeout_s":
3700` on the step. With serve running a fade returns at once, but without serve it blocks
for its whole length, and the step's default budget (3600 s) is shorter than govee-cli's
fade timeout (3660 s) for a fade of up to 3600 s.

Every command passes `--source rules`, so `govee stats` and the console show these changes
as Rules, and each run of a step is one event in govee's log.

A wake-up is three steps in a row: `lamp_brightness` at 1, `lamp_temp` at the colour
temperature, then `lamp_fade_on_to` with the final `pct` and the fade length in `seconds`.
Setting brightness 1 first makes the fade start from the dimmest level even when the lamp
was left on.

The flows that use the lamp, such as a daily wake-up and its lamp-off, are configured live
in the store and never committed.

## Checking a run

```bash
culture-rules runs list --rule-id RULE --json     # the run and its status
govee stats --json                                # recent_events: source, verb, request, status
journalctl --user -u govee-serve                  # every request serve answered
```

Changes made with the lamp's own button or the Govee Home app do not go through serve, so
they appear in none of these.
