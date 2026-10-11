# Rolling out a release

`culture-rules rollout` rolls a release out to every node, one step per command
(#45). It replaces the hand-written per-release scripts (`rollout-0186.sh` to
`rollout-0188.sh`, kept on #45 for reference). `culture-rules explain rollout
pause` (or any rollout verb) prints the step-by-step note; this page is the
operator's runbook.

## Before you start

- **Run the new release's CLI.** Build the wheel (`npm ci && npm run build` in
  `web/`, then `CULTURE_RULES_REQUIRE_WEB=1 uv build --wheel`) and install it into a
  venv of its own. The release is that wheel's own `release.json` version, and every
  step records itself in the rollout document `/rollouts/<version>`. `--wheel` must
  name that wheel (`culture_rules-<version>-...whl`); the CLI never picks one up from a
  host.
- **`release.json` names everything new since the last live release.** Its
  `new_rules` and `new_variables` list every rule and variable added since the last
  release that was rolled out live, not only this release's own. So a release that
  was skipped (never rolled out) has its rules and variables carried forward, and
  `enable` and `seed` still reach them.
- **The token comes only from the environment.** The admin token is the hidden grant
  secret `RULES_CULTURE_RULES_SERVICE_TOKEN`; a `grant:` reference cannot resolve it,
  so inject it:

  ```bash
  export CULTURE_RULES_API_URL=...   # the LAN listener; never committed
  cr() { grant run --inject CULTURE_RULES_TOKEN=RULES_CULTURE_RULES_SERVICE_TOKEN -- \
           /path/to/release-venv/bin/culture-rules "$@"; }
  ```

- **Every step is a dry run until `--apply`.** Run it without `--apply` first and
  read what it would do (the exact ssh/scp argv for `upgrade`). Guards refuse in a dry
  run too, so a clean dry run means `--apply` will go ahead.
- **Auto mode blocks production deploys** in Claude Code, so the operator runs each
  step; nothing chains steps and there is no `all` verb.

## Bootstrapping the first release

The rollout CLI needs the API's `/rollouts` endpoints, and an API older than the
release that ships the CLI (0.18.12) has none. Against such an API every rollout
step that writes the rollout document (`pause`'s snapshot, each step record,
`upgrade`'s start record) refuses with exit 2: "the API at `<url>` has no
/rollouts endpoints (it predates the rollout CLI)". `upgrade` still proves the
admin role before anything runs, so on an old API it runs nothing. `cr rollout
plan --wheel "$WHEEL"` prints the API's version (from `/health`; an old API names
none) and says when the endpoints are missing.

So 0.18.12 is rolled out once by hand, with a script in the style of the earlier
ones (`rollout-0186.sh` to `rollout-0188.sh` on #45), each step dry-run first:

1. Wait until no run is live: `cr rollout idle` must report none (it is
   read-only and works on the old API). `cr runs pause` only sets the pause
   flag, without `rollout pause`'s live-run guard, and the upgrades restart the
   nodes, which would interrupt a running chain. Then list the enabled rules into
   a file (`cr rules list --json`, keeping the ids whose `enabled` is true) and
   pause the engine: `cr runs pause --apply`.
2. Upgrade spark's shared venv first (`uv pip install --reinstall-package
   culture-rules --no-deps "$WHEEL"` into the venv the API unit runs, then
   restart `culture-rules-api` and `culture-rules-node`), then thor, orin and
   spark2 with `install.sh --apply`, `--wheel` and the flags in the
   [install records table](#the-machine-install-records).
3. Seed the new variables with `seed-variables.sh`. It runs `$CULTURE_RULES`
   (default `culture-rules`) directly, so `cr()` does not authenticate it: point
   `CULTURE_RULES` at the release CLI's absolute path and run the script under
   `grant`:

   ```bash
   CULTURE_RULES=/path/to/release-venv/bin/culture-rules \
     grant run --inject CULTURE_RULES_TOKEN=RULES_CULTURE_RULES_SERVICE_TOKEN -- \
     bash docs/rules/pr-fixer/seed-variables.sh --apply
   ```

4. Import the bundle (`docs/rules/pr-fixer`, or `pr-fixer/` in the installed
   wheel): `cr workflows import <bundle> --apply`, then `cr rules import <bundle>
   --apply`.
5. Re-enable each rule in the recorded list, plus the release's new rules
   (`release.json`): `cr rules enable <id> --apply`.
6. Resume: `cr runs resume --apply`.

Then set each machine's `install` section (below). The bootstrap writes no
rollout document, so check it by hand rather than with `rollout verify`:
each node's `engine_version` (`GET /machines/status`), `cr runs controls` and
the enabled rules. Every later
release uses the rollout CLI.

## The machine install records

`rollout upgrade` reads each host's flags from its machine record's `install`
section, so a forgotten flag can no longer drop a node secret. Set them once with
`cr machines show <name> --json`, adding `install` to the record and
`cr machines update --body @file.json --apply` (the whole record, its `name`
included; leave out `id` and `updated_at`, which the API refuses). The values below are the hand-run scripts' flags; `secrets` are grant
secret **names**, never values.

| Host | `install` section |
|---|---|
| spark | `{"mode": "shared_venv"}`: the API and node share one venv with hand-edited units, so `install.sh` never runs there |
| thor | `{"mode": "install_sh", "node_name": "thor"}` |
| orin | `{"mode": "install_sh", "node_name": "orin"}` |
| spark2 | see below |

```json
{
  "mode": "install_sh",
  "node_name": "spark2",
  "secrets": ["RULES_QWEN_FIXER_TOKEN", "RULES_GITHUB_APP_PRIVATE_KEY"],
  "gate_run_as": "sudo -n -u culture-fixer -- /usr/bin/env PATH=<fixer-home>/.local/bin:/usr/local/bin:/usr/bin:/bin"
}
```

`<fixer-home>` is the `culture-fixer` account's home directory on spark2.

`extras`, `wheelhouse`, `python`, `events_host` and `events_port` are optional; a
field left out takes `install.sh`'s default. The ssh target is the record's `address`,
else its name.

The install section is code the host runs, so:

- **Only an admin may add, change or remove it.** An editor saving a machine whose
  `install` differs from the stored one gets 403 `install_requires_admin`; other
  machine fields stay editor.
- **Every field is one word.** `node_name`, `events_host`, `wheelhouse`, `python`,
  each `extras` entry and each `secrets` name may not hold a control character or
  whitespace, and `gate_run_as` may not hold a control character. `upgrade` checks
  them again before it builds any command, and refuses an `address` that is not a
  plain host name (one starting with `-` could read as an ssh option).

## The steps, in order

```bash
cr rollout plan --wheel "$WHEEL"           # what will happen; writes nothing
cr rollout idle                            # non-terminal runs; pause needs none
cr rollout pause --apply                   # snapshot the enabled rules, then pause
cr rollout upgrade --host spark --wheel "$WHEEL" --apply    # shared venv first
cr rollout upgrade --host thor --wheel "$WHEEL" --apply     # then each install.sh host
cr rollout upgrade --host orin --wheel "$WHEEL" --apply
cr rollout upgrade --host spark2 --wheel "$WHEEL" --apply
cr rollout seed --apply                    # new variables from the wheel's variables.json
cr rollout import --apply                  # the bundle's workflows and rules
cr rollout enable --apply                  # the snapshot plus the release's new rules
cr rollout resume --apply
cr rollout verify                          # exit 0 only when every check passes
```

`cr rollout show <version>` prints the rollout document: the snapshot and each
applied step's outcome.

### The rollout document

Each applied step is recorded with `POST /rollouts/<version>/steps/<step>`. The
server merges that one step into the document in a store transaction and stamps
`applied_at` with its own clock, so two commands recording at once never lose
each other's steps. `pause` records the snapshot with `POST
/rollouts/<version>/snapshot`: the first one wins, and a later one answers 409
`snapshot_recorded`, so a second `pause` keeps the recorded snapshot. A step
re-recorded replaces that step, except `resume_requested`, which the first
record keeps (409 `step_recorded`). Both
endpoints are admin and audited. `PUT /rollouts/<version>` (admin) still replaces
a whole document, but the CLI does not use it.

### Step details

- **`upgrade --apply` proves the admin role first.** It records
  `upgrade:<host>` as started before it runs anything, and a token without the
  admin role is refused there (exit 1, nothing run). A dry run runs nothing.
- **The shared venv is resolved from the running units.** On the shared-venv
  host, `upgrade --apply` reads the API unit's `ExecStart` D-Bus property (`busctl
  --user --json=short get-property ... ExecStart`), which is the exact argv:
  `systemctl show` joins the arguments with bare spaces, so a venv path with a
  space would be ambiguous there. The executable must be
  `<venv>/bin/culture-rules`, or `grant` running `grant run [--inject VAR=NAME]...
  -- <venv>/bin/culture-rules ...`; any other executable (`env`, `python`) is
  refused even when an argument names `.../bin/culture-rules`. The unit must
  have one `ExecStart`. The node unit must run the same venv; otherwise, or when
  no venv can be read, the upgrade is refused naming both. No data directory is
  assumed, and the dry run prints `<venv>` in its place.
- **`enable` refuses while a snapshot rule is absent.** A rule recorded in the
  snapshot that no longer exists (deleted since the pause) is refused before
  anything changes, in a dry run too. Restore it (from a backup, or `cr rules
  create --body @rule.json --apply`) or re-import the bundle that carries it
  (`cr rollout import --apply`), then re-run `enable`.
- **`enable` refuses extra enabled rules.** A rule enabled outside the snapshot
  plus the new rules is refused before anything changes. Disable each one with
  `cr rules disable <id> --apply`. A rule that belongs enabled must be in the
  snapshot, which `pause` records once per release.
- **`resume` records its boundary first.** It records `resume_requested`
  (server time), then resumes the engine, then records `resume`.
  `resume_requested` is recorded once per release: the server keeps the first
  record and answers a later one 409 `step_recorded` in the same transaction, so
  a second (or concurrent) `resume` keeps the earlier boundary and goes on.
- **`verify` reads every failed run since that boundary.** The window opens at
  `resume_requested`, or at `resume` for a document written before that step
  existed. It pages through `GET /runs?status=failed&since=...` until
  `next_cursor` is null, ordered by finish time, so a run created before the
  resume that fails after it is still counted. If the listing is incomplete the
  check fails: "could not read every failed run".
- **Versions order by PEP 440.** That covers `N.N`, `aN`/`bN`/`rcN`, `.postN`,
  `.devN` and `+local`. A version that does not parse counts as older, so
  `enable` refuses it and `verify` fails. A step that changes a live actor (the 0.18.6 rollout set
`qwen-fixer`'s `concurrency_pool`) is not a rollout verb: use
`cr actors update ... --apply` between `import` and `enable`.

## What each lesson became

The lessons from the hand-run rollouts (#45), and what now enforces each one:

| Lesson | Enforced by |
|---|---|
| Restore what was on, not a fixed list | `pause` stores the enabled-rule snapshot in the rollout document before pausing; `enable` re-enables exactly the snapshot plus `release.json`'s new rules and lists every rule left disabled |
| Keep each host's install flags in one place | the machine record's `install` section; `upgrade` refuses a record without one |
| Name the wheel | `--wheel` is required and its version must equal the release; the scp target is a per-version directory and `install.sh` gets `--wheel` with that path |
| Publish a release's new rules and variables with it | `release.json` and `variables.json` ship in the wheel (`culture_rules/deploy_data`); `seed` and `import` read them, no checkout needed |
| Wait for a running chain | `pause` refuses while a run is not terminal (`--force` is the only override) |
| Seed before import, upgrade before enable | `import` refuses until this release's `seed` step is recorded; `enable` refuses while an online node's `engine_version` is older than the release (PEP 440 order; an unparseable version counts as older) |
| Restore exactly what was on | `enable` refuses while a rule outside the snapshot plus the new rules is enabled, or while a snapshot rule no longer exists, before it changes anything |
| The operator runs each step | one command per step, dry run first; `upgrade`'s dry run prints the exact argv and opens no connection |

## Limits

- `idle`, `plan` and `verify` are read-only, so the rollout document records the
  other steps but not these; `verify`'s output is the evidence (deviation d1 of the
  `rollout-cli` plan).
- `import` and `enable` are editor verbs, but recording their step needs the admin
  token; with an editor token they run and report the step as not recorded (d2).
  `upgrade` is different: it acts outside the API, so it records its start first
  and runs nothing without the admin role.
- `upgrade` on the shared-venv host runs locally and refuses unless the record's name
  is this host's short hostname.
