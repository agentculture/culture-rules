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
| `~/.local/share/cultureagent-bridges/venv` | `cultureagent==0.14.1` (forwards titled `tool_call_update`s, so the agent's `STATUS:` notes arrive) |
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

## 7. The fixer rules and workflows (t17, d21)

The fixer is committed data in `docs/rules/pr-fixer/`, in the import format
(`rules/<id>.json`, `workflows/<id>.json`, `actors/<id>.json`). JSON rather
than YAML, so the import works on an API without the `yaml` extra.

Since d21 the fixer is **three workflows chained by run events**
([run events](../run-events.md)). Each stage is its own run, and each run
fires the next through the `rules.run.succeeded` event it emits:

| Rule | Fires on | Runs | Budget |
|---|---|---|---|
| `pr-fixer-checks` | `github.pr.checks_settled`, conclusion neither `success` nor `no_checks`, GitGuardian not failed | `pr-fix` | counts, 3 per PR |
| `pr-fixer-comment` | `github.comment.created` by a trusted author that asks (below) | `pr-fix` | counts |
| `pr-fixer-review` | `github.review.submitted` by a trusted author | `pr-fix` | counts |
| `pr-fixer-review-comment` | `github.review_comment.created` by a trusted author that asks | `pr-fix` | counts |
| `pr-fixer-review-commit` | `rules.run.succeeded` of `pr-fix`, gate `pass` or `no_gate` | `review-commit` | outside |
| `pr-fixer-refix` | `rules.run.succeeded` of `review-commit`, review `request_changes` | `pr-fix` with the findings | counts |
| `pr-fixer-publish` | `rules.run.succeeded` of `review-commit`, review `approve`, gate `pass` | `publish-fix` | outside |
| `pr-fixer-secrets` (d25) | `github.pr.checks_settled` with `gitguardian` in `failed_apps` | `report-secrets` | its own key; one comment per head SHA |
| `pr-fixer-secrets-late` (d25) | `github.pr.checks_failed_late` with `gitguardian` in `failed_apps` | `report-secrets` | the same key and comment |

`pr-fixer-secrets` and `pr-fixer-secrets-late` are not part of the chain: they only
comment (see
[GitGuardian findings](#gitguardian-findings-d25)).

The d21 text calls the review stage `pr-fixer-review`. That id already names
the review-submitted trigger rule, so the stage is `pr-fixer-review-commit`,
after its workflow.

### The fixer in the editor

The editor has no Rules tab: it folds each rule into the workflow it starts
([spec](../specs/2026-10-09-editor-rules-folded-into-workflows-three-views.md)).
The rules above are unchanged; only the way the editor shows them is. On the
**Workflows** tab the fixer's 9 rules and 4 workflows read as:

- **one chain card for the fix chain**: `pr-fix`, `review-commit` and
  `publish-fix`, linked by continuations, "3 workflows linked by
  continuations · 4 entry points · was 7 rules". `pr-fix` starts when one of
  its 4 entry points fires (`pr-fixer-checks`, `-comment`, `-review`,
  `-review-comment`) and owns the continuation `pr-fixer-refix`.
  `review-commit` owns `pr-fixer-review-commit`, and `publish-fix` owns
  `pr-fixer-publish`. Each previous workflow shows a read-only "Continues
  into" link to the next;
- **a chain card of its own for `report-secrets`**, with its 2 entry points
  `pr-fixer-secrets` and `pr-fixer-secrets-late`: no continuation links it
  to the fix chain. `pr-fixer-checks` and `pr-fixer-secrets` both start on
  `github.pr.checks_settled`, so the list notes on each "same event starts"
  the other workflow.

Over the whole list that is "4 workflows · 6 entry points · was 9 rules";
"See it as one chain" draws the chain. These counts are what the editor
derives from the rules it reads (`web/src/fold/model.ts`), nothing is
stored for them. A continuation is linked only by its condition's
`data.workflow_id == <workflow>` term (the `rules.run.succeeded` rows above):
edit the predecessor with the "Continues from" control on the entry point,
which writes exactly that term, rather than in the condition.

Opening `pr-fix` shows the **Simple** view: When lists its entry points,
each with its trigger, condition, placement and whether it counts toward the
attempt budget; Then reads "Continues into" `review-commit`, "Ends here",
"On failure" and "Runs" (the PR's key and the budget). A value all 5 of its
rules hold identically (the PR's key, or the placement on spark2) shows
once; editing it writes each
rule in turn, skipping and flagging any rule changed meanwhile. **Detailed**
shows the steps (`quiet`, `secrets`, `threads`, `sonar`, `fix`) and
**Debug** every port and reference. An old `/rules/pr-fixer-checks` link
opens `/workflows/pr-fix?entry=pr-fixer-checks`.

Editing an entry point in the Simple view writes only that rule, so no
trusted workflow digest moves. Any save of the workflow itself does change
its digest: a rename from the head (which works in every view, Simple
included) or step edits in Detailed. A trusted workflow saved that way stops
being trusted ([Trusted workflows](#trusted-workflows-d20-round-2-d21-roles)):
its runs still start, but the chain will not review or push their work
until the new digest is added to `culture_rules/actors/trusted.py` and
released to every node. Saving the original definition back restores the
trust.

So the editor asks first (deviation d6 of the fold). Save on a workflow
whose id is a trusted role (`pr-fixer`, `pr-fix`, `review-commit`,
`publish-fix`; `web/src/workflows/trusted.ts`) opens **"Save a trusted
workflow?"**, which says exactly that: the new digest is not trusted, the
runs still start but the fixer chain will not review or push their work
until the digest is in `trusted.py` on every node, and saving the original
back restores the trust. **Keep editing** (focused first; Escape does the
same) closes it and nothing is saved; **Save anyway** saves. The editor
matches by id, which holds only because every trusted digest's workflow id
is its role's name: `tests/rules/test_trusted_role_ids.py` pins that, so a
role named differently fails there rather than slipping past the question.

### What every rule checks

The four trigger rules fire only for:

- the PR's own repository: `head_repo == base_repo`, so no forks;
- a PR that is not a draft (`draft == false`);
- an open PR (`state == "open"`). A closed PR, or an event that does not say,
  starts nothing;
- a repository in `vars.fixer_repos` and not in `vars.fixer_excluded_repos`
  (d18). A repository in both lists never fires.

A missing fact makes the comparison false, so an event without PR facts
never fires. The comment, review and review-comment rules also need a trusted
author (`data.author in vars.trusted_authors`) and not the App itself
(`self_authored != true`).

**A comment must ask: start the comment with `/fix` or
`@rules-culture-dev`.** Only the comment's first token counts, after any
leading whitespace. Anything later in the body never starts a run: a mention
mid-sentence, a quote, a code block. The accepted forms are listed in
`vars.fixer_comment_triggers`; the seed default is `["/fix",
"@rules-culture-dev"]`. The webhook receiver reads two facts from that first
token:

- `command`: the token when it is a `/word` followed by whitespace or the end
  of the comment, lowercased (`/fix`). `/fix, please` is no command: put a
  space after it;
- `mention`: `@rules-culture-dev` (or `@rules-culture-dev[bot]`) when the
  token is the App's mention. The match folds ASCII case only and needs a
  token boundary: `@rules-culture-dev,` counts; `@rules-culture-devx`,
  `@rules-culture-dev[bot]x` and `@rules-culture-dev.example` do not. The slug
  is the App actor's `params.self_identity` without `[bot]`.

Narrow the variable to `["/fix"]` to ignore mentions. Qodo's billing notice,
a status note and a closing comment (the three live cases) carry neither, so
they start nothing. A submitted review needs no command: a trusted reviewer's
review starts a run, as before.

The three stage rules check `data.workflow_id` (the upstream run's workflow)
and the repository allow-list again. An operator who drops a repository from
`fixer_repos` stops its chains at the next stage.

### Settings every rule shares

- They ship with `enabled: false`.
- Placement is machine `spark2`.
- The key is `pr-fixer:{trigger.data.repository}#{trigger.data.number}`. It
  resolves on the run events too, because they carry the PR fields.
- **The chain holds the key** between its stages
  ([run events](../run-events.md), "A chain is one unit per key"). A new
  checks settle or `/fix` comment that arrives during a chain waits and fires
  once the chain ends. It never interleaves with it.
- **Only fix runs count** toward the PR's attempt budget of 3: the four
  trigger rules and `pr-fixer-refix`. The review and publish stages are
  `counts_toward_budget: false`. A human push or green checks resets it.
- **One comment per chain** (`only_at_chain_end: true` on every action and
  `on_failure`), and since d26 it is the chain's **live status comment**
  (`status: true`, see [The status comment](#the-status-comment-d26)). The
  stage that ends the chain writes its final section:
  - a failed stage writes the hand-back,
    `PR fixer handed back (<code>): <message>` with the run link;
  - `publish-fix` writes the success line with the gate verdict and the
    pushed head;
  - in review-only mode the review writes its verdict and findings;
  - a stage that something continues writes nothing (`chain_continues` on
    its run).
- All inputs of the three workflows are optional. A stage whose inputs are
  missing still starts, fails inside and hands back, so a chain never ends
  in silence.

### Workflow `pr-fix`

It builds and gates one commit. It never reviews or pushes it.

1. `quiet`: a 300-second `wait` with the `head_unchanged` guard. It is the
   same as the d20 workflow's: a push during the wait ends the run
   `superseded`.
2. `secrets` (d25): the built-in `gitguardian.hold`. It reads the GitGuardian
   check of the PR head as the App. While GitGuardian fails on the head, the
   run fails `secrets_found` before any agent runs, and hands back once. A
   lookup error fails the run too (fail closed, like `threads`). See
   [GitGuardian findings](#gitguardian-findings-d25).
3. `threads`: the built-in `github.threads` (d15). Only the PR's unresolved
   threads opened by a trusted author reach the agent. Any error fails the
   run.
4. `sonar`: the built-in `sonar.gate_issues`
   (`culture_rules/node/actions/sonar.py`). It reads the PR's SonarCloud
   quality gate from SonarCloud's public API. For each **failing** condition
   it lists the issues behind it:

   | Failing condition | Issues listed |
   |---|---|
   | `new_reliability_rating` | bugs |
   | `new_security_rating` | vulnerabilities |
   | `new_maintainability_rating` | code smells |
   | `new_security_hotspots_reviewed` | hotspots still to review |

   The step lists at most 50, paged across every listed type. It reports the
   `total` SonarCloud counts and how many it `omitted`; a capped list's note
   says "the first N of TOTAL". Coverage and duplication have no issue list;
   the step names them in its note. The project key is `{owner}_{name}`. A failed
   lookup (no analysis, SonarCloud down) is `available: false` with a note.
   It never fails the run: Sonar data is advice, not a guard.
5. `fix`: a `retry_until` of up to 3 tries until the gate verdict is `pass`
   or `no_gate`. The gate's failure text is the next try's instruction.
   - `agent`: `qwen-fixer` in mode `yolo`. Its bound inputs are the trusted
     `threads`, `sonar_issues` and `sonar_note`. The rule's instruction says
     to fix exactly those Sonar issues and never the rest of the backlog.
     `require_commit: true`: a turn that leaves no commit (bridge status
     `no_changes` or `uncommitted`, or the head unmoved) fails the step at
     once with `no_changes`. The attempt then ends with no gate and no
     review, and hands back once.
   - `gate`: the built-in gate on spark2, unchanged from d20. It builds
     **one commit itself** and tests, diffs and bundles exactly that commit.

The run exports what the next stages need: the gate's verdict, `commit_sha`,
`start_sha`, `diff`, `diff_truncated`, `gate_output` and `bundle`. It also
exports the trusted threads, the agent's `threads_addressed` and summary, the
instruction and task, the clone URL and the head branch.

### Workflow `review-commit`

1. `review`: `codex-reviewer` (spark, sandbox `read-only`, locked brief).
   It reads the pr-fix run's diff. It runs only for a passing gate (or no
   gate) whose diff is the whole change.
2. `verdict`: the built-in `review`. It **walks back to the `pr-fix` run**
   whose success started this run (`culture_rules/actors/lineage.py`), never
   through wired inputs:
   - this run's id must be its rule's firing on that run's event;
   - the event must equal the pr-fix run's immutable completion record;
   - the pr-fix run must be a trusted `pr-fix` that succeeded.

   Each failed check is `chain_unverified` or `workflow_not_trusted`. The
   step then reads that run's last gate and its implementer from the store.
   It checks the reviewer exactly as d20 does: read-only, another actor and
   backend, the locked brief, the same commit and diff. A request for changes
   becomes the re-fix's instruction: the findings, then "The original task".
   If the PR's attempt budget is already spent, no re-fix would be admitted,
   so the step fails `changes_requested` with the findings, and that one
   hand-back ends the chain.

**Review records are per commit.** Each outcome is an immutable record in
`fixer_reviews`. One that names a whole target (repo, PR, base, start, tip)
moves that target's pointer in `fixer_review_targets` forward. A later review
run of the same commit replaces an earlier one's result; an older run's late
write never does. The d20 per-run pointers (`fixer_review_current`) are no
longer read.

### Workflow `publish-fix`

1. `push`: `github.push` as `github-app` on spark2, where the bundle is. On
   top of every d20 check, the port **verifies the whole chain from the
   store**:
   - this run's workflow is a trusted `publish-fix`;
   - it was started by a trusted `review-commit` run succeeding, itself
     started by a trusted `pr-fix` run succeeding, each link checked against
     the upstream run's completion record (`chain_unverified`,
     `workflow_not_trusted`);
   - the commit, its start, the bundle, the repository, the PR and the branch
     are exactly what that pr-fix run's last gate built (`chain_mismatch`),
     and the gate passed (`gate_not_passed`);
   - the rule of every run of the chain is still enabled (`rule_disabled`).
     For a re-fix the chain reaches back through every earlier review and fix
     to the trigger rule that started it. Disabling any fixer rule mid-chain,
     the initiating one included, stops the push;
   - the commit's review record approves exactly this commit, by a reviewer
     other than the implementer, and was **written by this chain's review
     run** (`review_not_in_chain`);
   - then come the d20 checks: base, head (`head_moved`), consumption by
     compare-and-set, and the re-check right before `git push`.

   A commit equal to the PR head has nothing to push. It is reported done
   only after the PR read, so a closed PR is `pr_not_open`, never a success.
2. `threads`: the PR's trusted unresolved threads, read again after the push.
   A thread resolved meanwhile is not answered.
3. `pick` and `replies`: one `github.review_reply` per trusted thread the
   agent addressed, naming the pushed commit.

### GitGuardian findings (d25)

**A failed GitGuardian check is never auto-fixed.** A leaked secret must be
revoked and rotated by a human. An agent editing the file would only hide
the finding: rewriting history does not un-leak a secret. So the fixer posts
the findings as a PR comment and stays off the PR until GitGuardian passes.

GitGuardian reports through a GitHub check run of its App (slug
`gitguardian`, check `GitGuardian Security Checks`). On a finding the run
concludes `failure`, its title reads like `1 secret uncovered!`, and its text
holds a markdown table: GitGuardian id (the incident link), status, secret
type, commit, filename and a "View secret" link to the diff line. The secret
value is not in the table. A PR too large to scan concludes `neutral`
("Could not complete scanning of your commits"): that is not a finding, and
nothing is posted.

How it fits together:

- **The settled event names the failed apps.** `github.pr.checks_settled`
  carries `failed_apps`: the app slugs, lower-cased and sorted, of the counted
  suites that completed `failure`.
- **`pr-fixer-checks` stays off.** Its condition gains
  `not (gitguardian ∈ failed_apps)`, so a head GitGuardian fails on starts no
  fix, whatever else failed. An event without `failed_apps` (one settled
  before the upgrade) is read as no GitGuardian failure.
- **A late failure is reported too.** When GitGuardian's suite completes
  `failure` after its head settled (the settle timed out while it ran, or a
  re-run failed), the settler emits `github.pr.checks_failed_late` once per
  (repo, head SHA, app): the settled event's data with `failed_apps` grown
  by the app, `late_app`, `conclusion: failure` and `settled_by: late`. Its
  PR facts are those of the settle. That is on purpose: a secret pushed to a
  PR is leaked even if the PR has since been closed or its head has moved,
  so reporting it is still right. No clock decides that a failure is late:
  it becomes a candidate (`checks_settle_late`), and the late event is
  emitted only once the App's current listing still shows the app's suite
  for that head concluded `failure`. So a failure that was re-run green is
  never reported. A failed listing, or a node that cannot read the repo's
  checks, keeps the candidate for the next tick on a node that can. Every
  insert and every new failure noted for the head and app gives the
  candidate a fresh random `token`. A confirmer drops the candidate only if
  its token is still the one it read. The token never repeats, not even when
  the candidate is dropped and noted again. So a stale clean listing never
  drops a newer failure. Emitting is atomic too: one transaction re-reads
  the candidate, requires the token, inserts the late event and drops the
  candidate. So a stale failing listing never emits after a newer clean one
  has dropped it. A
  candidate nobody confirms within the 24-hour recovery window is dropped.
  Candidates are not backed up; see the backup doc's "Not covered".
  The settle tick's recovery scan notes a candidate for a late completion the
  webhook stored but never handled. It also looks at completions received up
  to 5 minutes (`LATE_SKEW_MARGIN_S`) before the settled event, because the
  webhook server's clock may lag the settler's. Both
  settle event types and their id prefixes (`settled_`, `late_`) are
  reserved: the bus and the webhooks quarantine a copy, so nothing outside
  the settler can fire the report or squat its id.
  `pr-fixer-secrets-late` fires on it with
  the same condition, workflow, key and once key as `pr-fixer-secrets`. No
  fixer rule fires on it. A fix that the earlier settle started stops at the
  `pr-fix` hold if GitGuardian has failed by the end of the quiet period. If
  GitGuardian fails after the hold has passed, that fix run goes on: its
  agent never receives the findings.
- **The `pr-fix` hold stops every other fix run.** A `/fix` comment, a review
  or a re-fix runs `pr-fix`, whose `secrets` step (`gitguardian.hold`) fails
  `secrets_found` after the quiet period, before the threads, the agent and
  the gate. The hand-back reads `PR fixer handed back (actor_failed):
  secrets_found: GitGuardian reports 1 hardcoded secret on 0ef24e4. A human
  revokes and rotates them first; ...`. It counts as an attempt.
- **`pr-fixer-secrets` posts the findings.** It fires on the same settled
  event when `gitguardian ∈ failed_apps`, for an open, same-repo PR of a
  repository in `vars.fixer_repos` and not in `vars.fixer_excluded_repos`.
  Drafts are included: a pushed secret is leaked whether or not the PR is a
  draft. It runs `report-secrets`, whose one step, the built-in
  `gitguardian.findings` (`culture_rules/node/actions/gitguardian.py`), reads
  the check as the App and parses the table
  (`culture_rules/apps/gitguardian.py`). The action posts its `comment` as
  the App, with the run link.
- **Once per head SHA.** The action's `once_key`
  (`gitguardian:<repo>#<number>@<head_sha>`) makes `github.comment` claim the
  key in the store (`github_comment_once`) before it posts. A key already
  claimed completes as `skipped: posted_before` and posts nothing. The claim
  survives every budget reset (a green settle resets the attempt budget,
  so `max_attempts` cannot dedupe), re-armed settles, the late rule and other
  nodes. Only a post GitHub refused with a client error (4xx except 408)
  releases the claim, so a later firing can post. Any other failure (a 5xx,
  408, network error, the deadline, an unreadable answer) may follow a
  created comment, so the claim stays as `unknown` and a later post is
  skipped as `claimed_before`: at most once, never twice. The claims are
  backed up with the run history. The
  `on_failure` comment has its own once key. Both rules share the key
  `pr-secrets:{repository}#{number}@{head_sha}`, one run at a time per head,
  outside the fixer's per-PR key and budget, so a fix chain in flight never
  delays them.

What the comment carries, per finding: the secret type, `file:line` (the
line from the "View secret" anchor, when there is one), the short commit,
the incident link and the GitGuardian status. Nothing else of the check text
reaches it. Only a recognizable findings table is read: a header naming at
least `Secret` and `Filename`, immediately followed by its `|---|` separator,
then contiguous rows up to the first other line. Anything in a fenced or
indented code block is ignored. Cells are cleaned of backticks, pipes, angle
brackets and links. No URL from the check is echoed. The incident link is
rebuilt from its digits as
`https://dashboard.gitguardian.com/workspace/<n>/incidents/<n>[?occurrence=<n>]`
only when the raw link is exactly that shape for the row's incident, else
only the id is shown. The check link is rebuilt as
`https://github.com/<owner>/<repo>/runs/<n>`. The "View secret" link only
yields the line number, and a commit is kept only when it is hex. At most 50 findings are listed
(`max_findings`, up to 200); a longer list ends with `… and N more`. N counts
the table's rows, or the check title's count when that is larger. For the
sample check (values made up):

```markdown
**GitGuardian found 1 hardcoded secret on `0ef24e4`.**

| Secret type | File | Commit | Incident | Status |
|---|---|---|---|---|
| `Generic Password` | `esphome/x.yaml:8` | `0ef24e4` | [12345678](https://dashboard.gitguardian.com/workspace/111/incidents/12345678?occurrence=222) | Triggered |

Revoke and rotate each secret, then remove it from the code; rewriting history does not un-leak it. The PR fixer does not touch secrets and stays off this PR until GitGuardian passes.

Run: https://rules.culture.dev/api/runs/<run id>
```

A failing check whose table cannot be read still gets a comment. It says
how many secrets the title reports and points to the check. A failed lookup
fails the run, and `on_failure` posts that the findings could not be read
(`{{ run.error.code }}`), with the same remediation. If GitGuardian no
longer fails by the time the step reads it (a re-run passed), the comment
says so and lists nothing.

`report-secrets` holds no trusted role: it never pushes or reviews, so
nothing checks its digest.

### The status comment (d26)

**The fixer says that it works, on the PR, while it works.** Each fix chain
keeps exactly one comment on the PR, posted by the App and edited live.
`culture_rules/node/fixer_status.py` reads the chain and renders the
comment; `culture_rules/node/status_board.py` owns the comment's record and
every GitHub call; `culture_rules/apps/public_text.py` makes relayed text
inert.

- **Opting in.** A chain writes a status comment when its rules' chain-end
  `github.comment` (the action or `on_failure`) has `params.status: true`.
  Every shipped fixer rule does; `pr-fixer-secrets` and
  `pr-fixer-secrets-late` do not (the GitGuardian findings stay a separate
  comment). `rules describe` reads `(as its chain's status comment)`.
  `status` does not combine with `once_key`.
- **The start.** The node on the App actor's machine (spark) looks at the
  running runs in its `status` stage, after the drive stage. The first run
  of an opted-in chain that is past its hold gets a record: its `secrets`
  step (`gitguardian.hold`) has succeeded, which also means the quiet period
  is over. A run superseded in its quiet period, or stopped by the hold,
  never gets one; its hand-back is the chain's only comment, as before. The
  comment says what started the chain (checks settled, a comment, a review
  or a review comment, with the author and the head SHA), lists the stages
  and links the chain's first run.
- **One per chain.** The record lives in `fixer_status_comments`, keyed by
  the chain's **root**: the run an external event started. A review, a
  re-fix or a publish walks its verified `rules.run.succeeded` links back to
  it (`culture_rules/actors/lineage.py`), so a re-fix reuses the comment. It
  is backed up with the run history. A new chain on the same PR (a later
  settle or `/fix`) gets its own comment.
- **Live edits.** The node re-renders every open chain and edits the comment
  (`PATCH /repos/{owner}/{repo}/issues/comments/{id}`) as the stages move:

  | Stage | Shows |
  |---|---|
  | Quiet period and GitGuardian hold | done once the hold passed |
  | Agent (`qwen-fixer`) | working (try N of 3), done or failed |
  | Test gate | the verdict (`pass`, `fail`, `guard`, `no_gate`) |
  | Review (`codex-reviewer`) | working, then the verdict and the findings count |
  | Push | the pushed head, nothing to push, or the failure code |

  A re-fixed chain adds `Earlier: round 1: review request_changes (N
  findings).` The comment also shows the agent's status notes (below), its
  last activity, and the **fix summary** of the latest fix that succeeded
  (the agent's own summary, which the chain-end texts no longer carry).
- **The end never fails the run.** The chain-end action (the hand-back, the
  push line, the review-only verdict) does not call GitHub and resolves no
  credential: it stores its text as the record's **pending final** and
  completes. The `status` stage
  delivers it as the comment's final section, on top, with the run that
  ended the chain linked by the engine; if the chain has no comment yet, it
  posts the comment, complete. The record is `final` only once GitHub
  answered 2xx. A chain that ends without a chain-end action (a run
  cancelled or superseded mid-chain, or nothing continuing it for 15
  minutes) gets an engine-worded final the same way. A final comment is
  never edited again.

#### One writer, desired state

- **A single writer.** One process writes an App actor's status comments:
  the holder of the actor's **writer lease** (`fixer_status_writers`, one
  document per actor: `{owner: host:pid:boot-random, until}`, 120 s), taken
  or renewed by compare-and-set at the start of every cycle and again
  before every GitHub call. Only a node on the actor's **current** placed
  machine tries (it reads the placement again every 10 s). So two node
  processes on one machine never both write.
- **Bounded clock skew.** Nodes must keep their clocks within 30 s of each
  other (`MAX_CLOCK_SKEW_S`; run NTP, see the prerequisites in
  `rules-culture-dev.md`). A holder starts no call once its own clock passes
  `until - 30 s - 20 s` (the skew bound and a call's hard deadline); another
  process takes the lease only after `until + 30 s` by its own clock. The
  lease (120 s) is at least twice the call deadline plus the skew. So with
  skew within the bound, a new writer (after the actor moves, or after the
  old process dies) never writes while the old writer's last call is in
  flight; it waits up to 150 s after the old writer's last renewal. **Beyond
  the bound** (more than about 130 s of skew, the lease minus a call
  deadline plus the bound), a new writer could take the lease while the old
  writer's last call is still in flight, and that call could land after the
  new writer's final. The record would then say `delivered` while GitHub
  shows the older body, until a later chain on the PR writes again. The shipped `github-app` actor
  is placed on spark (`tests/rules/fixtures/github-app.live.json`,
  `machine: spark`), so spark is the writer. Records are selected by actor:
  moving the actor to another machine hands the writer over once the old
  lease expires, and no record is stranded. **An App actor without a
  machine gets no live status comment:** `status: true` then posts a plain
  chain-end comment, as before d26.
- **Desired state.** The store holds the inputs: the chain's runs, the
  agent's notes, the pending final text. Each cycle the writer renders the
  comment they describe and hashes it (`desired_rev`). If GitHub has not
  acknowledged that body (`acked_rev`), the writer edits the comment to it,
  or posts it when there is none yet. `acked_rev` moves only on a 2xx. A
  failed or ambiguous call (5xx, 408, a timeout, a network error) changes
  nothing and is retried with backoff. Because the one writer always sends
  the *current* desired body, the last write is the newest state: a stale
  edit or an older final cannot stay on the comment. A record is done only
  once the acknowledged body is the final body.
- **Field ownership.** The chain-end action and the API own the record's
  inputs (`final_text`, `final_run`, `final_requested_at`); the writer owns
  the delivery state (state, comment, `acked_rev`, `retry_at`, failures,
  outcome, the final and pending flags). Every write re-reads the record and
  re-applies only its own fields on the fresh document by compare-and-set,
  retrying a lost one, so a final stored while the writer is mid-call is
  never dropped and never turns an unsent post into `unresolved`. An input
  write leaves the record pending and stores `inputs_rev`, a version of the
  inputs. Every send carries the `inputs_rev` its body was rendered from, and
  an ending (a final delivered, a horizon) is applied only while the
  record's inputs are still that snapshot: a final stored while an older one
  is being posted or edited is sent next, never marked delivered unsent.
- **Posting at most once.** A post is recorded as `posting` before it is
  sent. An ambiguous answer leaves it `posting`; the next cycle lists the
  PR's comments, adopts the one this App posted
  (`performed_via_github_app.id`) that carries the chain's hidden marker,
  and edits it from then on. If there is none, the record gives up silently
  (`unresolved`) rather than risk a second comment. A refused post (4xx),
  or one never sent, goes back to `none`. A deleted comment (the edit
  answers 404) is posted again.
- **Pacing.** Writes keep 5 s apart, final ones included; a change of the
  notes alone waits a minute.
- **Backoff.** A failed call sets `retry_at` (5 s, doubling, at most 15
  minutes). `http_403`, `http_422`, and an App that cannot serve the repo
  give up after 3 tries (`outcome: gave_up`).
- **Every pending record ends.** A final still undelivered 24 hours after
  it was asked, or a record with no activity for 7 days (whatever it is
  retrying), gives up (`gave_up`, final, no longer pending). Retention then
  drops it 30 days later.
- **Bounded calls.** One `status` stage makes at most 10 HTTP requests
  (token exchanges and every page of a comment listing included, each
  charged before it is sent) within 10 seconds, after the drive stage. Each
  call, resolving the App's key included (through the port's bounded
  resolver), has a hard 20 s deadline: a watchdog (opt-in, the status stage
  only; every other GitHub call runs inline as before) runs the request on a
  worker thread (a small bounded pool) and gives up at the deadline, so
  dripping headers, chunks or trailers cannot outlast it (the abandoned
  worker ends at its socket timeout, the same remaining budget). The
  transport also reads the body in chunks against the deadline, with a size
  cap. Work the budget
  stops waits for the next cycle without counting as a failure.
- **Fair selection.** A cycle reads each served actor's pending records
  that are due (`retry_at` up to now) in (`retry_at`, id) order, paged with
  a composite cursor, so records sharing a timestamp are never skipped; a
  written or failed record moves to the back, and an unchanged one costs no
  request. The node declares the indexes these queries use (an actor's due
  records, final records by date, a key's runs by date, a run's bridge
  invocations).

A pushed fix after one re-fix reads (ids and SHAs made up; relayed text is
backslash-escaped, so it renders as plain words):

```markdown
PR fixer pushed the fix\: gate pass, reviewed and approved, pushed True, head 39051c8ff0e0a4c1a5bfc7876ce399283a8dd75b\.

Run: https://rules.culture.dev/api/runs/<publish run id>

**PR fixer status**

Started by checks settled (failure) at `dab3155b8700`.

- **done** Quiet period and GitGuardian hold
- **done** Agent (qwen-fixer)
- **verdict pass** Test gate
- **approve (0 findings)** Review (codex-reviewer)
- **pushed `39051c8ff0e0`** Push

Earlier: round 1: review request_changes (1 finding).

**Fix summary**

made x 3

Chain started with run: https://rules.culture.dev/api/runs/<first pr-fix run id>

<!-- culture-rules:fixer-status <first pr-fix run id> -->
```

While the chain works, the first line reads `**PR fixer is working on this
PR.** This comment is updated as it goes.`, and an **Agent notes** list
follows the stages.

#### The agent's status notes

The agent may tell the PR what it is doing. A run whose rule writes a
status comment gets this appended to its agent's instruction (never to a
locked brief such as the reviewer's):

```text
Status notes (optional): to tell the people on this PR what you are doing, run the shell command `echo "STATUS: <one short sentence>"`. ...
```

The bridge reports each shell tool call in a `progress` callback whose note
is the call's title (`tool_call: Shell: echo "STATUS: fixing the test"`).
The engine (`record_bridge_event`) reads the text after `STATUS:`,
normalizes and checks it, and keeps the last 5 notes on the bridge
invocation. A refused note is counted (`status_notes_withheld`) and drops
the kept notes too. Other progress notes (the agent's other tool calls) are
never relayed. The agent never holds a GitHub token: the engine relays its
notes as the App.

**The bridge describes the resolved title (cultureagent 0.14.1).** Against
an OpenAI-compatible streaming backend (cortex), Qwen Code first announces a
tool call as a *preparing* `tool_call` without its arguments (title
`Shell`), then sends the full title in a `tool_call_update`. Since
cultureagent 0.14.1 (agentculture/cultureagent#53) the qwen backend
describes a `tool_call_update` that carries a non-empty title, so the
`STATUS:` text reaches the engine; updates without a title (result frames)
stay silent. With cultureagent 0.14.0 the notes never arrived.

#### What is relayed, and how it is made inert

Untrusted text (notes, the fix summary, the chain-end text, which can quote
a reviewer's findings) is never "cleaned" Markdown. It is:

1. **normalized**: Unicode NFKC; control and format characters (zero-width,
   bidi) removed; every URL (`scheme://…`, `//host…`, `www.…`) dropped;
   whitespace collapsed (one line of at most 200 characters for a note);
2. **checked**, and refused when it, or its stripped view (tags,
   backslashes, backticks, emphasis removed, so `ghp_<b></b>…`
   reassembles), looks like a credential: GitHub tokens (`ghp_`, `gho_`,
   `ghs_`, `ghu_`, `ghr_`, `github_pat_`), AWS keys (`AKIA`, `ASIA`), Slack
   and OpenAI-style keys, private key blocks, long random strings (a commit
   SHA or a run id is not one). It is also refused when, compacted
   (lower-cased, separators and markup dropped, so spaces and note
   boundaries cannot split a value), it holds a **known secret** of the
   process: a 12-character piece of a value, or of its hex, base64 or
   urlsafe-base64 encoding. Known values are everything the process
   resolved through `grant` (the App private key, bridge tokens), its
   `CULTURE_RULES_SECRET_*` environment, and the bridge bearer tokens it
   used. Every check runs on the raw, entity-decoded and normalized text
   and on the stripped view of each, so neither markup nor angle brackets
   hide a value. Notes are checked one by one and together. A refused note is
   dropped; a refused summary or final section renders `[withheld]`;
3. **escaped**: every Markdown metacharacter is backslash-escaped, `<`, `>`
   and `&` become entities (so `<!--` can never appear), `@` gets a
   zero-width space (no mention pings), leading spaces are dropped. It
   renders as the same words: no link, image, heading, list, table, fence
   or HTML.

Engine facts (verdicts, codes, SHAs, run ids, the author's login, the
repository) are rendered only when they have their expected shape. Run links
and the hidden marker are built from validated engine values, never from
the relayed text. Right before every post or edit the whole body is checked
again: an untrusted section that holds a secret, alone or with the rest,
becomes `[withheld]`. For that last check the engine's own fixed literals
(the run-link base `<public URL>/api/runs/`, the headline) lose their
interior: each keeps its first and last 11 token characters, so a piece
that crosses into a fact or relayed text still matches, and only a piece
wholly inside the engine's public wording is not counted. Without this, a secret that held a fragment of the
public hostname hid every body behind the fallback (seen live on
culture-rules-tester#8). Such a secret is weak, so rotate it. Relayed text
and engine facts are checked exactly as before. `CULTURE_RULES_PUBLIC_URL`
must be a plain http(s) origin (`scheme://host[:port][/path]`; no
credentials, query, fragment, whitespace or control characters). It is
rebuilt from its parts, and anything else falls back to the default with a
warning.

**Residual risk, plainly.** These checks stop accidents and the obvious
leaks. An agent determined to exfiltrate through an encoding of its own
choosing cannot be fully stopped by any filter. The known-secret check
covers the values the checking process holds: notes are checked by the API
(which receives the bridge callbacks) and again by the node on spark, which
renders and posts; the fixer's bridge token is resolved on spark2, so it is
known there only when it is in the checking process's environment. What the
fixer's agent holds is its bridge token, scoped to its own bridge.

### Review-only mode

Disable `pr-fixer-publish`. The review then ends the chain. Its action writes
`PR fixer review: <verdict> for <commit> (gate <verdict>); not pushed: nothing
publishes it`, followed by the findings and the run link, into the chain's
status comment. Nothing is pushed.
The same comment ends a chain whose gate was `no_gate`, because
`pr-fixer-publish` needs a passing gate.

### Bridge jobs are cancelled when their attempt ends

A bridge job can outlive its step attempt: the step timed out, a newer
attempt replaced it, or the run failed or was cancelled. Each node cycle, the
node on the actor's machine (where its bridge token is) asks the bridge to
stop such a job (`POST /v1/invocations/<id>/cancel`). It tries at most 5
times. The invocation records `cancel_sent_at` and `cancel_reason`. An
orphaned session no longer holds the fixer's only seat.

### The d20 single workflow, in detail

The 0.13.0 single workflow, `pr-fixer`, did the fix, the gate, the review and
the push in one run. It is no longer shipped. Its data is kept as a test
fixture (`tests/rules/fixtures/pr-fixer-single/`), and its digest stays
trusted while runs pinned to it may still push. The mechanics below are its
steps 3 and 4, kept as they were. The split's `gate`, `review`, `verdict` and
`push` steps work the same way. The differences are the ones the sections
above name:

- the review is its own run, and its record is per commit;
- the push verifies the chain;
- a request for changes runs a new `pr-fix` instead of the next try.

1. `fix` (its step 3): a `retry_until` with at most 3 tries. It stops when the
   verdict is `pass` or `no_gate` **and** the reviewer approves, and carries the last
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
2. `push` (its step 4): a built-in `action` step, `github.push` as `github-app`, on
   spark2 where the gate's bundle is. It runs with `gate_verdict` wired in, so only a
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

### Trusted workflows (d20 round 2, d21 roles)

`github.push` and the `review` step serve only runs whose pinned workflow
definition hashes to a digest pinned in code, **in a role**
(`TRUSTED_WORKFLOWS`, `culture_rules/actors/trusted.py`):

| Role | Workflow | May |
|---|---|---|
| `pr-fixer` | the 0.13.0 single workflow (fixture copy) | review and push its own commit |
| `pr-fix` | `workflows/pr-fix.json` | build and gate a commit (its runs are what a review judges) |
| `review-commit` | `workflows/review-commit.json` | record a review of a `pr-fix` run's commit |
| `publish-fix` | `workflows/publish-fix.json` | push a commit a `review-commit` run approved |

A digest sits in one role, and a run may do only what its role allows. A push
checks the role of every run of its chain. The digest is sha256 of the
definition as the workflow model writes it, without `version` (every save
bumps it), as compact sorted JSON. It is recomputed from each run's pinned
definition, never read from the run. Any other edit, even a description,
makes the workflow untrusted: it still runs and hands back, but pushes
nothing.

To change a workflow on purpose:

1. Edit it in `docs/rules/pr-fixer/workflows/`.
2. `tests/rules/test_pr_fixer_bundle.py` fails and prints the new digest.
   Add it to its role in the same PR. Keep the old digest while runs pinned
   to it may still be in flight; drop it in a later release.
3. Ship the wheel and upgrade every node.
4. Import the workflow (`culture-rules workflows import ... --apply`).

The shipped digests (0.14.0):

| Role | Digest |
|---|---|
| `pr-fixer` | `sha256:01ece1cd…f4fc5d6` (kept for the transition) |
| `pr-fix` | `sha256:04570dee…43b6a8` (with the d25 GitGuardian hold) |
| `review-commit` | `sha256:79064f76…b158e6` |
| `publish-fix` | `sha256:0fa92074…c23680` |

### Rolling out the split (d21)

The order matters: nodes first, then the data.

1. **Pause the engine** (`culture-rules runs pause`, lapse l5) and let the
   fixer runs in flight finish, or stop them (`rules stop-runs`). A d20 run
   pinned to the single workflow can still push after the upgrade (its digest
   stays trusted). A review the old build recorded is read through a narrow
   legacy path: only for a run of that trusted workflow, only from its per-run
   pointer in `fixer_review_current`, judged and consumed exactly as d20 did.
2. **Upgrade every node and the API** to the 0.14.0 (or later) wheel. It holds the new
   trusted digests, the chain holds, the cancel stage and the Sonar built-in.
   An old node that evaluates a stage rule cannot hold the key or verify the
   chain, so upgrade them all before any stage rule is enabled.
3. **Seed the new variable**:
   `bash docs/rules/pr-fixer/seed-variables.sh --apply`. Existing variables
   are kept; this adds `fixer_comment_triggers`. Without it the comment rules'
   import is refused.
4. **Import**: actors, then workflows, then rules
   (`culture-rules actors|workflows|rules import docs/rules/pr-fixer --apply`).
   The four trigger rules keep their ids and now run `pr-fix`; the import sets
   every rule back to `enabled: false`.
5. **Disable the old single workflow**: the stored `pr-fixer` workflow is no
   longer referenced by any rule. Disable it
   (`culture-rules workflows disable pr-fixer --apply`), so no direct run
   starts it.
6. **Enable the nine rules** (`rules enable <id> --apply` each), the stage
   rules first, then `pr-fixer-secrets` and `pr-fixer-secrets-late` (d25),
   then the trigger rules. For
   review-only mode, leave `pr-fixer-publish` off.
7. **Resume** (`culture-rules runs resume`).
8. In a later release, drop the `pr-fixer` digest from `TRUSTED_WORKFLOWS`.

### Rolling out the GitGuardian report (d25)

d25 ships in 0.15.0, right after the split (0.14.0), whose `pr-fix` never
ran live without the hold, so only the held `pr-fix` digest is trusted.
Rolling out 0.15.0 over 0.13.x does these steps inside "Rolling out the
split".

1. **Upgrade every node and the API** to the release. It emits
   `failed_apps`, trusts the held `pr-fix` digest and has the
   `gitguardian.*` built-ins. A node without
   them fails `report-secrets` and the new `pr-fix` with `no_builtin`.
2. **Check the App can read checks**: the GitHub App needs `Checks: read`
   (the settle already uses it).
3. **Import** the workflows, then the rules
   (`culture-rules workflows import docs/rules/pr-fixer --apply`, then
   `culture-rules rules import docs/rules/pr-fixer --apply`). That adds
   `report-secrets`, `pr-fixer-secrets` and `pr-fixer-secrets-late`, updates
   `pr-fix` (the hold) and
   `pr-fixer-checks` (the GitGuardian clause), and sets every rule back to
   `enabled: false`.
4. **Enable** `pr-fixer-secrets` and `pr-fixer-secrets-late`, and re-enable
   the rules that were on
   (`culture-rules rules enable <id> --apply`).

### Rolling out the status comment (d26)

d26 ships in 0.16.0, over the live 0.15.0. It changes no pinned workflow, so no digest changes;
only the rules change (`status: true` and their texts).

1. **Upgrade every node and the API** to the release. The API records the
   agent's notes from the bridge callbacks; the node on spark (where the App
   actor lives) posts and edits the status comments. A node of an older
   release posts the chain-end text as a plain comment, as before.
2. **Check the App can edit its comments.** Editing a comment needs the same
   permission as posting one (`Issues: write` for an issue comment;
   `Pull requests: write` covers comments on a PR). The App already posts
   the chain-end comments, so nothing should change; a 403 on the first
   edit shows in the node log (`status comment of chain ... not edited: http_403`), and the record gives up after 3 tries (`outcome: gave_up`): the fix itself is never affected.
3. **Import** the rules (`culture-rules rules import docs/rules/pr-fixer
   --apply`). That sets every rule back to `enabled: false`; re-enable the
   ones that were on (`culture-rules rules enable <id> --apply`).
4. **Optional, for the agent's notes:** a cultureagent release whose qwen
   backend describes a `tool_call_update` that carries a title (see
   [The agent's status notes](#the-agents-status-notes)). Without it the
   status comment works; it only shows no notes.

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
python -m culture_rules.actors.trusted actor < github-app.json
```

Add the printed digest to `TRUSTED_ACTOR_DIGESTS["github-app"]` (and refresh
the fixture), release, and upgrade every node before editing the live actor.
The same command with `workflow < FILE` prints a workflow digest.

**`commit_author` (operator-approved 2026-10-08).** The live App actor gets
`params.commit_author: rules-culture-dev[bot]`, so `github.push` also
refuses any commit not authored by the bot (`foreign_author`). The release
trusts both digests, the actor without the field and with it
(`tests/rules/fixtures/github-app.live.json` and `github-app.with-author.json`),
so the order is: ship the release, upgrade every node, then edit the live
actor. Drop the first digest in a later release.

### Reading it back in plain words (d19)

`culture-rules rules describe <id>` and `culture-rules workflows describe <id>`
(`GET /rules/{id}/describe`, `GET /workflows/{id}/describe`, and the MCP tools
`rules_describe` / `workflows_describe`) describe a stored definition from its
config alone. No AI writes it, and the name and description fields are not
used. In the editor, the (i) "About" button on each workflow (list row and
title) and on each entry point of a workflow's Simple view shows the same
lines. For the shipped bundle (d21):

```console
$ culture-rules rules describe pr-fixer-checks
When github.pr.checks_settled
If head_repo = base_repo
and draft = false
and state = open
and repository ∈ vars.fixer_repos
and not (repository ∈ vars.fixer_excluded_repos)
and conclusion ≠ success
and conclusion ≠ no_checks
and not (gitguardian ∈ failed_apps)
Run workflow pr-fix (5 steps)
On spark2
Then github.comment as github-app (as its chain's status comment) (only where its chain ends)
On failure github.comment as github-app (as its chain's status comment) (only where its chain ends)
Key pr-fixer:{repository}#{number}, ≤3 attempts
Disabled
$ culture-rules rules describe pr-fixer-publish
When rules.run.succeeded
If workflow_id = review-commit
and review = approve
and verdict = pass
and repository ∈ vars.fixer_repos
and not (repository ∈ vars.fixer_excluded_repos)
Run workflow publish-fix (4 steps)
On spark2
Then github.comment as github-app (as its chain's status comment) (only where its chain ends)
On failure github.comment as github-app (as its chain's status comment) (only where its chain ends)
Key pr-fixer:{repository}#{number}, outside the attempt budget
Disabled
$ culture-rules rules describe pr-fixer-secrets
When github.pr.checks_settled
If head_repo = base_repo
and state = open
and repository ∈ vars.fixer_repos
and not (repository ∈ vars.fixer_excluded_repos)
and gitguardian ∈ failed_apps
Run workflow report-secrets (1 step)
On spark2
Then github.comment as github-app (once per once_key)
On failure github.comment as github-app (once per once_key)
Key pr-secrets:{repository}#{number}@{head_sha}
Disabled
$ culture-rules workflows describe pr-fix
1 quiet — wait 300 s; stop if the PR head moves (head_unchanged, as github-app)
2 secrets — gitguardian.hold as github-app: stop while GitGuardian fails on the head
3 threads — github.threads as github-app: unresolved threads by trusted authors
4 sonar — sonar.gate_issues: the issues behind the PR's failing SonarCloud gate
5 fix — retry up to 3×, until verdict ∈ {pass, no_gate}:
  5.1 agent — qwen-fixer (agent, must commit)
  5.2 gate — test gate on spark2
$ culture-rules workflows describe report-secrets
1 findings — gitguardian.findings as github-app: the head's GitGuardian findings, no secret values
$ culture-rules workflows describe review-commit
1 review — codex-reviewer (agent, read-only), when gate_verdict ∈ {pass, no_gate} and diff_truncated = false
2 verdict — review verdict, recorded for its commit (github.push checks it)
$ culture-rules workflows describe publish-fix
1 push — github.push as github-app on spark2 (only on a passing gate and an approving review of exactly that commit)
2 threads — github.threads as github-app: unresolved threads by trusted authors
3 pick — github.threads_addressed
4 replies — for each item (≤200): github.review_reply as github-app and resolve
```

These outputs are pinned as golden tests (`tests/model/test_describe.py`).
`--json` adds the structured `entries` (`label`, `text`, `depth`, and `step`
on a workflow entry). The vocabulary is in `culture_rules/model/describe.py`:
one phrase per trigger kind, step kind, built-in, action kind and condition
operator. An unknown kind reads as its raw name, so describing never fails.

## 8. The reviewer on spark (d20)

**Deployed 2026-10-08, with one difference from the recipe below: by operator
decision the bridge runs as the `spark` account, using that account's Codex
login, not a dedicated `culture-reviewer`.** The bridge's capability
document reports `confinement: unix-user:spark: codex enforces --sandbox with
a bubblewrap helper backed by unprivileged user namespaces, which this kernel
permits`, and a read-only probe on spark could neither write a file nor
reach the network. The bridge token is `RULES_CODEX_REVIEWER_TOKEN` in
spark's own grant store, injected into the bridge as
`CODEX_BRIDGE_AUTH_TOKEN` and into spark's node unit as
`CULTURE_RULES_SECRET_RULES_CODEX_REVIEWER_TOKEN`. The bridge lives in its
own venv (`~/.local/share/cultureagent-bridges/venv`, cultureagent 0.14.0)
beside the operator's cultureagent 0.13.0 tool; its config sets `codex_bin`
to the nvm-installed codex. It shares the operator's Codex quota: a
rate-limited review hands the PR back and pushes nothing. Moving to a
dedicated account later is the recipe below.

It is the recipe for the second
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
| Account | `spark` (operator decision 2026-10-08; `culture-reviewer` is the stricter option) |
| Bridge | codex on 8094, loopback only, sandbox `read-only` (deployed) |
| Actor | `codex-reviewer` (imported, trusted digest `sha256:dc26a418…`) |

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
editor, switching the rule's entry point off (in its workflow's Simple view)
shows a notice, **"Stop N current runs?"**, with **Approve** and **Keep
running**.

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

Since d21 the push checks the rule of **every** run of its chain: the
trigger rule that started the fix, `pr-fixer-review-commit` and
`pr-fixer-publish`. Disabling any one of them mid-chain stops the push, and
the chain hands back once (`rule_disabled`). Stopping runs is per rule: stop
the runs of each rule you disabled.

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
