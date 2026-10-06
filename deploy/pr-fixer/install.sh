#!/usr/bin/env bash
# Install (or upgrade) the cultureagent qwen bridge for the PR fixer, as the unprivileged
# fixer account (culture-fixer), on THIS host. Codex is not installed here (d8).
#
# Dry-run by default: prints the plan (including the bridge config) and writes nothing.
# Pass --apply to commit it. See docs/operations/pr-fixer.md. Idempotent.
#
# No secret is ever written to a file or argv. The bridge's bearer token, the cortex API
# key and the agent's read-only GitHub and SonarCloud tokens live in this account's grant
# store and are injected at exec time (grant run --inject ...).
set -euo pipefail

HOST=""
CULTUREAGENT_VERSION="0.14.0"
QWEN_VERSION="0.24.7"
CORTEX_URL="http://localhost:8000/v1"
QWEN_TOKEN_SECRET="FIXER_QWEN_BRIDGE_TOKEN"
GITHUB_TOKEN_SECRET="FIXER_GITHUB_TOKEN"
SONAR_TOKEN_SECRET="FIXER_SONAR_TOKEN"
CORTEX_KEY_SECRET="FIXER_CORTEX_API_KEY"
COMMIT_AUTHOR="rules-culture-dev[bot] <337624453+rules-culture-dev[bot]@users.noreply.github.com>"
ALLOW_PREFIX="https://github.com/agentculture/"
PYTHON_VERSION="3.12"
APPLY=0

usage() {
  cat <<'USAGE'
Usage: install.sh --host TAILNET_IP [options]

Installs cultureagent (the qwen bridge) into a private venv, writes the bridge
config, Qwen Code's cortex settings and a systemd user unit.
Dry-run unless --apply is given. Run it as the fixer account, not as root.

Required:
  --host ADDR                 address both bridges bind (the host's tailnet IP)

Options:
  --cultureagent-version V    cultureagent release to install (default: 0.14.0)
  --qwen-version V            Qwen Code version the bridge's handshake accepts (default: 0.24.7)
  --cortex-url URL            OpenAI-compatible endpoint serving cortex (default: http://localhost:8000/v1)
  --qwen-token-secret NAME    grant secret: the qwen bridge's bearer token (default: FIXER_QWEN_BRIDGE_TOKEN)
  --github-token-secret NAME  grant secret: the agent's read-only GitHub token (default: FIXER_GITHUB_TOKEN)
  --sonar-token-secret NAME   grant secret: the agent's SonarCloud read token (default: FIXER_SONAR_TOKEN)
  --cortex-key-secret NAME    grant secret: the cortex API key (default: FIXER_CORTEX_API_KEY)
  --python VERSION            Python for the venv (default: 3.12)
  --apply                     perform the install (without it nothing is written)
  -h, --help                  show this help
USAGE
}

die() {
  echo "install.sh: $*" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="${2?--host needs a value}"; shift 2 ;;
    --cultureagent-version) CULTUREAGENT_VERSION="${2:?--cultureagent-version needs a value}"; shift 2 ;;
    --qwen-version) QWEN_VERSION="${2:?--qwen-version needs a value}"; shift 2 ;;
    --cortex-url) CORTEX_URL="${2:?--cortex-url needs a value}"; shift 2 ;;
    --qwen-token-secret) QWEN_TOKEN_SECRET="${2:?--qwen-token-secret needs a value}"; shift 2 ;;
    --github-token-secret) GITHUB_TOKEN_SECRET="${2:?--github-token-secret needs a value}"; shift 2 ;;
    --sonar-token-secret) SONAR_TOKEN_SECRET="${2:?--sonar-token-secret needs a value}"; shift 2 ;;
    --cortex-key-secret) CORTEX_KEY_SECRET="${2:?--cortex-key-secret needs a value}"; shift 2 ;;
    --python) PYTHON_VERSION="${2:?--python needs a value}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

case "$HOST" in
  "" | 0.0.0.0 | "::" | *[!A-Za-z0-9.:-]*) die "--host must be one concrete address (the tailnet IP), not empty or a wildcard" ;;
esac
for v in "$CULTUREAGENT_VERSION" "$QWEN_VERSION"; do
  case "$v" in *[!0-9.]* | "") die "versions are dotted numbers: $v" ;; esac
done
for s in "$QWEN_TOKEN_SECRET" "$CORTEX_KEY_SECRET" "$GITHUB_TOKEN_SECRET" "$SONAR_TOKEN_SECRET"; do
  case "$s" in *[!A-Za-z0-9_]* | "") die "grant secret names are letters, digits, underscore: $s" ;; esac
done
case "$CORTEX_URL" in http://* | https://*) ;; *) die "--cortex-url must be an http(s) URL" ;; esac
[ "$(id -u)" -ne 0 ] || die "run this as the fixer account, not root"

CFG="$HOME/.config/cultureagent-bridges"
VENV="$HOME/.local/share/cultureagent-bridges/venv"
UNIT_DIR="$HOME/.config/systemd/user"
QWEN_SETTINGS="$HOME/.qwen/settings.json"
UV=$(command -v uv || echo "$HOME/.local/bin/uv")

render_config() {
  printf '{\n'
  printf '  "host": "%s",\n  "port": 8093,\n' "$HOST"
  printf '  "repo_allowlist_prefixes": ["%s"],\n' "$ALLOW_PREFIX"
  printf '  "max_concurrent": 1,\n  "always_async": true,\n'
  printf '  "default_model": "cortex",\n'
  printf '  "qwen_agent_versions": ["%s"],\n' "$QWEN_VERSION"
  printf '  "commit_author": "%s"\n' "$COMMIT_AUTHOR"
  printf '}\n'
}

render_qwen_settings() {
  cat <<JSON
{
  "security": {"auth": {"selectedType": "openai"}},
  "model": {"name": "cortex", "baseUrl": "$CORTEX_URL"},
  "modelProviders": {
    "openai": [
      {"id": "cortex", "name": "cortex", "baseUrl": "$CORTEX_URL",
       "envKey": "QWEN_CUSTOM_API_KEY_CORTEX"}
    ]
  }
}
JSON
}

# GH_TOKEN and SONAR_TOKEN reach the agent too: the bridge passes its environment on.
INJECTS="--inject QWEN_BRIDGE_AUTH_TOKEN=$QWEN_TOKEN_SECRET --inject QWEN_CUSTOM_API_KEY_CORTEX=$CORTEX_KEY_SECRET --inject GH_TOKEN=$GITHUB_TOKEN_SECRET --inject SONAR_TOKEN=$SONAR_TOKEN_SECRET"

render_unit() {
  cat <<UNIT
[Unit]
Description=cultureagent qwen bridge (PR fixer)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
# Every token and the cortex key reach the bridge only as environment at exec time.
ExecStart=%h/.local/bin/grant run $INJECTS -- $VENV/bin/cultureagent-qwen-bridge --config %h/.config/cultureagent-bridges/qwen.json
Restart=always
RestartSec=5
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
UNIT
}

echo "Plan for the PR-fixer qwen bridge as $(id -un) on $HOST:"
echo "  venv:      $VENV (python $PYTHON_VERSION) with cultureagent==$CULTUREAGENT_VERSION"
echo "  qwen:      Qwen Code $QWEN_VERSION expected at ~/.local/lib/qwen-code/bin/qwen or ~/.local/bin/qwen"
echo "  settings:  $QWEN_SETTINGS (cortex at $CORTEX_URL; key from grant:$CORTEX_KEY_SECRET)"
echo "--- qwen.json ---"
render_config
echo "--- unit cultureagent-qwen-bridge.service ---"
echo "  exec: grant run $INJECTS -- cultureagent-qwen-bridge --config $CFG/qwen.json"
echo "  then:      systemctl --user daemon-reload; enable --now cultureagent-qwen-bridge"

if [ "$APPLY" -ne 1 ]; then
  echo
  echo "Dry run: nothing was written. Re-run with --apply to install."
  exit 0
fi

mkdir -p "$CFG" "$(dirname "$VENV")" "$UNIT_DIR" "$(dirname "$QWEN_SETTINGS")"
chmod 700 "$CFG" "$(dirname "$QWEN_SETTINGS")"

[ -x "$UV" ] || die "uv not found (looked on PATH and in $HOME/.local/bin)"
if [ ! -x "$VENV/bin/python" ]; then
  "$UV" venv -q --python "$PYTHON_VERSION" "$VENV"
fi
"$UV" pip install -q --python "$VENV/bin/python" "cultureagent==$CULTUREAGENT_VERSION"

render_config >"$CFG/qwen.json"
chmod 600 "$CFG/qwen.json"
render_qwen_settings >"$QWEN_SETTINGS"
chmod 600 "$QWEN_SETTINGS"
render_unit >"$UNIT_DIR/cultureagent-qwen-bridge.service"

systemctl --user daemon-reload
systemctl --user enable --now cultureagent-qwen-bridge 2>&1 | grep -v '^Created' || true
systemctl --user restart cultureagent-qwen-bridge
"$VENV/bin/cultureagent-qwen-bridge" --config "$CFG/qwen.json" --print-capabilities >/dev/null \
  && echo "qwen bridge: capabilities OK" || echo "qwen bridge: capability check failed (see journalctl --user -u cultureagent-qwen-bridge)" >&2
echo "Installed. Check: curl -H 'Authorization: Bearer ...' http://$HOST:8093/v1/capabilities"
