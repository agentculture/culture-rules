# rules.culture.dev: the tunnel, the loopback Access listener, and the hand-turns

Task t37 (claims c36, c13; honesty conditions h23, h13). This is the operator
recipe for putting the culture-rules web UI and API behind Cloudflare Access at
`https://rules.culture.dev`, on every host that serves it. It follows the
precedent of `nodes.culture.dev` in the culture-nodes repo.

**Everything here that a person applies on a host or in Cloudflare is a
hand-turn.** The build never runs `cultureflare ... --apply` and never touches
a real host; the ids table below is filled by the operator when they do.

## Topology

```text
browser --HTTPS--> Cloudflare edge (Access SSO)
                        |  tunnel (outbound from each serving host)
                        v
host: cloudflared-rules.service --HTTP--> 127.0.0.1:18765  (CULTURE_RULES_ACCESS_LISTEN)
                                                |
                                                v
                                        culture-rules API + built web UI
mesh / LAN clients --HTTP--> CULTURE_RULES_HOST:CULTURE_RULES_PORT  (service tokens only)
```

Two listeners, on purpose. Only the loopback Access listener honours a
`Cf-Access-Jwt-Assertion` header, pinned to the team domain and the Access app's
AUD tag. The LAN listener ignores that header, so a JWT captured elsewhere cannot
be replayed there. The loopback address is never published on a LAN interface.

The serving hosts are the hosts that run the culture-rules API: **spark**,
**thor** and **orin** (the same hosts that carry the replica set, see
`docs/operations/replica-set.md`). Each one runs its own `cloudflared`
connector with the **same** tunnel token, so Cloudflare load-balances across the
connectors and the hostname survives any one host going down.

## Prerequisites

- `cultureflare` on PATH, and an account-wide Cloudflare API token plus account
  id in the shell environment for the provisioning run only
  (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`). They never enter this
  repo, an env file or a unit.
- `cloudflared` installed on every serving host at `/usr/local/bin/cloudflared`
  (the unit's `ExecStart` uses an absolute path because systemd does not read
  `PATH`).
- `grant` installed for the unit user (in their per-user bin directory, e.g. `$HOME/.local/bin/grant`). Secret references
  are `grant:<NAME>`. Deviation d2 of the build replaced the earlier secrets
  manager with `grant`, so the plan text's sealing step is done with `grant`.
- The operator's email as the first Access allow entry (placeholder
  `<operator-email>`; no real address is written into the tree).
- **Synchronized clocks on every engine node** (NTP, e.g. `systemd-timesyncd`
  or `chrony`; check with `timedatectl`). The PR fixer's status comment writer
  lease (d26) tolerates at most 30 s of skew between nodes
  (`MAX_CLOCK_SKEW_S`); see `docs/operations/pr-fixer.md`, "One writer,
  desired state".

## Step 1: dry-run, then apply (once, from one host)

Dry-run first. It prints the plan and changes nothing:

```bash
cultureflare remote-login setup \
  --hostname rules.culture.dev \
  --service http://127.0.0.1:18765 \
  --allow <operator-email>
```

Read the plan. When it is what you expect, run the same command with `--apply`:

```bash
cultureflare remote-login setup \
  --hostname rules.culture.dev \
  --service http://127.0.0.1:18765 \
  --allow <operator-email> \
  --apply
```

One idempotent run creates the named tunnel with remote-managed ingress
`rules.culture.dev` to `http://127.0.0.1:18765`, the `rules.culture.dev` CNAME,
the Access application and an allow-by-email policy. Do not use cultureflare's own secret-sealing flag:
secrets go to `grant` in step 2. Add `--session-duration <value>` if the
session length is to be explicit rather than the 24 h default.

### Resulting ids (operator fills this in)

Record the ids from the `--apply` output on the hand-turn issue and here. None
of them is a secret, but they are deployment state, so the committed copy of
this table stays a template.

| Item | Value |
|---|---|
| tunnel name | `<tunnel-name>` |
| tunnel id | `<tunnel-id>` |
| Access app id | `<access-app-id>` |
| AUD tag | `<access-app-aud-tag>` |
| team domain | `<team>.cloudflareaccess.com` |
| applied on (date, host) | `<yyyy-mm-dd, host>` |

The AUD tag is also readable under Zero Trust, Access, Applications,
`rules.culture.dev`, Overview, Application Audience (AUD) Tag.

## Step 2: seal the tunnel token in grant (every serving host)

The connector token is the one the Cloudflare API returns for the tunnel
(`GET .../cfd_tunnel/<tunnel-id>/token`), sealed hidden so nothing can `cat` it:

```bash
# on the host, as the unit user; CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID and TUNNEL_ID set
curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/token" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert isinstance(d.get("result"), str) and d["result"], d; sys.stdout.write(d["result"])' \
  | grant set RULES_CULTURE_DEV_TUNNEL_TOKEN - --hidden \
      --purpose "cloudflared connector token for the rules.culture.dev tunnel" \
      --rotate-howto "re-read GET cfd_tunnel/<id>/token and grant set again"
grant show RULES_CULTURE_DEV_TUNNEL_TOKEN     # metadata only: hidden
```

The `assert` is load-bearing: a wrong `TUNNEL_ID` does not fail `curl`, it returns
an error envelope with a null `result`, and sealing that would only surface later
as a connector that never registers. The per-tunnel token can run this one
tunnel and nothing else, so it is the only Cloudflare credential that lives on a
serving host; the account-wide API token stays in the provisioning shell.

## Step 3: the Access listener (every serving host)

Set the three Access variables together in the API's environment file. The
template is `deploy/cloudflared/cloudflared-rules.env.example`:

- `CULTURE_RULES_ACCESS_LISTEN=127.0.0.1:18765`, the address the tunnel
  forwards to;
- `CULTURE_RULES_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com`;
- `CULTURE_RULES_ACCESS_AUD=<access-app-aud-tag>`, from the ids table.

All three set turns Access on; all three unset leaves it off; a partial tuple is
refused at start.

Set `CULTURE_RULES_NODE_NAME` to the name this host's engine node runs under
(for example `spark` on the host whose hostname is `spark-f8a9`). `/health`
reports on that node's heartbeat, and `culture-rules node run` defaults its
`--host` to the same variable, so put it in the environment of both units.
Unset, both fall back to the short hostname. A mismatch leaves `/health`
`degraded` while `/machines/status` shows the node online. `serve
--node-name` overrides the variable for the API alone.

Restart the API after the change. If the API runs in a
container, publish the port on the host loopback only (`127.0.0.1:18765:...`),
never on every interface.

## Step 4: install cloudflared-rules.service (every serving host)

The template is `deploy/cloudflared/cloudflared-rules.service`. Its one
placeholder is `@TUNNEL_TOKEN_SECRET@`, the grant secret name from step 2
(`RULES_CULTURE_DEV_TUNNEL_TOKEN`).

```bash
# on each of spark, thor and orin, from a checkout of this repo
sed 's/@TUNNEL_TOKEN_SECRET@/RULES_CULTURE_DEV_TUNNEL_TOKEN/' \
  deploy/cloudflared/cloudflared-rules.service \
  | install -D -m 0644 /dev/stdin "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/cloudflared-rules.service"
systemctl --user daemon-reload
systemctl --user enable --now cloudflared-rules
loginctl enable-linger "$USER"      # so the user unit survives logout and reboot
systemctl --user status cloudflared-rules
journalctl --user -u cloudflared-rules -n 50
```

`ExecStart` is `grant run --inject TUNNEL_TOKEN=RULES_CULTURE_DEV_TUNNEL_TOKEN --
cloudflared tunnel --no-autoupdate run`, so the token reaches cloudflared as an
environment variable at exec time and appears in no file or argv.

## Verification

Per serving host:

```bash
systemctl --user is-active cloudflared-rules          # active
journalctl --user -u cloudflared-rules -n 5           # "Registered tunnel connection" lines
ss -ltn '( sport = :18765 )'                          # 127.0.0.1:18765 only, never 0.0.0.0
```

From anywhere, unauthenticated:

```bash
curl -sI https://rules.culture.dev/ | head -2
# HTTP/2 302, location: https://<team>.cloudflareaccess.com/...  (the Access redirect)
```

An unauthenticated request must get the `302` to the Access login, never a
Cloudflare 1033 or 502 (those mean a connector or origin problem) and never
the app's content. Stop one host's unit and repeat: the `302` must persist
while another host's connector is up.

## Cloudflare cache rule (never cache the app)

A zone "Cache everything" rule on `culture.dev` (edge TTL 2 h) once served
stale `/api/*` answers from the edge. The fix, applied live by the operator, is
the zone cache rule **"Never cache rules.culture.dev except hashed
`/assets/*`"**. Keep it: it must stay ordered before any broader cache rule.

The origin cooperates. The API adds `Cache-Control: no-store` to every response
that lacks its own header (`NoStoreByDefault` in `culture_rules/server/caching.py`).
The HTML shell is `private, no-cache`, and Vite's content-hashed `/assets/*` are
`private, max-age=31536000, immutable`. After any change to the zone's cache
rules, check:

look at the response headers of an `/api/*` request in the browser's network
tab (an Access-authenticated session): `cache-control: no-store`, and
`cf-cache-status` must never be `HIT`. A hashed `/assets/*` file may be cached.

## Webhook receivers and the Access Bypass paths

The API mounts two receivers, exact paths only (`HOOK_PATHS` in
`culture_rules/server/app.py`), both `POST`, both on the loopback Access
listener:

| Path | Surface |
|---|---|
| `/hooks/github` | GitHub App webhook |
| `/hooks/jira` | Jira system webhook |

GitHub and Jira cannot sign in through Access, so each path needs its own
**Bypass** (**Include: Everyone**). Access policies carry no path, so each one
is a separate self-hosted Access application whose domain is the exact path,
`rules.culture.dev/hooks/github` and `rules.culture.dev/hooks/jira`; the more
specific path wins over the `rules.culture.dev` application. Never widen them to
`/hooks/*` or `/`. Both exist (created through the Cloudflare API, 2026-10-05):
an unsigned `POST` to either path reaches the API and answers `401`, while any
other path still redirects to the Access login.

The app authenticates every delivery itself, so Bypass does not mean open:

- GitHub: `X-Hub-Signature-256` HMAC with the webhook secret.
- Jira: `X-Hub-Signature` HMAC, or the `?token=` query value (the webhook
  token). `HookQueryFilter` in `culture_rules/server/serve.py` strips the query
  string from access-log lines for webhook paths, so the token is not logged.

A delivery for a disabled app actor answers `202` and is dropped.

### Bridge callbacks

One more `POST` route skips principal resolution: the bridge callback
`/bridge-invocations/bri_<24 hex>/events` (`BRIDGE_CALLBACK_RE` in
`culture_rules/server/app.py`, full match, the same raw-path and `/api` rules).
A cultureagent bridge posts its heartbeat and terminal events there for a
bridge agent actor's invocation. The only credential is the per-attempt
callback token the actor handed the bridge (`Authorization: Bearer`), checked
against its stored hash. It is no credential on any other route. The route
records the event in the `bridge_invocations` collection, and each node cycle
delivers recorded results to their runs. Point a bridge actor's
`params.callback_url` at a listener the bridge host can reach, for example the
LAN listener over the tailnet. No Access Bypass is needed for that.

## App secrets inside the services (hidden `grant` secrets)

Some secrets are used inside the API and node processes, not by a subprocess:
the webhook HMAC secrets, the GitHub App private key (JWT signing), the Discord
bot token and the Jira token. `grant get` refuses a `--hidden` secret by design,
so the service unit injects each one when it starts, exactly as it injects the
Mongo URI: `--inject CULTURE_RULES_SECRET_<NAME>=<NAME>`, where `<NAME>` is
the grant name upper-cased with every other character turned into `_`. A
`grant:<NAME>` reference resolves to that variable first, and to `grant get`
only when it is unset.

- **API** (the serving host): inject the webhook secrets and the Jira token,
  which the receivers use to verify deliveries and refetch issues.
- **Engine node**: `deploy/node/install.sh --secret NAME` (repeatable) adds the
  injections to the node unit.
- **Pin each app actor to the machine that holds its secrets** (`machine` on
  the actor). A rule action naming that actor in `params.actor` then runs on
  that machine, and only that machine's node holds the Discord gateway lease.
  On rules.culture.dev the app actors live on `spark`.

## GitHub App (dedicated to culture-rules)

The full setup, invite and troubleshooting guide is
[`docs/actors/github.md`](../actors/github.md); this section is the summary.

Hand-turns, operator only; secrets go to `grant`, never into the repo.

1. Create a new GitHub App for culture-rules. Webhook URL
   `https://rules.culture.dev/hooks/github`, with a webhook secret.
2. Permissions: Issues read and write, Pull requests read and write, Metadata
   read. Subscribe to `pull_request`, `issue_comment`, `issues`,
   `pull_request_review`.
3. Install it on the allow-listed repositories only.
4. Seal the credentials (names are a proposal; any name works if the actor
   references it):

   ```bash
   grant set RULES_GITHUB_APP_PRIVATE_KEY - --hidden < app-private-key.pem
   grant set RULES_GITHUB_WEBHOOK_SECRET - --hidden
   ```

5. Create an `app` actor (`params.surface = "github"`) with
   `connection = {app_id, installation_id, private_key: "grant:RULES_GITHUB_APP_PRIVATE_KEY",
   webhook_secret: "grant:RULES_GITHUB_WEBHOOK_SECRET", repos: [...]}`, the
   `events` it emits, the `actions` it may perform, and
   `self_identity = "<app-slug>[bot]"` so its own comments are tagged and do not
   retrigger rules. A literal secret is refused on save with `secret_literal`.
   The private key needs the `github` extra (`cryptography`).

## Discord bot

The full setup, invite and troubleshooting guide is
[`docs/actors/discord.md`](../actors/discord.md); this section is the summary.

1. Create a Discord application and bot. Enable the privileged **MESSAGE
   CONTENT** intent. Invite it with permission to read and send messages in the
   target channels.
2. `grant set RULES_DISCORD_BOT_TOKEN - --hidden`.
3. Create an `app` actor with `surface = "discord"`,
   `events = ["discord.message.created"]`,
   `connection = {bot_token: "grant:RULES_DISCORD_BOT_TOKEN", guild_id, channels: [...]}`
   (the channel allow-list).

Exactly one node holds the gateway connection, through the named lease
`discord-gateway:<actor id>`; another node takes over if the holder stops.
`culture-rules node run --once` never connects. Needs the `discord` extra.

## Jira webhook and service account

The full setup, invite and troubleshooting guide is
[`docs/actors/jira.md`](../actors/jira.md); this section is the summary.

1. Seal the service-account token (`JIRA_SERVICE_ACCOUNT_TOKEN` already exists
   in `grant`) and a webhook token: `grant set RULES_JIRA_WEBHOOK_TOKEN - --hidden`.
2. In Jira, Settings, System, WebHooks, register
   `https://rules.culture.dev/hooks/jira?token=<webhook-token>` (or configure an
   HMAC secret instead) for issue created, issue updated and comment created.
3. Create an `app` actor with `surface = "jira"` and
   `connection = {site, email, token, webhook_token, projects: [...]}`, where
   `token` is the reference `grant:JIRA_SERVICE_ACCOUNT_TOKEN` and
   `webhook_token` is `grant:RULES_JIRA_WEBHOOK_TOKEN`. A scoped service-account
   token works only through the Atlassian gateway: set `api_base` to
   `https://api.atlassian.com/ex/jira/<cloudId>` (the cloud id is served at
   `https://<site>/_edge/tenant_info`). Leave `projects` unset to accept every
   project, including ones created later.

## Kill switches

From narrowest to widest. All are admin verbs and audited; writes need
`--apply`.

- **Disable an app actor** (`enabled: false`): its ingest stops (the receivers
  answer `202` and drop the delivery), its Discord gateway disconnects, and any
  action naming it fails `actor_unavailable`.
- **Drain a machine**: `culture-rules machines drain <name> --apply` stops new
  steps being placed there; `machines undrain` reverses it.
- **Pause the engine**: `culture-rules runs pause --apply` (API
  `POST /controls/pause`; `runs resume` lifts it). New trigger events are
  dropped while paused; see `docs/operations/pause.md`.

## Rate cap and event depth

A rule fires at most 60 times per trailing hour by default; set
`trigger.params.max_fires_per_hour` to change it. A `schedule` trigger is exempt
from the default cap (deviation d2) unless it sets the parameter itself. The events
subscription depth is `CULTURE_RULES_EVENTS_DEPTH` (default 4).

## Upgrade order and rollout rule

Install the new wheel on **all four engine nodes before enabling any new
trigger or action kind**: spark, thor, orin, then spark2. spark2 is offline and
is upgraded from a wheelhouse (`deploy/node/README.md`). A node that lacks a
kind would otherwise fail those runs. Use `deploy/node/install.sh`
(dry-run first, then `--apply`), restart the API on the serving hosts, and
confirm each node's heartbeat in `GET /machines/status` before turning
a new kind on.

After every node runs the new wheel, run the two one-off migrations (admin, dry-run
by default; review the dry-run output, then repeat with `--apply`):

1. `culture-rules rules migrate-typeless` lists event rules without a
   `params.type`; `--apply` disables each one with an audit record and deletes
   nothing. Give each a type in the editor before re-enabling it.
2. `culture-rules runs backfill-ids` fills the top-level `rule_id` and
   `workflow_id` on run documents written before 0.11.0, so rule history and
   run filters find them; a second run changes nothing.

## Hand-turn checklist

File each against the cycle issue, or comment on it, when applied:

1. Install `cloudflared` at `/usr/local/bin/cloudflared` on spark, thor, orin.
2. Dry-run `cultureflare remote-login setup`, review, then run it with `--apply`.
3. Fill the ids table (tunnel name, tunnel id, Access app id, AUD tag).
4. Optionally set the Access session duration explicitly.
5. `grant set RULES_CULTURE_DEV_TUNNEL_TOKEN` on each serving host (step 2).
6. Set the three `CULTURE_RULES_ACCESS_*` variables and restart the API on
   each host (step 3).
7. Install and enable `cloudflared-rules.service`, and `loginctl enable-linger`
   (step 4), on each host.
8. Run the verification block and paste the `302` headers on the issue.
9. Adding a person later is an edit to the Access allow policy, plus an entry in
   `CULTURE_RULES_ADMINS` / `CULTURE_RULES_EDITORS` for any role above viewer.
