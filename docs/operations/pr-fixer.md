# The PR fixer machine: an unprivileged account running the agent bridges

Task t18 (claims c30; honesty condition h24). This is the operator recipe for
the machine the PR fixer's agents run on: **spark2**. Two cultureagent bridges
(Qwen Code on cortex, and Codex) run as systemd user units of a dedicated
account, `culture-fixer`. The engine reaches them over the tailnet as two
agent actors, `qwen-fixer` and `codex-fixer`.

**The steps marked *operator* are hand-turns.** They need root on spark2,
a GitHub or SonarCloud login, or an interactive `codex login`. The build
ships the scripts and this recipe; it never runs them on a host.

## What the account may and may not touch

The agent runs tools as the bridge's Unix user. Neither bridge sandboxes the
host (see cultureagent's `docs/bridges.md`, "Trust boundary"), so the account
is the boundary:

| The account can | The account cannot |
|---|---|
| fetch `https://github.com/agentculture/*` into its own caches and commit locally | push: the bridges refuse it and every cache's push URL is unusable; the GitHub App pushes after the engine's test gate |
| read PRs, checks and Actions logs with its own read-only token | read the engine node's `node.env`, grant store or the App private key (another user's mode-700 directories) |
| call cortex with its own API key | use the operator's `gh`, `claude` or `codex` logins (they live in the operator's home, mode 750) |

Known limit: a bridge passes its environment on to the agent, and both run
as the same user. The agent can therefore read the bridge's own bearer token
and could call its own bridge. That reaches nothing the agent cannot already
do as that user (fetch allowlisted repos, commit locally), and it is tracked
as plan risk r11.

## 1. Create the account (operator, root)

From a culture-rules checkout on spark2:

```bash
sudo bash deploy/pr-fixer/create-user.sh            # plan only
sudo bash deploy/pr-fixer/create-user.sh --apply
```

It creates `culture-fixer` with its own group and home (mode 750), no sudo
and no operator groups, and enables lingering so its units start at boot. It
warns if the operator's home is readable by others.

## 2. Tools for the account (operator)

As `culture-fixer` (`sudo -iu culture-fixer`):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh        # uv, into ~/.local/bin
# grant: install the same way as for the operator account, into ~/.local/bin
npm install --prefix ~/.local/lib/qwen-code @qwen-code/qwen-code@0.24.7
ln -sf ~/.local/lib/qwen-code/node_modules/.bin/qwen ~/.local/bin/qwen
npm install --prefix ~/.local/lib/codex @openai/codex
ln -sf ~/.local/lib/codex/node_modules/.bin/codex ~/.local/bin/codex
codex login                                            # interactive, once
```

The qwen bridge's handshake accepts only listed Qwen Code versions;
`install.sh` pins `0.24.7` (`--qwen-version` to change it).

## 3. Secrets (operator)

As `culture-fixer`, seal three secrets in that account's own grant store:

```bash
grant generate FIXER_QWEN_BRIDGE_TOKEN --hidden --bytes 32 --encoding hex
grant generate FIXER_CODEX_BRIDGE_TOKEN --hidden --bytes 32 --encoding hex
grant set FIXER_CORTEX_API_KEY - --hidden        # the cortex endpoint's API key, from stdin
```

Then create the account's read-only credentials:

- a fine-grained GitHub token on the agentculture organisation with
  **Contents: Read, Pull requests: Read, Checks: Read, Actions: Read**, used
  with `gh auth login --with-token` as `culture-fixer`;
- a SonarCloud token for reads (`grant set FIXER_SONAR_TOKEN - --hidden`).

On the **engine node** that dispatches to the bridges, seal the same two
bridge tokens under the node's grant store (for example as
`RULES_QWEN_FIXER_TOKEN` and `RULES_CODEX_FIXER_TOKEN`) and add them to the
node with `deploy/node/install.sh --secret ...`.

## 4. Install the bridges (as `culture-fixer`)

```bash
bash deploy/pr-fixer/install.sh --host "<spark2 tailnet IP>"          # plan only
bash deploy/pr-fixer/install.sh --host "<spark2 tailnet IP>" --apply
```

| Path | Content |
|---|---|
| `~/.local/share/cultureagent-bridges/venv` | `cultureagent==0.14.0` |
| `~/.config/cultureagent-bridges/qwen.json`, `codex.json` (mode 600) | bind the tailnet IP on 8093 / 8094; allowlist prefix `https://github.com/agentculture/`; one run at a time; `commit_author` the App bot |
| `~/.qwen/settings.json` (mode 600) | Qwen Code on cortex; the key comes from `QWEN_CUSTOM_API_KEY_CORTEX` |
| `~/.config/systemd/user/cultureagent-{qwen,codex}-bridge.service` | `grant run --inject ... -- cultureagent-<backend>-bridge --config ...` |

No token is written to a file. Every agent commit is authored as
`rules-culture-dev[bot] <337624453+rules-culture-dev[bot]@users.noreply.github.com>`,
the identity the App pushes as.

## 5. Register the actors

Two agent actors placed on spark2. `callback_url` is the API's LAN base URL
(the bridge posts to `POST /bridge-invocations/{id}/events`, which checks the
per-invocation token itself):

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

`codex-fixer` is the same with port `8094`, `grant:RULES_CODEX_FIXER_TOKEN`,
and `"sandbox": "workspace-write"` instead of `mode`. The qwen bridge refuses
a run without `mode`.

## 6. Verify

| Check | Expected |
|---|---|
| as `culture-fixer`: `cat /home/spark2/.config/culture-rules/node.env`, `ls /home/spark2/.config/gh`, `grant get RULES_GITHUB_APP_PRIVATE_KEY` | permission denied, or not found in this account's grant store |
| `curl -s -o /dev/null -w '%{http_code}' http://<spark2 tailnet IP>:8093/v1/capabilities` | `401` |
| the same with `-H "Authorization: Bearer <token>"`, on 8093 and 8094 | `200` and the capability document (`"pushes": false`) |
| `culture-rules actors list` | `qwen-fixer` and `codex-fixer`, machine `spark2` |

## On spark2

| Item | Value |
|---|---|
| Account | `culture-fixer` (planned; created by the operator) |
| Bridges | qwen on 8093, codex on 8094, tailnet only |
| Actors | `qwen-fixer`, `codex-fixer` (planned) |
