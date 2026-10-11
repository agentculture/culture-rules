#!/usr/bin/env bash
# Seed the shared variables the daily newspaper reads (docs/operations/newspaper.md).
#
#   bash docs/rules/newspaper/seed-variables.sh            # dry run: shows each write
#   bash docs/rules/newspaper/seed-variables.sh --apply    # writes (admin role)
#   bash docs/rules/newspaper/seed-variables.sh --apply --force   # also re-set existing ones
#
# The values and descriptions come from variables.json next to this script: an example
# sections list, no recipient (newspaper_to = []) and no device (newspaper_device = null).
# Set the real recipient and device afterwards in the Variables tab or with
# `culture-rules variables set`. A variable that already exists is left alone unless
# --force is given. Run it before importing the rule: a rule that references an undefined
# variable is refused at import.
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

# One line per variable: name, the value as JSON, the description (tab-separated; JSON
# never holds a raw tab).
ENTRIES="$(python3 -c '
import json, sys
for name, item in json.load(open(sys.argv[1])).items():
    print(name, json.dumps(item["value"]), item["description"], sep="\t")
' "$HERE/variables.json")"

while IFS=$'\t' read -r name value description; do
  if [[ "$FORCE" -eq 0 ]] && grep -qxF "$name" <<<"$EXISTING"; then
    echo "skip $name: already set (use --force to replace it)"
    continue
  fi
  "$CR" variables set "$name" --value "$value" --description "$description" "${APPLY[@]}"
done <<<"$ENTRIES"
