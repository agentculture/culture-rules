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
  repositories, permissions **Contents: Read, Pull requests: Read, Checks:
  Read, Actions: Read** (Metadata: Read is implied).
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

## 5. Verify

| Check | Expected |
|---|---|
| as `culture-fixer`: `cat /home/spark2/.config/culture-rules/node.env`, `ls /home/spark2/.qwen`, `grant list` | permission denied twice; `grant list` shows only the four `FIXER_*` names |
| `curl -s -o /dev/null -w '%{http_code}' http://<spark2 tailnet IP>:8093/v1/capabilities` | `401` |
| the same with `-H "Authorization: Bearer <token>"` | `200` and the capability document (`"pushes": false`) |
| as `culture-fixer`, through grant: `gh api repos/agentculture/culture-rules/pulls` | `200`; a `gh pr merge` or a push is refused |
| `culture-rules actors list` | `qwen-fixer`, machine `spark2` |

## On spark2

| Item | Value |
|---|---|
| Account | `culture-fixer` (planned; created by the operator) |
| Bridge | qwen on 8093, tailnet only |
| Actor | `qwen-fixer` (planned) |
