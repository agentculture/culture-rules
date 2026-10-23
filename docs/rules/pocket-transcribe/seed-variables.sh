#!/usr/bin/env bash
# Seed the shared variable the pocket-transcribe rule reads (docs/operations/pocket-transcribe.md).
#
#   bash docs/rules/pocket-transcribe/seed-variables.sh            # dry run: shows each write
#   bash docs/rules/pocket-transcribe/seed-variables.sh --apply    # writes (admin role)
#   bash docs/rules/pocket-transcribe/seed-variables.sh --apply --force   # also re-set it
#
# The value and description come from variables.json next to this script. A string value
# that starts with $HOME/ is expanded to this user's home ($HOME) at seed time, so run it as the
# user the engine node runs as: transcripts_dir becomes an absolute path such as
# /home/<user>/.local/share/culture-rules/transcripts, which must lie inside the STT actor's
# output_root. A variable that already exists is left alone unless --force is given. Run it
# before importing the rule: a rule that references an undefined variable is refused at import.
#
# CULTURE_RULES overrides the CLI (default: culture-rules on PATH); the CLI's own
# environment (API URL, service token) applies.
set -euo pipefail

CR="${CULTURE_RULES:-culture-rules}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPLY=()
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=(--apply) ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 1 ;;
  esac
done

EXISTING="$("$CR" variables list --json \
  | python3 -c 'import json, sys; print("\n".join(i["name"] for i in json.load(sys.stdin)["items"]))')"

# One line per variable: name, the value as JSON (a leading $HOME/ expanded), the description
# (tab-separated; JSON never holds a raw tab).
ENTRIES="$(python3 -c '
import json, os, sys
for name, item in json.load(open(sys.argv[1])).items():
    value = item["value"]
    if isinstance(value, str) and value.startswith("$HOME/"):
        value = os.path.join(os.environ["HOME"], value[len("$HOME/"):])
    print(name, json.dumps(value), item["description"], sep="\t")
' "$HERE/variables.json")"

while IFS=$'\t' read -r name value description; do
  if [[ "$FORCE" -eq 0 ]] && grep -qxF "$name" <<<"$EXISTING"; then
    echo "skip $name: already set (use --force to replace it)"
    continue
  fi
  "$CR" variables set "$name" --value "$value" --description "$description" "${APPLY[@]}"
done <<<"$ENTRIES"
