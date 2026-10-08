# The PR fixer machine: an unprivileged account running the agent bridge

Task t18 (claims c30; honesty condition h24; deviation d8). This is the
operator recipe for the machine the PR fixer's agent runs on: **spark2**. One
cultureagent bridge, Qwen Code on cortex, runs as a systemd user unit of a
dedicated account, `culture-fixer`. The engine reaches it over the tailnet as
the agent actor `qwen-fixer`. Codex is not installed on spark2: it stays on
spark (d8). Every fix the gate passes is then reviewed on spark by a second,
independent agent, `codex-reviewer`, before anything is pushed (d20, section
8).

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

1. As `culture-fixer`, the gate makes a workspace with `mktemp -d` in that
   account's own temp space (`culture_rules_gate.XXXXXXXXXX`, mode 700, owned
   by `culture-fixer`). Inside it, it makes `checkout/` and `tmp/`.
2. It runs `git init` in `checkout/` and feeds the node-verified pack to
   `git index-pack --stdin`. The node opens the pack file (mode 600, in the
   node's own mode-700 temp directory) and passes it as `culture-fixer`'s
   stdin, so that account never reads a path the node owns.
3. It checks out the commit, verifies that `HEAD` is the commit and that
   `git status --porcelain --ignored` is empty, runs the gate, and removes
   the whole workspace, also on failure.

The setup and test commands run with their temp space in the workspace's
`tmp/`, not the account's. The gate puts
`env TMPDIR=<workspace>/tmp PYTEST_ADDOPTS=--basetemp=<workspace>/tmp/pytest`
in front of each declared argv, after the prefix and the `env -C`. This is
the same way it passes git's settings, since sudo resets the environment.
Without it, pytest's default basetemp is `<tmp>/pytest-of-culture-fixer`, so
every `tmp_path` names the account. A repo test that asserts a flag such as
`-f` is absent from output echoing a temp path then failed only in the gate
(lobes-cli#302). The workspace name has no `-` for the same reason.
`PYTEST_ADDOPTS` goes in front of pytest's command line, so a repo's own
`--basetemp` in its gate command still wins. A command that is not pytest
ignores `PYTEST_ADDOPTS` and just gets a private `TMPDIR`. The verdict's
`command` is still the declared argv. A repo whose gate command sets its own
`PYTEST_ADDOPTS` through `env` replaces the gate's, basetemp included.
A `PYTEST_ADDOPTS` in the run-as account's own environment is **not** kept:
the gate's value replaces it. In production there is none to keep, because
sudo resets the environment before the command runs; put any pytest options
a repo needs in its `gate:` command instead.

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
   `pass` or `no_gate` **and** the reviewer approves, and carries the last
   step's `instruction` (the gate's failure text, or the reviewer's findings)
   into the next try. After three tries without that, the run fails
   `loop_max_exceeded` and the hand-back comment ends with the last reason
   (`config.explain`). Each try runs four steps:
   - `agent`: an `ai` step on actor `qwen-fixer` in mode `yolo`. Its `threads`
     input (the bridge's `threads` field) holds only the trusted threads, each
     `{thread_id, comment_id, path, line, author, body}`.
     While `qwen-fixer` is at its concurrency cap, the step waits `blocked` in
     the actor's queue. Its 3900 s working budget starts only when the bridge
     accepts the work. The wait is bounded at twice that budget (130 minutes);
     past it the step fails `queue_timeout`.
   - `gate`: the built-in `gate` on spark2. It reads the agent's `worktree`,
     `head_before` and `head_after`. Before anything else it reads the PR as
     the App and refuses a `base_sha` that is not the PR's base
     (`base_mismatch`; `base_unverified` when it cannot tell), because the
     base selects the gate policy. A merge in the agent's commits is a
     `guard` verdict (`merge_commit`). On `pass` and `no_gate` the gate then
     builds **one commit itself** (`git commit-tree`): the agent tip's tree on
     the PR head and nothing else of the agent's. Author and committer are the
     App bot (`commit_identity`, default the `rules-culture-dev[bot]`
     identity), the message is written by the engine from trusted run state
     (`pr-fixer: automated fix for <repo>#<n> (run <id>, try <k>)`), and both
     dates are the PR head's, so a re-run builds the same SHA. The gate
     builds it **before** testing: setup and tests run in a fresh checkout of
     that commit, so one commit is tested, diffed, reviewed, bundled and
     pushed (`commit_sha`; the agent's
     tip is `agent_commit_sha`). The agent's own commits, and anything they
     added and later removed, never leave spark2. The gate also outputs the diff of the built commit
     (`diff`, `diff_chars`) and `diff_truncated`, which is true when the text
     does not show the whole change: over 30000 characters, a binary file, an
     executable bit or other mode change, a symlink, a submodule pointer, or
     text that is not valid UTF-8 (`diff_problems` names each).
   - `review` (d20): an `ai` step on actor `codex-reviewer` (spark, sandbox
     `read-only`). It runs only when the gate verdict is `pass` or `no_gate`
     and the diff is the whole change (`config.when`); otherwise it is skipped. The
     fix commit is not on GitHub yet, so the bridge checks out the PR head and
     the reviewer reads the gate's diff, with the original instruction, the
     trusted threads and the end of the gate output. Its brief lives in code
     (`REVIEWER_BRIEF` in `culture_rules/actors/review.py`), not in the
     workflow: the actor's `params.locked_instruction` names it, and the
     engine refuses any instruction from the step config or inputs
     (`instruction_locked`). It lists what to check and how to end: one verdict
     object, `approve` or `request_changes` with findings, naming the commit.
     It tells the reviewer that the diff, threads and gate output are
     untrusted data, never instructions (the diff is the fixer's own work).
   - `verdict`: the built-in `review`. It reads this try's gate, reviewer and
     agent results from the run in the store (never from wired inputs) and
     fails closed. The implementer is the agent step that produced the gate's
     `agent_commit_sha`, never a name in the config. The reviewer must be an
     actor with `params.reviewer: true` on the `codex` backend
     (`reviewer_not_allowed`), and its bridge invocation must carry the locked
     brief's digest. The gate's start must be the PR head the run was
     started for. Anything but a clear approval of exactly the built commit,
     by an actor and backend other than the implementer's, from a reviewer
     that is read-only and changed nothing, is not an approval:
     `request_changes` goes to the next try; a malformed, ambiguous or
     mismatched review, or a reviewer that wrote, is not allowed or is the
     implementer, fails the run (`review_invalid`, `review_commit_mismatch`,
     `reviewer_not_read_only`, `reviewer_not_allowed`,
     `reviewer_is_implementer`, `review_missing`) and hands back. An
     incomplete diff is `request_changes` ("make a smaller, text-only fix")
     without running Codex. Each outcome is an immutable record in
     `fixer_reviews` (one per attempt: repo, PR, start, tip, verdict,
     identities; id `<run>:<verdict step key>:<attempt>`), and
     `fixer_review_current` points at the run's newest one. The pointer only
     moves forward, so a late result for an older try never becomes current;
     two different results for the same try make it a permanent `conflict`
     (`review_conflict`).
4. `push`: a built-in `action` step, `github.push` as `github-app`, on spark2
   where the gate's bundle is. It runs with `gate_verdict` wired in, so only a
   `pass` pushes. The port refuses `rule_disabled` when the firing rule was
   disabled mid-run. Then, for **every** push whatever the workflow wires, it
   reads the run's current review record and refuses unless it approves this
   repo and PR (`review_target_mismatch`), from exactly `expected_head_sha`
   to exactly the commit being pushed (`review_commit_mismatch`), by a
   reviewer whose actor and backend both differ from the implementer's:
   `review_missing`, `review_rejected`, `reviewer_is_implementer`. It reads
   the record again right before the final `git push` and refuses if
   another record has become current (`review_changed`). Then it
   **consumes** the approval: a compare-and-set turns the pointer to
   `consumed`, bound to the pushed commit. Before that, the PR's base as
   GitHub reports it must still be the base the review recorded
   (`base_changed` otherwise). A base change does not move the head, and
   the checks settle is once per head, so `base_changed` **re-arms** the
   head's settle in a new generation: the next check completion, or the
   node's own poll, emits a fresh `github.pr.checks_settled` carrying the
   PR's current base, and `pr-fixer-checks` starts a new run that gates and
   reviews against it. At most three re-arms per head (`REARM_LIMIT`), within
   the rules' per-PR attempt budget. Each re-arm is bound to the refusal's
   review record, so replaying the same refused push re-arms nothing (a
   refusal that arrives while the head's settle is still waiting is folded
   into it, its cause recorded and any missing PR number or branch filled
   in); a head
   that never settled is armed with the PR number and branch the push named,
   so its event fetches the PR's current facts. A delayed emitter of an older
   generation can no longer mark a newer one settled (its compare-and-set
   includes the generation). Only `pr-fixer-checks` re-fires this
   way, and only while the head's checks are red; a run started by a comment
   on a green head needs a new comment. No later verdict can move a
   consumed pointer (it is recorded and fails `review_consumed`), so no
   revocation can land between that moment and the push. What remains is the
   GitHub reads and the git network call themselves, which cannot be part
   of a store transaction (a base could still move between the PR read and
   the push): a
   push that fails after consumption pushed nothing, and its retry finds the
   approval consumed for the same commit. The push takes `commit_sha` and
   `expected_head_sha` from the gate.
   Before all of that, the run's pinned workflow must be a **trusted** one
   (`workflow_not_trusted`, below), so an edited workflow can run but never
   push.
5. `pick`: the built-in `github.threads_addressed`. It keeps the agent's
   `threads_addressed` entries whose `thread_id` is in the trusted list. Any
   other id is dropped, never answered. Each reply names the pushed commit
   (`push.head_after`), never the agent's own.

### Trusted workflows (d20 round 2)

`github.push` and the `review` step serve only runs whose pinned workflow
definition hashes to a digest in `TRUSTED_WORKFLOW_DIGESTS`
(`culture_rules/actors/trusted.py`). The digest is sha256 of the definition
as the workflow model writes it, without `version` (every save bumps it), as
compact sorted JSON. It is recomputed from the run's pinned definition, not
read from the run. Any other edit, even a description, makes the workflow
untrusted: it still runs and hands back, but pushes nothing.

To change the workflow on purpose:

1. Edit `docs/rules/pr-fixer/workflows/pr-fixer.json`.
2. `tests/rules/test_trusted_workflow.py` fails and prints the new digest.
   Add it to the set in the same PR. Keep the old digest while runs pinned
   to it may still be in flight; drop it in a later release.
3. Ship the wheel and upgrade every node.
4. Import the workflow (`culture-rules workflows import ... --apply`).

It is a set, so the split workflows planned for d21 can sit beside it.

### Trusted actors (d20 round 3)

Actor documents are outside the workflow digest, so their security-relevant
fields are pinned the same way, in `TRUSTED_ACTOR_DIGESTS` (per actor id, a
set of digests, in `culture_rules/actors/trusted.py`):

- `codex-reviewer`: id, kind, harness, model, machine, and `params`
  `bridge_url`, `callback_url`, `bridge_token`, `sandbox`, `model`, `mode`,
  `locked_instruction`, `reviewer`, `max_bound_input_chars`. The check
  happens where the configuration is used: the bridge adapter refuses to
  dispatch from an untrusted snapshot (`actor_not_trusted`) and records the
  digest and the endpoint it called on the invocation. The verdict step
  requires that recorded digest to be trusted, and checks the current actor
  too, so swapping the actor for the dispatch and restoring it before the
  verdict does not help. The review record keeps the digest it checked
  (`trusted_actors`). The snapshot is the raw stored document, and the
  router never builds an adapter or action port for a soft-deleted actor
  (`deleted_at` set).
- `github-app`: id, kind, machine, `params.surface`, `commit_author`,
  `permissions`, and `connection` `app_id`, `installation_id` and
  `private_key` (the reference). `github.push` reads the actor **once**,
  checks that snapshot, and uses the same snapshot's connection and commit
  author for every GitHub and git call; any other shape is refused.
- `qwen-fixer` is not pinned: everything it produces is reviewed.

Names, descriptions and limits such as `max_concurrency` are not part of
the digest. A fully malicious admin who controls actors and a bridge stays
out of scope; this makes such a change need a release.

**The App's `repos` list is deliberately not pinned.** It holds about 120
agentculture repositories, and guildmaster adds repositories at provisioning
(d18). Pinning it would block every fixer push after each new repository
until a release. Which repositories the App can reach is scope, not review
integrity: every push still needs the trusted workflow, a genuine Codex
approval of the exact commit, and the fixer rules' own allow-list
(`vars.fixer_repos`).

The shipped `github-app` digest is the live actor's, from
`tests/rules/fixtures/github-app.live.json` (a copy of the stored document:
ids and grant reference names only). To recompute it after a change:

```bash
culture-rules actors show github-app --json > github-app.json   # the bare actor document
python -m culture_rules.actors.trusted actor github-app.json
```

Add the printed digest to `TRUSTED_ACTOR_DIGESTS["github-app"]` (and refresh
the fixture), release, and upgrade every node before editing the live actor.
The same command with `workflow FILE` prints a workflow digest.

**`commit_author` is not set on the live App actor.** The digest therefore
pins it as unset, and `github.push`'s author check (`foreign_author`) stays
off, as it is today. The gate-built commit is always authored as
`rules-culture-dev[bot]`, so the check would pass. *Proposed, not applied:*
set `params.commit_author` to `rules-culture-dev[bot]` on the live actor,
and in the same release replace the pinned digest with the one for that
shape. The push would then also refuse any commit not authored by the bot.

### Reading it back in plain words (d19)

`culture-rules rules describe <id>` and `culture-rules workflows describe <id>`
(`GET /rules/{id}/describe`, `GET /workflows/{id}/describe`, and the MCP tools
`rules_describe` / `workflows_describe`) describe a stored definition from its
config alone. No AI writes it, and the name and description fields are not
used. In the editor, the (i) "About" button on each rule and workflow (list
rows and title) shows the same lines. For the shipped bundle:

```console
$ culture-rules rules describe pr-fixer-checks
When github.pr.checks_settled
If head_repo = base_repo
and draft = false
and repository ∈ vars.fixer_repos
and not (repository ∈ vars.fixer_excluded_repos)
and conclusion ≠ success
and conclusion ≠ no_checks
Run workflow pr-fixer (6 steps)
On spark2
Then github.comment as github-app
On failure github.comment as github-app
Key pr-fixer:{repository}#{number}, ≤3 attempts
Disabled
$ culture-rules workflows describe pr-fixer
1 quiet — wait 300 s; stop if the PR head moves (head_unchanged, as github-app)
2 threads — github.threads as github-app: unresolved threads by trusted authors
3 fix — retry up to 3×, until verdict ∈ {pass, no_gate} and review = approve:
  3.1 agent — qwen-fixer (agent)
  3.2 gate — test gate on spark2
  3.3 review — codex-reviewer (agent, read-only), when gate_verdict ∈ {pass, no_gate} and diff_truncated = false
  3.4 verdict — review verdict, recorded for github.push
4 push — github.push as github-app on spark2 (only on a passing gate and an approving review)
5 pick — github.threads_addressed
6 replies — for each item (≤200): github.review_reply as github-app and resolve
```

These two outputs are pinned as golden tests (`tests/model/test_describe.py`).
`--json` adds the structured `entries` (`label`, `text`, `depth`, and `step`
on a workflow entry). The vocabulary is in `culture_rules/model/describe.py`:
one phrase per trigger kind, step kind, built-in, action kind and condition
operator. An unknown kind reads as its raw name, so describing never fails.

## 8. The reviewer on spark (d20; *planned*)

Nothing in this section is deployed yet. It is the recipe for the second
agent that reviews every fix the gate passes: **Codex** through the
cultureagent codex bridge (`cultureagent-codex-bridge`, cultureagent 0.14.0)
on **spark**, registered as the actor `codex-reviewer`. It must never be able
to commit or push. Four things hold that:

- the bridge runs every review with `codex exec --sandbox read-only`. The
  actor sets `sandbox: read-only`, the engine refuses a step that asks for
  anything wider (`sandbox_locked`), and the `review` step refuses a reviewer
  actor that is not read-only (`reviewer_not_read_only`);
- the bridge runs as its own Unix account, `culture-reviewer`, which holds no
  GitHub write credential, no SSH key and no `gh` login. This matters because
  Codex enforces `read-only` with a bubblewrap helper that needs unprivileged
  user namespaces. Where the kernel restricts them, the session runs
  **unconfined instead of failing** (the bridge says so in its capability
  document's `confinement` sentence), and then the account is the only
  boundary;
- the bridge itself never pushes, and the agent's environment carries no push
  credential;
- the `review` step checks the result: no commits, nothing dirty, head
  unmoved, else `reviewer_not_read_only`.

### Account and secrets (operator)

Create `culture-reviewer` on spark the way section 1 creates `culture-fixer`
on spark2: own group and home (mode 750), no sudo, no operator groups,
lingering on. Then log Codex in **as that account** (`codex login`, a
browser or device-code hand-turn). Its session lives in that account's
`CODEX_HOME`, never the operator's.

Secrets go in grant, as in section 2:

| grant name | Store | Injected as | Used by |
|---|---|---|---|
| `REVIEWER_CODEX_BRIDGE_TOKEN` | `culture-reviewer`'s | `CODEX_BRIDGE_AUTH_TOKEN` | the bridge: the bearer token it requires |
| `RULES_CODEX_REVIEWER_TOKEN` | spark's node (operator account) | read by the node through `grant:` | the engine, calling the bridge |
| `REVIEWER_GITHUB_TOKEN` (only for private repos) | `culture-reviewer`'s | `GH_TOKEN` | a read-only fine-grained token, as in section 2 |

Generate the bridge token once in the node's store and copy it, so the two
match:

```bash
G="$(command -v grant)"   # sudo needs the absolute path
$G generate RULES_CODEX_REVIEWER_TOKEN --hidden --bytes 32 --encoding hex
$G run --inject T=RULES_CODEX_REVIEWER_TOKEN -- sh -c 'printf %s "$T"' \
  | sudo $G set REVIEWER_CODEX_BRIDGE_TOKEN - --hidden --user culture-reviewer
```

Add `--secret RULES_CODEX_REVIEWER_TOKEN` to spark's node install
(`deploy/node/install.sh`, with the flags it was installed with).

### The bridge (as `culture-reviewer`)

```bash
uv tool install cultureagent==0.14.0      # puts cultureagent-codex-bridge on PATH
```

The bridge config, `.config/cultureagent-bridges/codex.json` in the
account's home (mode 600):

```json
{
  "host": "127.0.0.1",
  "port": 8094,
  "repo_allowlist_prefixes": ["https://github.com/agentculture/"],
  "default_sandbox": "read-only",
  "max_concurrent": 1,
  "always_async": true
}
```

It binds loopback: the actor's machine is spark, so the only caller is
spark's own engine node. Bind spark's tailnet IP instead only if that ever
changes. The callbacks go the other way, to the API's loopback listener
(the callback route needs no Access token; it checks its own per-invocation
token).

The user unit, `.config/systemd/user/cultureagent-codex-bridge.service` in
the same home:

```ini
[Unit]
Description=cultureagent codex bridge (PR fixer reviewer, read-only)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
# The bridge token reaches the bridge only as environment at exec time.
ExecStart=%h/.local/bin/grant run --inject CODEX_BRIDGE_AUTH_TOKEN=REVIEWER_CODEX_BRIDGE_TOKEN -- %h/.local/bin/cultureagent-codex-bridge --config %h/.config/cultureagent-bridges/codex.json
Restart=always
RestartSec=5
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now cultureagent-codex-bridge
```

### The actor

The actor ships as `docs/rules/pr-fixer/actors/codex-reviewer.json`:
machine `spark`, harness `codex`, and `params` `bridge_url`
`http://127.0.0.1:8094`, `callback_url` `http://127.0.0.1:18765` (the API's
loopback listener on spark; change it if yours differs), `bridge_token`
`grant:RULES_CODEX_REVIEWER_TOKEN`, `sandbox: read-only`,
`reviewer: true` (only such an actor may review), `locked_instruction:
pr-fixer-review` (the brief in code), `max_concurrency: 1` (one review at a
time; others wait) and `max_bound_input_chars: 60000`. The bridge appends the step's inputs (the
diff, the threads, the gate output) to the prompt and cuts them at 60000
characters without saying so; with this cap the engine refuses
(`bound_inputs_too_large`) rather than let a reviewer judge a diff it saw
only part of.

```bash
culture-rules actors import docs/rules/pr-fixer --apply
```

### Verify

| Check | Expected |
|---|---|
| as `culture-reviewer`: `git push`, `gh auth status`, `ls ~operator/.codex` | no credential; permission denied |
| `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8094/v1/capabilities` | `401` |
| the same with the bearer token | `200`; `"pushes": false`; read the `confinement` sentence |
| `sysctl kernel.apparmor_restrict_unprivileged_userns` | `0`, or accept that the account is the only boundary (see above) |
| `culture-rules actors list` | `codex-reviewer`, machine `spark` |
| a fixer run on the scratch repository | `fix[0]/review` on spark, `fix[0]/verdict` `approve`, then the push |

## On spark2

| Item | Value |
|---|---|
| Account | `culture-fixer` (planned; created by the operator) |
| Bridge | qwen on 8093, tailnet only |
| Actor | `qwen-fixer` (planned) |

## On spark (d20)

| Item | Value |
|---|---|
| Account | `culture-reviewer` (planned; created by the operator) |
| Bridge | codex on 8094, loopback only, sandbox `read-only` |
| Actor | `codex-reviewer` (planned) |

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
