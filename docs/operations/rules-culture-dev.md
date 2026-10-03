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
