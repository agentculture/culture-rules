#!/usr/bin/env bash
# Install (or upgrade) the culture-rules engine node on THIS host, as the unit user.
#
# Dry-run by default: prints the plan and writes nothing. Pass --apply to commit it.
# See deploy/node/README.md. Idempotent: re-running upgrades the wheel and rewrites
# node.env and the systemd user unit in place.
#
# No secret is ever written to a file or argv. The MongoDB URI lives in a `grant`
# secret and is injected into the daemon's environment at exec time
# (grant run --inject CULTURE_RULES_MONGO_URI=<secret>).
set -euo pipefail

SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

NODE_NAME="${NODE_NAME:-}"
WHEEL=""
CA_FILE=""
MONGO_SECRET="RULES_MONGO_URI"
SECRETS=()
EXTRAS="store,events,github,discord,yaml"
WHEELHOUSE=""
EVENTS_HOST=""
EVENTS_PORT=""
PYTHON_VERSION="3.12"
GATE_RUN_AS=""
APPLY=0

usage() {
  cat <<'USAGE'
Usage: install.sh --node-name NAME [options]

Installs the culture-rules wheel into a private venv, writes node.env and a
systemd user unit (culture-rules-node.service). Dry-run unless --apply is given.

Required:
  --node-name NAME      machine name the node runs under (also: env NODE_NAME)

Options:
  --wheel PATH          culture_rules-*.whl to install (default: the one beside this script)
  --ca-file PATH        MongoDB TLS CA bundle, copied to ~/.config/culture-rules/mongo-ca.pem
                        (default: ca.pem beside this script, when present)
  --mongo-secret NAME   grant secret holding the Mongo URI (default: RULES_MONGO_URI)
  --secret NAME         a grant secret an app actor on this machine references (repeatable);
                        injected as CULTURE_RULES_SECRET_<NAME>, so a --hidden secret works
  --extras LIST         extras to install (default: store,events,github,discord,yaml)
  --wheelhouse DIR      offline install: pip --no-index --find-links DIR
  --events-host HOST    write EVENTS_BROKER_HOST into node.env (optional)
  --events-port PORT    write EVENTS_BROKER_PORT into node.env (optional)
  --python VERSION      Python for the venv (default: 3.12)
  --gate-run-as PREFIX  write CULTURE_RULES_GATE_RUN_AS=PREFIX into node.env (the PR fixer
                        gate's run-as prefix, e.g. 'sudo -n -u culture-fixer -- /usr/bin/env
                        PATH=...'); a prefix starting with sudo also sets
                        NoNewPrivileges=false in the unit, or sudo cannot run
  --apply               perform the install (without it nothing is written)
  -h, --help            show this help
USAGE
}

die() {
  echo "install.sh: $*" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --node-name) NODE_NAME="${2:?--node-name needs a value}"; shift 2 ;;
    --wheel) WHEEL="${2:?--wheel needs a value}"; shift 2 ;;
    --ca-file) CA_FILE="${2:?--ca-file needs a value}"; shift 2 ;;
    --mongo-secret) MONGO_SECRET="${2:?--mongo-secret needs a value}"; shift 2 ;;
    --secret) SECRETS+=("${2:?--secret needs a value}"); shift 2 ;;
    --extras) EXTRAS="${2:?--extras needs a value}"; shift 2 ;;
    --wheelhouse) WHEELHOUSE="${2:?--wheelhouse needs a value}"; shift 2 ;;
    --events-host) EVENTS_HOST="${2:?--events-host needs a value}"; shift 2 ;;
    --events-port) EVENTS_PORT="${2:?--events-port needs a value}"; shift 2 ;;
    --python) PYTHON_VERSION="${2:?--python needs a value}"; shift 2 ;;
    --gate-run-as) GATE_RUN_AS="${2:?--gate-run-as needs a value}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

[ -n "$NODE_NAME" ] || die "--node-name (or NODE_NAME) is required"
case "$NODE_NAME" in
  *[!A-Za-z0-9._-]*) die "node name may contain only letters, digits, dot, dash, underscore" ;;
esac
case "$MONGO_SECRET" in
  *[!A-Za-z0-9_]*) die "--mongo-secret must be a grant secret name (letters, digits, underscore)" ;;
esac
case "$EXTRAS" in
  *[!a-z,]*) die "--extras must be a comma-separated list of extra names" ;;
esac

if [ -z "$WHEEL" ]; then
  for candidate in "$SELF_DIR"/culture_rules-*.whl; do
    [ -e "$candidate" ] && WHEEL="$candidate"
  done
fi
[ -n "$WHEEL" ] || die "no wheel: pass --wheel or put culture_rules-*.whl beside this script"
[ -f "$WHEEL" ] || die "wheel not found: $WHEEL"
if [ -z "$CA_FILE" ] && [ -f "$SELF_DIR/ca.pem" ]; then
  CA_FILE="$SELF_DIR/ca.pem"
fi
[ -z "$CA_FILE" ] || [ -f "$CA_FILE" ] || die "CA file not found: $CA_FILE"
[ -z "$WHEELHOUSE" ] || [ -d "$WHEELHOUSE" ] || die "wheelhouse is not a directory: $WHEELHOUSE"

# The gate's run-as prefix (culture_rules.actors.gate, docs/operations/pr-fixer.md section 5).
# One line in node.env: no newline or carriage return. sudo needs the unit to allow new
# privileges: with NoNewPrivileges=true the kernel's no_new_privs flag makes sudo refuse.
NO_NEW_PRIVS=true
GATE_ENV_LINE=""
if [ -n "$GATE_RUN_AS" ]; then
  case "$GATE_RUN_AS" in
    *$'\n'* | *$'\r'*) die "--gate-run-as must be a single line" ;;
  esac
  read -r GATE_FIRST _ <<<"$GATE_RUN_AS" || true
  [ -n "${GATE_FIRST:-}" ] || die "--gate-run-as must not be blank"
  [ "$(basename -- "$GATE_FIRST")" != "sudo" ] || NO_NEW_PRIVS=false
  # systemd EnvironmentFile double quotes: backslash-escape \ " $ and backtick.
  quoted=${GATE_RUN_AS//\\/\\\\}
  quoted=${quoted//\"/\\\"}
  quoted=${quoted//\$/\\\$}
  quoted=${quoted//\`/\\\`}
  GATE_ENV_LINE="CULTURE_RULES_GATE_RUN_AS=\"$quoted\""
fi

CFG="$HOME/.config/culture-rules"
VENV="$HOME/.local/share/culture-rules/venv"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/culture-rules-node.service"
ENV_FILE="$CFG/node.env"
CA_DEST="$CFG/mongo-ca.pem"
UV=$(command -v uv || echo "$HOME/.local/bin/uv")

INSTALL_ARGS=(pip install -q --python "$VENV/bin/python" --reinstall-package culture-rules)
if [ -n "$WHEELHOUSE" ]; then
  INSTALL_ARGS+=(--no-index --find-links "$WHEELHOUSE")
fi
INSTALL_ARGS+=("${WHEEL}[${EXTRAS}]")

# An upgrade without --ca-file keeps the CA bundle an earlier install put in place.
KEEP_CA=0
[ -n "$CA_FILE" ] || [ ! -f "$CA_DEST" ] || KEEP_CA=1

# grant secret NAME -> "--inject CULTURE_RULES_SECRET_<NAME>=NAME" (see culture_rules.actors.secrets)
INJECTS=""
for name in "${SECRETS[@]}"; do
  case "$name" in
    *[!A-Za-z0-9._/-]* | "") die "secret name may contain only letters, digits, . _ / -: $name" ;;
  esac
  var="CULTURE_RULES_SECRET_$(printf '%s' "$name" | tr -c 'A-Za-z0-9_' '_' | tr 'a-z' 'A-Z')"
  INJECTS="$INJECTS --inject $var=$name"
done

render_env() {
  echo "# culture-rules engine node on $NODE_NAME (written by deploy/node/install.sh)"
  [ -z "$CA_FILE" ] && [ "$KEEP_CA" -eq 0 ] || echo "CULTURE_RULES_MONGO_TLS_CA_FILE=$CA_DEST"
  echo "CULTURE_RULES_NODE_NAME=$NODE_NAME"
  [ -z "$EVENTS_HOST" ] || echo "EVENTS_BROKER_HOST=$EVENTS_HOST"
  [ -z "$EVENTS_PORT" ] || echo "EVENTS_BROKER_PORT=$EVENTS_PORT"
  [ -z "$GATE_ENV_LINE" ] || printf '%s\n' "$GATE_ENV_LINE"
}

render_hardening() {
  if [ "$NO_NEW_PRIVS" = false ]; then
    echo "# The gate's run-as prefix (CULTURE_RULES_GATE_RUN_AS) starts with sudo, and sudo refuses"
    echo "# to run when the no_new_privs flag is set: install.sh --gate-run-as relaxes it."
  fi
  echo "NoNewPrivileges=$NO_NEW_PRIVS"
}

render_unit() {
  cat <<UNIT
[Unit]
Description=culture-rules engine node daemon ($NODE_NAME)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=%h/.config/culture-rules/node.env
# The Mongo URI (with its password) is injected into the child environment, never argv or a file.
ExecStart=%h/.local/bin/grant run --inject CULTURE_RULES_MONGO_URI=$MONGO_SECRET$INJECTS -- %h/.local/share/culture-rules/venv/bin/culture-rules node run
Restart=always
RestartSec=5
UMask=0077
$(render_hardening)

[Install]
WantedBy=default.target
UNIT
}

echo "Plan for node '$NODE_NAME':"
echo "  wheel:        $WHEEL  (extras: $EXTRAS)"
if [ -n "$WHEELHOUSE" ]; then
  echo "  mode:         OFFLINE, pip --no-index --find-links $WHEELHOUSE"
else
  echo "  mode:         online (default index)"
fi
echo "  venv:         $VENV (python $PYTHON_VERSION, created if missing)"
if [ "$KEEP_CA" -eq 1 ]; then
  echo "  CA file:      keep the installed $CA_DEST"
else
  echo "  CA file:      ${CA_FILE:-none} -> $CA_DEST"
fi
echo "  node.env:     $ENV_FILE (mode 600)"
render_env | sed 's/^/                /'
echo "  unit:         $UNIT"
if [ "$NO_NEW_PRIVS" = false ]; then
  echo "                NoNewPrivileges=false (the gate's run-as prefix uses sudo)"
else
  echo "                NoNewPrivileges=true"
fi
echo "  mongo secret: grant:$MONGO_SECRET (injected at exec time; never written)"
echo "  exec:         grant run --inject CULTURE_RULES_MONGO_URI=$MONGO_SECRET$INJECTS -- culture-rules node run"
echo "  then:         systemctl --user daemon-reload; enable --now culture-rules-node"

if [ "$APPLY" -ne 1 ]; then
  echo
  echo "Dry run: nothing was written. Re-run with --apply to install."
  exit 0
fi

mkdir -p "$CFG" "$(dirname "$VENV")" "$UNIT_DIR"
chmod 700 "$CFG"
if [ -n "$CA_FILE" ]; then
  install -m 644 "$CA_FILE" "$CA_DEST"
fi

[ -x "$UV" ] || die "uv not found (looked on PATH and in $HOME/.local/bin)"
if [ ! -x "$VENV/bin/python" ]; then
  "$UV" venv -q --python "$PYTHON_VERSION" "$VENV"
fi
"$UV" "${INSTALL_ARGS[@]}"

render_env >"$ENV_FILE"
chmod 600 "$ENV_FILE"
render_unit >"$UNIT"

systemctl --user daemon-reload
systemctl --user enable --now culture-rules-node 2>&1 | grep -v '^Created' || true
systemctl --user restart culture-rules-node
"$VENV/bin/culture-rules" --version
echo "Installed. Enable lingering once so the unit survives logout: loginctl enable-linger \"\$USER\""
