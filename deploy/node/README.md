# deploy/node: the engine node installer

`install.sh` installs or upgrades the culture-rules engine node
(`culture-rules node run`) on the host it runs on, as the unit user. It
creates a private venv, installs the wheel with the extras
`store,events,github,discord`, writes `node.env` and a systemd user unit
(`culture-rules-node.service`).

It is **dry-run by default**, like the CLI's writes: without `--apply` it
prints the plan and writes nothing. It is idempotent, so the same command
upgrades a node.

## What it writes

| Path | Content |
|---|---|
| `$HOME/.local/share/culture-rules/venv` | the venv, with `culture-rules[<extras>]` |
| `$HOME/.config/culture-rules/mongo-ca.pem` | the MongoDB TLS CA, copied from `--ca-file` |
| `$HOME/.config/culture-rules/node.env` (mode 600) | `CULTURE_RULES_MONGO_TLS_CA_FILE`, `CULTURE_RULES_NODE_NAME`, optionally `EVENTS_BROKER_HOST`, `EVENTS_BROKER_PORT` and `CULTURE_RULES_GATE_RUN_AS` |
| `$HOME/.config/systemd/user/culture-rules-node.service` | the unit |

No secret is written anywhere. The MongoDB URI is a `grant` secret
(`RULES_MONGO_URI` by default, `--mongo-secret` to rename it). The unit's
`ExecStart` is `grant run --inject CULTURE_RULES_MONGO_URI=<secret> --
.../culture-rules node run`, so the URI reaches the daemon only as an
environment variable at exec time. Seal it once per host with `grant set`
before the first start.

## Flags

| Flag | Meaning |
|---|---|
| `--node-name NAME` | required (or env `NODE_NAME`); the machine name, for example `spark`, `thor`, `orin`, `spark2` |
| `--wheel PATH` | the wheel; default is the `culture_rules-*.whl` beside the script |
| `--ca-file PATH` | the Mongo TLS CA; default is `ca.pem` beside the script, if present; an upgrade without either keeps the installed `mongo-ca.pem` |
| `--mongo-secret NAME` | grant secret holding the URI (default `RULES_MONGO_URI`) |
| `--secret NAME` | repeatable; a grant secret an app actor on this machine references, injected as `CULTURE_RULES_SECRET_<NAME>` so a `--hidden` secret works |
| `--extras LIST` | default `store,events,github,discord` |
| `--wheelhouse DIR` | offline install: `--no-index --find-links DIR` |
| `--events-host HOST`, `--events-port PORT` | optional `EVENTS_BROKER_*` lines in `node.env` |
| `--python VERSION` | Python for the venv (default 3.12) |
| `--gate-run-as PREFIX` | the PR fixer gate's run-as prefix, written to `node.env` as `CULTURE_RULES_GATE_RUN_AS`; a prefix starting with `sudo` also makes the unit `NoNewPrivileges=false` (see below) |
| `--apply` | actually install |

## The unit and `NoNewPrivileges`

The unit sets `NoNewPrivileges=true`, so the node and everything it starts
can never gain privileges. A node that runs the PR fixer's test gate through
sudo (`--gate-run-as 'sudo -n -u culture-fixer -- ...'`, see
[`docs/operations/pr-fixer.md`](../../docs/operations/pr-fixer.md) section
5) cannot keep it: with the kernel's `no_new_privs` flag set, sudo refuses to
run. So a `--gate-run-as` prefix whose first word is `sudo` writes
`NoNewPrivileges=false`, with a comment saying why. Without the flag, or with
a prefix that is not sudo, the unit keeps `NoNewPrivileges=true` and
`node.env` gets no `CULTURE_RULES_GATE_RUN_AS` line.

`node.env` is rewritten on every run, so pass `--gate-run-as` on every
upgrade of a gate node. The alternative for the unit is a drop-in,
`$HOME/.config/systemd/user/culture-rules-node.service.d/gate-sudo.conf`,
holding `[Service]` and `NoNewPrivileges=false`. If the node starts with a
sudo prefix and `no_new_privs` set, it logs an error naming both fixes, and
its gate steps fail `run_as_blocked`.

## Connected host

Copy the wheel (and `ca.pem`) next to the script, or point at them:

```bash
bash deploy/node/install.sh --node-name thor \
  --wheel ./culture_rules-<version>-py3-none-any.whl --ca-file ./ca.pem        # plan only
bash deploy/node/install.sh --node-name thor \
  --wheel ./culture_rules-<version>-py3-none-any.whl --ca-file ./ca.pem --apply
loginctl enable-linger "$USER"      # once, so the unit survives logout and reboot
```

Check with `systemctl --user status culture-rules-node` and
`GET /machines/status` (the node's heartbeat appears).

## Offline host (spark2)

spark2 cannot reach a package index, so build a wheelhouse on a connected
host of the **same platform and Python** as the target, then carry it over.
Check the target first (`uname -m`, `python3 --version`). A wheelhouse built
on x86_64 does not install on aarch64.

```bash
# on a connected host with matching architecture and Python 3.12, from the built wheel
uv build                                   # produces dist/culture_rules-<version>-py3-none-any.whl
mkdir wheelhouse
python3 -m pip download --dest wheelhouse \
  "dist/culture_rules-<version>-py3-none-any.whl[store,events,github,discord]"
cp dist/culture_rules-<version>-py3-none-any.whl ca.pem wheelhouse/
tar czf node-offline.tgz wheelhouse install.sh   # install.sh from deploy/node/
```

If the connected host differs from the target, add `--platform`,
`--python-version` and `--only-binary=:all:` to `pip download` to fetch
wheels for the target. Then on the offline host:

```bash
tar xzf node-offline.tgz
bash install.sh --node-name spark2 --wheel wheelhouse/culture_rules-<version>-py3-none-any.whl \
  --ca-file wheelhouse/ca.pem --wheelhouse wheelhouse --apply
```

`uv` and a Python 3.12 interpreter must already be on the offline host; the
installer does not fetch them. The `grant` secret for the Mongo URI is sealed
on the host as usual.

## Verification

Dry-run and static check, runnable anywhere:

```bash
shellcheck deploy/node/install.sh
bash deploy/node/install.sh --help
```
