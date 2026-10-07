# The PR fixer machine: an unprivileged account running the agent bridge

Task t18 (claims c30; honesty condition h24; deviation d8). This is the
operator recipe for the machine the PR fixer's agent runs on: **spark2**. One
cultureagent bridge, Qwen Code on cortex, runs as a systemd user unit of a
dedicated account, `culture-fixer`. The engine reaches it over the tailnet as
the agent actor `qwen-fixer`. Codex is not installed on spark2: it stays on
spark (d8).

**Steps marked *operator* are hand-turns.** They need root on spark2 or a
GitHub or SonarCloud login in a browser. Everything else runs as
`culture-fixer`, which never holds root.

## What the account may and may not touch

The agent runs tools as the bridge's Unix user. The bridge does not sandbox
the host (see cultureagent's `docs/bridges.md`, "Trust boundary"), so the
account is the boundary:

| The account can | The account cannot |
|---|---|
| fetch `https://github.com/agentculture/*` into its own caches and commit locally | push: the bridge refuses it and every cache's push URL is unusable; the GitHub App pushes after the engine's test gate |
| read PRs, checks and Actions logs with its own read-only GitHub token | read the engine node's `node.env`, grant store or the App private key (another user's mode-700 directories) |
| read SonarCloud issues with its own token, and call cortex with its own API key | use the operator's `gh`, `claude` or `codex` logins (they live in the operator's home, mode 750) |

Known limit: the bridge passes its environment on to the agent, and both run
as the same user. The agent can therefore read the bridge's own bearer token
and could call its own bridge. That reaches nothing the agent cannot already
do as that user, and it is tracked as plan risk r11.

## Where the secrets live

All of them are in **grant**, in `culture-fixer`'s own per-user store; none
is in a file, a config or a `gh` login. An administrator writes into that
store with `sudo grant set NAME - --hidden --user culture-fixer` (the value
from stdin). The unit injects them at exec time:

| grant name (in `culture-fixer`'s store) | Injected as | Used by |
|---|---|---|
| `FIXER_QWEN_BRIDGE_TOKEN` | `QWEN_BRIDGE_AUTH_TOKEN` | the bridge: the bearer token it requires |
| `FIXER_CORTEX_API_KEY` | `QWEN_CUSTOM_API_KEY_CORTEX` | Qwen Code: the cortex endpoint's key |
| `FIXER_GITHUB_TOKEN` | `GH_TOKEN` | the agent: `gh` and the GitHub API, read-only |
| `FIXER_SONAR_TOKEN` | `SONAR_TOKEN` | the agent: SonarCloud reads |

The engine node on spark2 holds the same bridge token in **its** store as
`RULES_QWEN_FIXER_TOKEN`, referenced by the actor as
`grant:RULES_QWEN_FIXER_TOKEN`.

## 1. Create the account (operator, root)

Copy `deploy/pr-fixer/` to spark2 (or use a checkout there), then:

```bash
sudo bash create-user.sh --authorize-key spark.pub            # plan only
sudo bash create-user.sh --authorize-key spark.pub --apply
```

It creates `culture-fixer` with its own group and home (mode 750), no sudo
and no operator groups, enables lingering so its unit starts at boot, and
lets the one given SSH key log in as the account, so the installer can run
there without root. Leave `--authorize-key` out to do step 3 yourself with
`sudo -iu culture-fixer`.

## 2. Seal the secrets (operator)

On spark2, from the operator account. The bridge token is generated once in
the node's store and copied into the account's store, so the two match:

```bash
G=~/.local/bin/grant
$G generate RULES_QWEN_FIXER_TOKEN --hidden --bytes 32 --encoding hex
$G run --inject T=RULES_QWEN_FIXER_TOKEN -- sh -c 'printf %s "$T"' \
  | sudo $G set FIXER_QWEN_BRIDGE_TOKEN - --hidden --user culture-fixer
# -e: fail instead of sealing the word "null" when the key is absent
jq -er '.env.QWEN_CUSTOM_API_KEY_OPENAI_HTTP_LOCALHOST_8000' ~/.qwen/settings.json \
  | tr -d '\n' | sudo $G set FIXER_CORTEX_API_KEY - --hidden --user culture-fixer
```

Then create two read-only tokens in a browser and paste each one in. `grant set`
reads stdin without a prompt, so use `read -rsp`, which prompts, hides the input and
seals no trailing newline:

- GitHub: a fine-grained token, resource owner `agentculture`, all
  repositories, permissions **Contents: Read, Pull requests: Read, Actions:
  Read, Commit statuses: Read** (Metadata: Read is implied). Fine-grained
  tokens have no Checks permission (it exists only for GitHub Apps); the engine
  reads check runs through the App and hands the failing ones to the agent, and
  Actions: Read covers the job logs.
  `read -rsp "GitHub token: " T && printf %s "$T" | sudo $G set FIXER_GITHUB_TOKEN - --hidden --user culture-fixer; unset T`
- SonarCloud: **My Account → Security → Generate token** (a user token).
  `read -rsp "Sonar token: " T && printf %s "$T" | sudo $G set FIXER_SONAR_TOKEN - --hidden --user culture-fixer; unset T`

## 3. Install the tools and the bridge (as `culture-fixer`)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh          # uv, into ~/.local/bin
~/.local/bin/uv tool install grant
# Node.js and Qwen Code 0.24.7 (the bridge's handshake accepts only listed versions)
npm install --prefix ~/.local/lib/qwen-code @qwen-code/qwen-code@0.24.7
ln -sf ~/.local/lib/qwen-code/node_modules/.bin/qwen ~/.local/bin/qwen
# gh, from the release tarball, into ~/.local/bin (reads GH_TOKEN; no gh login)
bash install.sh --host "<spark2 tailnet IP>"                # plan only
bash install.sh --host "<spark2 tailnet IP>" --apply
```

| Path | Content |
|---|---|
| `~/.local/share/cultureagent-bridges/venv` | `cultureagent==0.14.0` |
| `~/.config/cultureagent-bridges/qwen.json` (mode 600) | bind the tailnet IP on 8093; allowlist prefix `https://github.com/agentculture/`; one run at a time; `commit_author` the App bot |
| `~/.qwen/settings.json` (mode 600) | Qwen Code on cortex; the key comes from `QWEN_CUSTOM_API_KEY_CORTEX` |
| `~/.config/systemd/user/cultureagent-qwen-bridge.service` | `grant run --inject ... -- cultureagent-qwen-bridge --config ...` |

Every agent commit is authored as
`rules-culture-dev[bot] <337624453+rules-culture-dev[bot]@users.noreply.github.com>`,
the identity the App pushes as.

## 4. Give the node the bridge token and register the actor

On spark2, as the operator account, add the token to the engine node
(`deploy/node/install.sh --secret RULES_QWEN_FIXER_TOKEN`, alongside its other
`--secret` flags), then register the actor. `callback_url` is the API's LAN
base URL (the bridge posts to `POST /bridge-invocations/{id}/events`, which
checks the per-invocation token itself):

```json
{
  "id": "qwen-fixer",
  "name": "PR fixer (Qwen Code on cortex)",
  "kind": "agent",
  "machine": "spark2",
  "harness": "qwen",
  "model": "cortex",
  "params": {
    "bridge_url": "http://<spark2 tailnet IP>:8093",
    "callback_url": "http://<API LAN host>:<port>",
    "bridge_token": "grant:RULES_QWEN_FIXER_TOKEN",
    "model": "cortex",
    "mode": "yolo"
  }
}
```

The qwen bridge refuses a run without `mode`.

## 5. Let the node run the test gate as `culture-fixer` (operator, root; *planned*)

The fixer workflow's `gate` step (`kind: code`, `config.builtin: gate`,
`culture_rules/actors/gate.py`, task t13) runs on spark2's engine node. It
reads the `gate:` section of `culture.yaml` **from the PR's base commit**,
runs the diff guard, then runs the declared setup and test argv lists in a
fresh checkout of the agent's commit (never in the agent's worktree). It runs
nothing as the node user: every command that touches the worktree or the
checkout goes through the prefix in `CULTURE_RULES_GATE_RUN_AS`, and while
that is unset the step fails `gate_runner_unconfigured`.

Give the node user exactly one sudo grant, to run `env` as `culture-fixer`
without a password. Then put the prefix in the node's environment with the
node installer's `--gate-run-as` (re-run it with the flags the node was
installed with, plus this one). `PATH` must reach the account's
`~/.local/bin`, because sudo resets the environment:

```bash
echo 'spark2 ALL=(culture-fixer) NOPASSWD: /usr/bin/env' \
  | sudo tee /etc/sudoers.d/culture-rules-gate && sudo visudo -cf /etc/sudoers.d/culture-rules-gate
# as the node user (FIXER_HOME is the account's home: getent passwd culture-fixer | cut -d: -f6)
bash deploy/node/install.sh --node-name spark2 ...the flags it was installed with... \
  --gate-run-as 'sudo -n -u culture-fixer -- /usr/bin/env PATH=FIXER_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin'
# read the plan, then the same command with --apply
```

Write the absolute path in place of `FIXER_HOME`: `env` does not expand `~`.

Use the flag rather than editing `node.env` by hand: `install.sh` rewrites
`node.env` on every run, so a hand-added `CULTURE_RULES_GATE_RUN_AS` line
disappears at the next upgrade. The flag also changes the unit. By default
the unit sets `NoNewPrivileges=true`, which sets the kernel's
`no_new_privs` flag on the node and everything it starts, and with that flag
sudo refuses to run at all ("The "no new privileges" flag is set, which
prevents sudo from running as root"). A `--gate-run-as` prefix that starts
with `sudo` therefore writes `NoNewPrivileges=false`. Any other prefix keeps
`NoNewPrivileges=true`.

If you would rather not re-run the installer, a drop-in does the same for
the unit (the `CULTURE_RULES_GATE_RUN_AS` line still has to be in
`node.env`, and is lost at the next install without the flag):

```bash
mkdir -p "$HOME/.config/systemd/user/culture-rules-node.service.d"
printf '[Service]\nNoNewPrivileges=false\n' \
  > "$HOME/.config/systemd/user/culture-rules-node.service.d/gate-sudo.conf"
systemctl --user daemon-reload && systemctl --user restart culture-rules-node
```

At start the node checks for the conflict: when the prefix starts with
`sudo` and the node has `no_new_privs` set, it logs an error naming both
fixes, and every gate step fails `run_as_blocked` until the unit is fixed.
When a worktree command fails later, the gate probes the prefix with `true`.
If the probe fails too, the step fails `run_as_failed` (or `run_as_blocked`
when sudo names the flag) with the end of sudo's stderr. Otherwise it fails
`source_unavailable` with the end of git's stderr, for example a commit the
worktree does not have.

The gate appends `env -C <dir> -- <argv>` to that prefix, so each command
runs in that directory as `culture-fixer`, as an argv list, with no shell
anywhere. A prefix that starts with `ssh`, `su` or a shell is refused:
those would join argv into a shell command line.

What the grant means:

- **The node user can run anything as `culture-fixer`.** That is the point of
  the grant. `culture-fixer` is the less privileged account, and the reverse
  direction does not exist.
- **The PR's code never runs as the node user**, and never sees the node's
  `node.env`, grant store or App key.
- **On a timeout the node can only signal `sudo`.** sudo passes `SIGTERM` on
  to the command. A process that ignores it keeps running as `culture-fixer`.

The gate never reads git data in the worktree, which belongs to
`culture-fixer`, so git there would trip `safe.directory` for the node. As
`culture-fixer`, it streams one pack of the base, start and agent commits out
of the worktree (`git pack-objects --revs --stdout`). The pack goes into a
fresh bare repository that the node owns, where git checks every object's
hash. The diff guard and the base `culture.yaml` are read there, with no
hooks, no attributes and no global config.

The tests run in a fresh checkout, not in the worktree, because the agent
controls the worktree's index and untracked files. A `--skip-worktree` edit
or a stray `conftest.py` there would otherwise change what is tested. Each
gate run works like this:

1. As `culture-fixer`, the gate makes a directory with `mktemp -d` in that
   account's own temp space (mode 700, owned by `culture-fixer`).
2. It runs `git init` there and feeds the node-verified pack to
   `git index-pack --stdin`. The node opens the pack file (mode 600, in the
   node's own mode-700 temp directory) and passes it as `culture-fixer`'s
   stdin, so that account never reads a path the node owns.
3. It checks out the commit, verifies that `HEAD` is the commit and that
   `git status --porcelain --ignored` is empty, runs the gate, and removes
   the directory, also on failure.

Every git call in that checkout ignores `culture-fixer`'s own git config,
which the agent can edit. It runs with `GIT_CONFIG_GLOBAL=/dev/null`,
`GIT_CONFIG_NOSYSTEM=1`, an empty template and `core.hooksPath=/dev/null`, so
no hook runs. `culture-fixer` never needs read access to the bundle either:
only `github.push`, running as the node user, reads it.

When the verdict is `pass`, the gate writes a bundle of the commit for
`github.push` into `CULTURE_RULES_GATE_BUNDLE_DIR` (default
`~/.local/state/culture-rules/gate-bundles`). Bundles older than seven days
are pruned. The bundle path exists only on spark2, so `github.push` must run
there too.

Before a fixer run, seed the shared variable `fixer_protected_paths`. Its
patterns are added to the built-in floor, `.github/workflows/**`. While the
variable is unset, every verdict is `guard` with rule `protected_paths_unset`:

```bash
culture-rules variables set fixer_protected_paths --apply --value \
  '[".github/workflows/**", "sonar-project.properties", ".coveragerc", "setup.cfg", ".flake8", "pyproject.toml"]'
```

## 6. Verify

| Check | Expected |
|---|---|
| as `culture-fixer`: `cat ~spark2/.config/culture-rules/node.env`, `ls ~spark2/.qwen`, `grant list` | permission denied twice; `grant list` shows only the four `FIXER_*` names |
| `curl -s -o /dev/null -w '%{http_code}' http://<spark2 tailnet IP>:8093/v1/capabilities` | `401` |
| the same with `-H "Authorization: Bearer <token>"` | `200` and the capability document (`"pushes": false`) |
| as `culture-fixer`, through grant: `gh api repos/agentculture/culture-rules/pulls` | `200`; a `gh pr merge` or a push is refused |
| `culture-rules actors list` | `qwen-fixer`, machine `spark2` |

## 7. The fixer rules and workflow (t17)

The fixer is committed data in `docs/rules/pr-fixer/`, in the import format
(`rules/<id>.json`, `workflows/<id>.json`). JSON rather than YAML, so the import
works on an API without the `yaml` extra. Four rules, one trigger type each,
share one workflow (d13):

| Rule | Trigger | Extra condition |
|---|---|---|
| `pr-fixer-checks` | `github.pr.checks_settled` | `conclusion` neither `"success"` nor `"no_checks"` (a green head, or one with no counted check, is left alone) |
| `pr-fixer-comment` | `github.comment.created` | trusted author, not the App, `pr_enriched == true` |
| `pr-fixer-review` | `github.review.submitted` | trusted author, not the App |
| `pr-fixer-review-comment` | `github.review_comment.created` | trusted author, not the App |

Every rule also requires `head_repo == base_repo` and `draft == false`.
`fixer_repos` is an allow-list (d18): the repository must be in
`vars.fixer_repos`. `vars.fixer_excluded_repos` overrides it: a repository in
both lists never fires. A missing fact makes the
comparison false, so an event without PR facts never fires. "Trusted author"
is `data.author in vars.trusted_authors`, and "not the App" is
`self_authored != true`. The conditions reference the variables and never copy
a list.

All four rules have the same settings:

- they ship with `enabled: false`;
- their `on_failure` action (d16) is a `github.comment` as `github-app`:
  `PR fixer handed back (<code>): <message>` with the run link (the failing step is on
  the run);
- placement is machine `spark2`;
- `concurrency_key` is `pr-fixer:{trigger.data.repository}#{trigger.data.number}`
  and `max_attempts` is 3, both shared across the four rules. If spark2 fires
  a rule and dies before starting the run, the firing holds the PR's key until
  spark2 has been offline for 10 minutes; then any node fails it
  `placement_unavailable`, which frees the key;
- they pass the same workflow inputs: `repo`, `number`, `head_sha`,
  `head_branch`, `base_sha`, `clone_url`, `trusted_authors` (`{"$var":
  "trusted_authors"}`) and an `instruction` written for their trigger type.

Workflow `pr-fixer`:

1. `quiet`: a 300-second `wait` with the `head_unchanged` guard (the App
   actor reads the head). A push during the wait ends the run `superseded`.
   If the App actor's machine is offline or drained when the wait ends, the
   run waits up to 10 minutes for it, then fails `placement_unavailable`
   (an unenrolled machine fails it at once), which frees the PR's key.
   The head read has 10 seconds, including a cold `grant get` of the App
   key. A read that runs out of time is retried 5 seconds later, up to 5
   times, then fails the run (`head_lookup_failed`).
2. `threads`: the built-in `github.threads` (d15), as `github-app` on its
   machine. It lists the PR's unresolved review threads through GraphQL, at
   most 10 pages of 100, and keeps the threads whose opening comment's author
   is in `trusted_authors`. Logins are compared case-insensitively, and a bot's
   GraphQL login gets the REST `[bot]` suffix. It fails closed: a lookup error,
   the page cap or bad input fails the step and the run, so the agent never
   gets an unfiltered or partial list.
3. `fix`: a `retry_until` with at most 3 tries. It stops when the verdict is
   `pass` or `no_gate`, and carries the gate's `instruction` into the next try.
   Each try runs two steps:
   - `agent`: an `ai` step on actor `qwen-fixer` in mode `yolo`. Its `threads`
     input (the bridge's `threads` field) holds only the trusted threads, each
     `{thread_id, comment_id, path, line, author, body}`.
   - `gate`: the built-in `gate` on spark2. It reads the agent's `worktree`,
     `head_before` and `head_after`.
4. `push`: a built-in `action` step, `github.push` as `github-app`, on spark2
   where the gate's bundle is. It runs with `gate_verdict` wired in, so only a
   `pass` pushes. The port refuses `rule_disabled` when the firing rule was
   disabled mid-run.
5. `pick`: the built-in `github.threads_addressed`. It keeps the agent's
   `threads_addressed` entries whose `thread_id` is in the trusted list. Any
   other id is dropped, never answered.
6. `replies`: a `for_each` over `pick`'s list. Each item gets one
   `github.review_reply` (`comment_id` is the integer REST id of the opening
   comment, `thread_id` the GraphQL id, `resolve: true`).

The rule's terminal action is a `github.comment` as `github-app`. It reports
the verdict, the push and the agent's summary, and links the run as
`https://rules.culture.dev/api/runs/{{ run.id }}`. `run.id` is the
rule-action reference to the run's own id. The editor has no run page yet, so
the link opens the run document. The run's agent, gate and push steps run on
spark2, which puts the run on spark2's Statistics lane.

A rule's optional `on_failure` (d16) has the shape, validation and routing of
its `action`. The executor runs it exactly once when the run fails: a failed
step, a failed terminal action, or a mistyped workflow output. It first
cancels the unfinished steps. Its params can also read `run.error.step`,
`run.error.code` and `run.error.message` (only `on_failure` may), as well as
`run.id`, `trigger.*` and whatever workflow outputs exist. A superseded,
cancelled or successful run never runs it. If `on_failure` itself fails, it
gets its own retry policy and no more. The run then ends `failed` with the
original error, and `on_failure` never fires twice. The editor does not show
or edit the field yet. It is kept on save like any other field it does not
type.

Install order: variables first (an import that references an undefined
variable is refused), then the workflow, then the rules.

```bash
bash docs/rules/pr-fixer/seed-variables.sh            # dry run
bash docs/rules/pr-fixer/seed-variables.sh --apply    # admin; skips variables already set
culture-rules workflows import docs/rules/pr-fixer --apply
culture-rules rules import docs/rules/pr-fixer --apply
```

The seed values are:

- `trusted_authors`: the operator and Qodo's bot;
- `ignored_check_apps`: `["claude"]`;
- `checks_settle_timeout_s`: 900;
- `checks_settle_min_s`: 60;
- `fixer_repos`: `["agentculture/pr-fixer-sandbox"]`, the scratch repository
  for t20;
- `fixer_excluded_repos`: `[]`. culture-rules is out because it is not on the
  allow-list;
- `fixer_protected_paths`: the list in section 5.

Re-running the script leaves a variable that already exists alone. Pass
`--force` to replace it.

Widening the fixer means adding a repository to `fixer_repos`. Narrowing it
means removing one, or adding it to `fixer_excluded_repos`:

```bash
culture-rules variables add fixer_repos agentculture/some-repo            # dry run
culture-rules variables add fixer_repos agentculture/some-repo --apply    # admin
culture-rules variables remove fixer_repos agentculture/some-repo --apply
```

`variables add` and `variables remove` edit one item atomically. The server
does a compare-and-set on the version and retries, so two callers adding at
the same moment both land. An item already present (for `add`) or absent (for
`remove`) writes no new version. Every change is a new version naming the
caller, as with `variables set`.

An added item must be of a type the list already holds, judged per item, so
mixed lists and lists holding `null` work. An empty list takes any scalar. A
removed item may be any scalar. The CLI reads `ITEM` as text unless the list
holds numbers, booleans or `null` and the text parses as one. `--json-item`
takes the item as JSON instead, so `'"123"'` adds the string `123`.

*Planned* (guildmaster#138): guildmaster adds each repository to
`fixer_repos` when it provisions it. Whether a repository gets the fixer is
chosen at provisioning time, like public or private.

Known limits of this version:

- **The agent can still read the PR.** Only trusted threads are handed to it
  and answered, but the agent works in a checkout with a read-only token and
  could read other threads itself.
- **A superseded run posts nothing.** Every failed run posts the hand-back
  comment, but a run ended by a push during the quiet period posts nothing.
- **`no_gate` hands back.** On a repo without a `gate:` section, `push`
  refuses `gate_not_passed`, so nothing is pushed and the run hands back.
- **Only the last try's replies.** `threads_addressed` comes from the last
  agent try only.

*Planned* (t20/t21): importing the bundle on rules.culture.dev, the
`qwen-fixer` actor, and the App private key on spark2's node (`push` runs
there), then enabling the rules for one repository.

## On spark2

| Item | Value |
|---|---|
| Account | `culture-fixer` (planned; created by the operator) |
| Bridge | qwen on 8093, tailnet only |
| Actor | `qwen-fixer` (planned) |

## Disabling the fixer mid-run (d17)

Disabling the fixer rule stops it firing. It does **not** stop the runs
already going: the disable answers them, and you choose. The examples call
the rule `pr-fixer`; use the id it was imported with.

```console
$ culture-rules rules disable pr-fixer --apply --json
{ "verb": "rules disable", "applied": true, "result": { "id": "pr-fixer", "enabled": false,
  "active_runs": [{ "id": "run-…", "status": "running", "started_at": "…" }],
  "active_runs_total": 1, … },
  "hint": "1 current run(s) of pr-fixer are still active; disabling does not stop them. Stop them with: culture-rules rules stop-runs pr-fixer --apply" }
```

`active_runs` lists at most 50 runs, oldest first; `active_runs_total` counts
them all. A `rules update` that sets `enabled: false` answers the same. In the
editor, switching the rule off shows a notice, **"Stop N current runs?"**,
with **Approve** and **Keep running**.

- **Approve** (`culture-rules rules stop-runs pr-fixer --apply`, or
  `POST /rules/pr-fixer/stop-runs` with `{"apply": true}`, editor role):
  - each active run is cancelled through the normal run cancel, with status
    `cancelled` and the reason `rule disabled: stopped by <you>`, audited as
    `runs.cancel`;
  - the runs are read from the store, so a run another node is executing
    is cancelled too. That node's late result for it is ignored;
  - a cancelled run pushes nothing, comments nothing and hands nothing back:
    a cancel is not a failure;
  - without `--apply` it only lists the runs it would cancel;
  - a second call cancels nothing;
  - it is refused while the rule is enabled (`409 rule_enabled`, CLI exit 1).
    Stopping is offered only for a rule that is off.
- **Keep running** (or do nothing): the runs go on. The push step reads the
  live rule, so it still refuses with `rule_disabled`, and nothing reaches
  the PR branch.

## Which repos carry a gate section (t19)

A repo without `gate:` gets comments only; the fixer never pushes there.
Survey of the non-fork, non-archived agentculture repos, 2026-10-07.

| Repo | Gate |
|---|---|
| `culture-agent-template` | main (0.10.0, #34) |
| `devague` | main (0.24.2, #122) |
| `steward` | main (0.28.1, #85) |
| `guildmaster` | PR #137 (0.26.14) |
| `culture-rules` | this repo's final PR (rules/pr-fixer) |

Not yet, standard pattern (`uv sync`, `uv run pytest -n auto`); later batches,
each with the operator's go-ahead:

`agentsgit`, `agtag`, `appsec`, `associate`, `callsmith`, `climate-cli`, `code-lens-cli`, `culture`, `culture-nodes`, `culture-tools`, `data-refinery-cli`, `devex`, `dgx-spark-cli`, `discord-bot-cli`, `dominion-breaker`, `drone-cli`, `ebooks-cli`, `ec2-cli`, `ec2bedrock-cli`, `edge-ai-lab`, `eidetic-cli`, `embeddings-cli`, `embeddings-lens`, `events-cli`, `evidence-cli`, `face-cli`, `face-recognition-cli`, `fleet-cli`, `headspace-cli`, `innereye`, `intern-cli`, `jetson`, `jetson-ai-lab-cli`, `jetson-arena`, `jetson-cli`, `jetson-containers-agent`, `jetson-orin-cli`, `jetson-thor-cli`, `jetsonrun`, `jev-factory`, `katvan`, `knowledgebase-cli`, `league-of-agents`, `learn-cli`, `lecodeur`, `lobes-cli`, `media-cli`, `microphone-cli`, `neurosymbolic-system`, `notion-agent`, `nvsh`, `operator-cli`, `org`, `protocols-cli`, `prove-cli`, `reachy-lobes`, `reduce-cli`, `refactoring-cli`, `reterminal-cli`, `rigor-cli`, `rollout-cli`, `rtx-spark-cli`, `sensibo-cli`, `shabbos-goy`, `shell-cli`, `spanish-cli`, `storybook-cli`, `substack-cli`, `tensor-cli`, `unsloth-cli`, `webcam-cli`, `workledger-cli`, `xitter`, `xteink`.

Not yet, need their own gate (extra install flags, or browser or slow test passes in
CI):

`auntiepypi`, `colleague`, `embodiment`, `league-of-agents-platform`, `microduck-cli`, `reachy-mini-cli`, `telegram-agent`, `webglass-cli`.

No gate by design, no `culture.yaml`: `cultureflare`, `gitculture-cli`, `grant`, `nebula-run`, `office-agent`, `ship-game-v1`, `tipalti`. Not a uv project: `reachy-mini-mcp`.
`cultureagent` has no `culture.yaml` and was swapped out of batch 1 for
`guildmaster` (operator, 2026-10-07).
