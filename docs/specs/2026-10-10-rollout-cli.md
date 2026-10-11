# Rollout CLI

> culture-rules rollout: the operator rolls a release out to every node with one dry-run-first CLI noun, step by step, instead of hand-written per-release scripts

## Audience

- The operator who rolls culture-rules releases out to spark, thor, orin and spark2 (and Claude Code preparing a rollout: auto mode blocks production deploys, so the operator runs each step)

## Before → After

- Before: Each release was rolled out from a hand-written script (rollout-0186.sh, rollout-0187.sh, rollout-0188.sh) with the hosts' install flags, the enabled-rule snapshot file and the release's new rules hard-coded; a forgotten install.sh flag silently drops a node secret, and the wrong wheel can be picked up
- After: culture-rules rollout plan --wheel `<whl>` prints the steps, every host with its install flags, the rules to re-enable and the release's new rules and variables; culture-rules rollout `<step>` runs one step: idle, pause, upgrade --host `<name>`, seed, import, enable, resume, verify; every step is a dry run until --apply, takes --json, and has an MCP tool

## Why it matters

- Rollouts happen every release (five on 2026-10-10 alone); encoding the lessons (restore what was on, flags in one place, name the wheel, wait for idle) removes the steps that went wrong by hand

## Requirements

- The machine record gains a typed install section (`node_name`, secrets, `gate_run_as`, extras, wheelhouse, python, `events_host`, `events_port`, and mode: `install_sh` or `shared_venv`); spark, whose API and node share one venv with hand-edited units, uses `shared_venv`: uv pip install the wheel into that venv and restart culture-rules-api and culture-rules-node, never install.sh
  - honesty: A machine record without an install section cannot be upgraded (exit 1 naming the missing section); spark's record uses `shared_venv` and the CLI never runs install.sh for it
- A rollout document in the store (new collection and routes: GET viewer, writes admin) holds the release version, the enabled-rule snapshot taken at pause and each step's outcome; enable restores the snapshot plus the release's new rules and reports which rules stay disabled
  - honesty: pause writes the snapshot before pausing; enable re-enables exactly the snapshot plus the manifest's new rules and lists every rule left disabled
- The wheel carries what a rollout needs: install.sh, the pr-fixer bundle (workflows, rules) and a release manifest naming the release's new rules and new variables with their seed values, as package data; seed and import read them from the wheel, so no checkout is needed
  - honesty: The wheel built by CI contains install.sh, the bundle and the manifest; a test opens the built wheel and finds them
- upgrade runs ssh and scp through an injectable runner, only with --apply; it uses the wheel the operator names (--wheel), never one found on the host; afterwards it reads the host's installed version and its unit's ActiveState and ActiveEnterTimestamp
  - honesty: Without --apply, upgrade never opens an ssh connection; tests run it against a fake runner
- verify checks every node's `engine_version` (GET /machines/status gains `engine_version` from the heartbeat) and the API's own version against the wheel, the engine not paused, enabled rules equal to the snapshot plus the release's new rules, and no run failed with `no_builtin` since resume
  - honesty: verify exits 0 only when every check passes and lists each failed check otherwise
- Guards: pause refuses while a run is non-terminal (idle shows them) unless --force; import refuses before seed has run for this release; enable refuses while a node runs an older version; pause and resume are idempotent (an engine already paused or resumed is reported, not an error)
  - honesty: Each guard refuses with exit 1 and a remediation line naming the step to run first, and --force is the only override where one is allowed

## Honesty conditions

- Every step of the hand-run rollout-0188.sh (idle, pause, spark, nodes, seed, import, enable, resume) has a rollout verb that does the same thing with --apply, and its dry run names the exact commands or API calls it would make
- No step needs a checkout of the repository: the wheel alone carries install.sh, the bundle and the release manifest
- Every verb is usable by the operator from a shell with the token injected by grant run, and as an MCP tool, with the same output
- The before state is the three committed-to-issue scripts (issue #45 comment) and the 2026-10-10 rollouts that used them
- rollout plan --wheel prints, without writing anything, the steps in order, each host with the install flags from its machine record, the snapshot rules and the release's new rules and variables from the wheel's manifest
- Each lesson listed in issue #45 maps to a guard or a stored field the CLI enforces, named in the docs
- A test scans the rollout code paths and the stored documents for secret values and finds only grant names; scan-secrets stays green
- No verb runs more than one step; there is no 'all' verb
- The release after this one has its rollout recorded as a stored rollout document with every step applied and verify green

## Success signals

- The next release after this one is rolled out to all four nodes with culture-rules rollout commands only, no hand-written script, and rollout verify reports every check green

## Scope / boundaries

- The admin token reaches the CLI only through the environment (grant run --inject `CULTURE_RULES_TOKEN`=...); no secret value is stored on a machine record or a rollout document (secrets are grant names), printed, or committed; the tailnet API URL stays out of committed files
- Every step stays one command the operator runs and checks; nothing chains steps or runs a rollout unattended, and nodes never upgrade themselves

## Non-goals

- Automatic rollback, upgrading the MongoDB replica set or cultureagent bridges, and a rollout view in the web editor

## Scope exploration

- `s1` — `culture_rules/cli (registry, _nounlib, _build, verbs.py, __init__)`: A noun is a module with VERBS and register(); Verb(noun,name,summary,handler,params,mutating,role); write() gives the dry-run envelope; MCP tools are generated per verb; the parity test runs every handler with dummy args with and without apply against an in-memory API, so upgrade must fail on an unknown machine before any ssh; read-only verbs must be viewer
- `s2` — `machine model and routes`: Machine has no free-form field (additionalProperties false, strict parse), so install flags need a new typed field, a validator and a schema regen; PUT must omit id and `updated_at`; address can be the ssh target
- `s3` — `heartbeats and status`: Heartbeats store `engine_version` but GET /machines/status does not expose it and nothing reports the API's own version; verify needs both (openapi.json changes)
- `s4` — `deploy/node/install.sh and packaging`: install.sh flags: --node-name --wheel --ca-file --mongo-secret --secret (repeatable) --extras --wheelhouse --events-host/port --python --gate-run-as --apply; it rewrites node.env and the unit every run; the wheel ships only `culture_rules`, so install.sh and docs/rules must be added as package data (`hatch_build` pattern); spark's units are hand-edited, so install.sh must never run there
- `s5` — `API endpoints for the steps`: pause/resume are admin and answer 409 on repeat; rule enable is editor and is refused while an online node lacks variable support; import is POST /import (workflows before rules); seed is PUT /variables/{name} (admin) and the values live only in seed-variables.sh; a new collection must join `_ALL_COLLECTIONS` and an unlisted route is admin by default
- `s6` — `subprocess conventions`: No CLI module runs a subprocess today; agent.py and machines/probe.py inject a runner; bandit skips B603 but not B607, so ssh/scp need nosec B607 or shutil.which

## Decisions

- upgrade --host reaches thor, orin and spark2 over ssh: scp the wheel and install.sh, run install.sh with the host's flags (operator, 2026-10-10)
- The per-host install flags live on the machine records in the store; the enabled-rules snapshot taken at pause lives in a rollout document in the store (operator, 2026-10-10)

## Hard questions

- Should seed values move out of seed-variables.sh into a JSON manifest (the script reading it), so the wheel and the script share one source? (resolved: Seed values move into a JSON manifest (docs/rules/pr-fixer/variables.json) shipped in the wheel; seed-variables.sh reads it (operator, 2026-10-10))
