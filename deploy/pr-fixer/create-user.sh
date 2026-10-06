#!/usr/bin/env bash
# Create the unprivileged PR-fixer account on THIS host (the operator runs it with sudo).
#
# Dry-run by default: prints the plan and changes nothing. Pass --apply to commit it.
# See docs/operations/pr-fixer.md. Idempotent: an existing account is left as it is.
#
# The account gets its own home (mode 750) and its own group, no sudo and no membership
# in the operator's groups, so it cannot read the operator's home (mode 750 or tighter),
# the engine node's node.env, the App private key or the operator's gh/claude logins.
set -euo pipefail

FIXER_USER="culture-fixer"
AUTH_KEY=""
APPLY=0

usage() {
  cat <<'USAGE'
Usage: sudo create-user.sh [--user NAME] [--authorize-key FILE] [--apply]

Creates the unprivileged account the cultureagent bridges run as, and enables
lingering so its systemd --user units start at boot. Dry-run unless --apply.

Options:
  --user NAME   account name (default: culture-fixer)
  --authorize-key FILE
                one SSH public key allowed to log in as the account (for whoever
                runs install.sh there); the account gets no other login
  --apply       perform the change (needs root)
  -h, --help    show this help
USAGE
}

die() {
  echo "create-user.sh: $*" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --user) FIXER_USER="${2:?--user needs a value}"; shift 2 ;;
    --authorize-key) AUTH_KEY="${2:?--authorize-key needs a value}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

case "$FIXER_USER" in
  "" | *[!a-z0-9_-]*) die "user name may contain only lower-case letters, digits, - and _" ;;
esac

OPERATOR="${SUDO_USER:-$USER}"
if [ -n "$AUTH_KEY" ]; then
  [ -f "$AUTH_KEY" ] || die "public key not found: $AUTH_KEY"
  [ "$(grep -c . "$AUTH_KEY")" -eq 1 ] || die "--authorize-key must hold exactly one public key"
  KEY_TYPE=$(grep -oE '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-[a-z0-9-]+|sk-[a-z0-9-]+@openssh\.com|[a-z0-9-]+-cert-v01@openssh\.com) [A-Za-z0-9+/=]+' "$AUTH_KEY" | cut -d' ' -f1) \
    || die "--authorize-key is not an SSH public key: $AUTH_KEY"
fi

echo "Plan for the PR-fixer account '$FIXER_USER':"
echo "  useradd --create-home --user-group --shell /bin/bash $FIXER_USER   (skipped if it exists)"
echo "  chmod 750 /home/$FIXER_USER"
echo "  loginctl enable-linger $FIXER_USER"
[ -z "$AUTH_KEY" ] || echo "  authorize the $KEY_TYPE key in $AUTH_KEY as /home/$FIXER_USER/.ssh/authorized_keys (mode 600, replacing it)"
echo "  check: the operator's home (/home/$OPERATOR) is not readable by others"

if [ "$APPLY" -ne 1 ]; then
  echo
  echo "Dry run: nothing was changed. Re-run with sudo and --apply."
  exit 0
fi

[ "$(id -u)" -eq 0 ] || die "--apply needs root (run it with sudo)"

if id "$FIXER_USER" >/dev/null 2>&1; then
  echo "account $FIXER_USER exists; leaving it as it is"
else
  useradd --create-home --user-group --shell /bin/bash "$FIXER_USER"
fi
chmod 750 "/home/$FIXER_USER"
loginctl enable-linger "$FIXER_USER"
if [ -n "$AUTH_KEY" ]; then
  install -d -m 700 -o "$FIXER_USER" -g "$FIXER_USER" "/home/$FIXER_USER/.ssh"
  install -m 600 -o "$FIXER_USER" -g "$FIXER_USER" "$AUTH_KEY" "/home/$FIXER_USER/.ssh/authorized_keys"
fi

if [ -d "/home/$OPERATOR" ]; then
  mode=$(stat -c %a "/home/$OPERATOR")
  case "$mode" in
    *[1-7]) echo "WARNING: /home/$OPERATOR is mode $mode (others can enter it); chmod o-rwx it" >&2 ;;
  esac
fi
if id -nG "$FIXER_USER" | tr ' ' '\n' | grep -qxE "sudo|adm|docker|$OPERATOR"; then
  die "$FIXER_USER is in a privileged or operator group; remove it before going on"
fi
echo "Created. Next, as $FIXER_USER: deploy/pr-fixer/install.sh (see docs/operations/pr-fixer.md)."
