# Build Plan — Rollout CLI

slug: `rollout-cli` · status: `exported` · from frame: `rollout-cli`

> culture-rules rollout: the operator rolls a release out to every node with one dry-run-first CLI noun, step by step, instead of hand-written per-release scripts

## Tasks

### t1 — Machine records carry a typed install section

- instruction: `culture_rules`/model/machine.py (+ a nested dataclass), model/validate.py `_check_machine`, regenerate schemas with uv run python -m `culture_rules`.model.schema --write schemas; no API route change (definition bodies are dicts)
- covers: c8, h9
- acceptance:
  - Machine gains an optional install section {mode: `install_sh`|`shared_venv`, `node_name`, secrets\[\], `gate_run_as`, extras, wheelhouse, python, `events_host`, `events_port`}; strict parse refuses unknown keys; schemas/machine.schema.json regenerated and tests/model/`test_schemas.py` green
  - A validator refuses a secrets entry that is not a grant name (\[A-Z0-`9_`\]+) and a `gate_run_as` that is not a sudo -n -u prefix
  - Existing machine documents without install still load and save unchanged

### t2 — The wheel carries install.sh, the pr-fixer bundle and the release manifests

- instruction: Follow `hatch_build.py`'s `web_dist` pattern; `culture_rules`/`deploy_data`/`__init__.py` exposes paths via importlib.resources; keep tests/packaging/`test_wheel.py`'s skip rules
- covers: c10, h11, h2
- acceptance:
  - `hatch_build` force-includes deploy/node/install.sh and docs/rules/pr-fixer (workflows, rules, actors) as `culture_rules`/`deploy_data`; a test opens the built wheel and finds install.sh, the bundle and both manifests
  - docs/rules/pr-fixer/variables.json holds every seed variable {name, value, description}; seed-variables.sh reads it and its behaviour (skip existing, --force, --apply) is unchanged
  - docs/rules/pr-fixer/release.json names the release version, its new rules and new variables; a test checks every name exists in the bundle or variables.json and that its version equals pyproject's

### t3 — API: a rollout document and the versions verify needs

- instruction: server/app.py `_register_rollouts`; server/status.py; ops/health.py; auth/policy.py default makes PUT admin; scripts/export-openapi.py --write
- covers: c9, c12
- acceptance:
  - New collection rollouts (in `_ALL_COLLECTIONS`) with GET /rollouts, GET /rollouts/{id} (viewer) and PUT /rollouts/{id} (admin, audited); body {version, snapshot\[\], steps{name: {`applied_at`, outcome}}}; strict validation
  - GET /machines/status items gain `engine_version` (from the heartbeat, null when unknown); GET /health gains version (the API's package version)
  - api/openapi.json regenerated; the contract test, authz table test and web typecheck pass

### t4 — CLI rollout noun: plan, idle, pause, seed, import, enable, resume, verify

- instruction: Template: cli/`_commands`/runs.py; write() from `_nounlib` for the dry-run envelope; the CLI talks to the API only (no store imports); no verb chains steps
- depends on: t1, t2, t3
- covers: c1, h1, c2, h5, c4, h7, c9, h10, c12, h12, c13, h4, c15, h14
- acceptance:
  - `culture_rules`/cli/`_commands`/rollout.py registers the verbs with roles (plan, idle, verify viewer; import, enable editor; pause, resume, seed admin); each is a dry run until --apply, takes --json and has an MCP tool (parity test green)
  - pause stores the enabled-rule snapshot in the rollout document before POST /controls/pause and refuses while a run is non-terminal unless --force; pause and resume report an engine already paused or resumed instead of failing
  - seed and import read variables.json and the bundle from the wheel's `deploy_data` (or --bundle DIR); import refuses before this release's seed step is recorded; enable refuses while any node's `engine_version` is older than the release and re-enables exactly the snapshot plus release.json's new rules, listing every rule left disabled
  - verify exits 0 only when node versions and the API version equal the release, the engine is not paused, enabled rules equal snapshot plus new rules and no run failed with `no_builtin` since resume; otherwise it lists each failed check and exits 1
  - explain catalog blurb and verb notes, learn prose, CLAUDE.md, QWEN.md, AGENTS.override.md and AGENTS.colleague.md name the noun; tests/cli/`test_registry.py` NOUNS updated

### t5 — CLI rollout upgrade --host over ssh and scp, or the shared venv on spark

- instruction: Follow machines/probe.py's injected runner; argv lists only (no shell strings); ssh -o BatchMode=yes; # nosec B607 or shutil.which; the API URL and token never cross ssh
- depends on: t1, t4
- covers: c11, h3, c8, h9, h1
- acceptance:
  - upgrade --host NAME --wheel PATH reads the machine record; without --apply it prints the exact scp/ssh argv (or the shared-venv commands) and opens no connection; a record without install exits 1 naming the missing section
  - With --apply, `install_sh` mode copies the named wheel and install.sh to a per-version directory and runs install.sh --node-name --wheel `<that path>` plus one --secret per grant name, --gate-run-as, --extras, --wheelhouse, --python, --events-host/port from the record, then reads the installed version and the unit's ActiveState and ActiveEnterTimestamp; `shared_venv` mode runs uv pip install --reinstall-package culture-rules --no-deps WHEEL into the venv and restarts culture-rules-api and culture-rules-node; install.sh never runs for `shared_venv`
  - The runner is injectable; the parity test and every unit test use a fake runner; the step outcome is written to the rollout document

### t6 — Docs, CHANGELOG and the secrets boundary test

- instruction: Write the machine install records as JSON examples in the doc (no secrets, grant names only)
- depends on: t4, t5
- covers: c14, h13, c3, h6, c5, h8
- acceptance:
  - docs/operations/rollout.md documents each verb, the machine install records for spark, thor, orin, spark2 (from the hand-run scripts), and maps each lesson in #45 to the guard or field that enforces it
  - A test drives a full dry run and an applied fake-runner rollout and asserts no stored document, output line or argv contains a secret value (only grant names); scan-secrets stays green; no tailnet URL is committed
  - CHANGELOG entry and version bump

### t7 — Roll the next release out with culture-rules rollout only

- instruction: Operator-run (auto mode blocks deploys): Claude prepares the machine install records (rollout dry runs first) and the operator runs each step with grant run --inject
- depends on: t6
- covers: c17, h15
- acceptance:
  - The operator rolls the release after this one out to spark, thor, orin and spark2 with rollout verbs only; its rollout document records every step applied and rollout verify exits 0
