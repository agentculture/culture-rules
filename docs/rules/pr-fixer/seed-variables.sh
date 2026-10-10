#!/usr/bin/env bash
# Seed the shared variables the PR fixer reads (docs/operations/pr-fixer.md, section 7).
#
#   bash docs/rules/pr-fixer/seed-variables.sh            # dry run: shows each write
#   bash docs/rules/pr-fixer/seed-variables.sh --apply    # writes (admin role)
#   bash docs/rules/pr-fixer/seed-variables.sh --apply --force   # also re-set existing ones
#
# A variable that already exists is left alone unless --force is given, so re-running the
# script never overwrites values edited later in the Variables tab. Every write appends a
# version naming you. Run it before importing the rules: a rule that references an
# undefined variable is refused at import.
#
# CULTURE_RULES overrides the CLI (default: culture-rules on PATH); the CLI's own
# environment (API URL, service token) applies. The values live in variables.json beside it.
set -euo pipefail

CR="${CULTURE_RULES:-culture-rules}"
APPLY=()
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=(--apply) ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 1 ;;
  esac
done

EXISTING="$("$CR" variables list --json \
  | python3 -c 'import json, sys; print("\n".join(i["name"] for i in json.load(sys.stdin)["items"]))')"

set_var() {
  local name="$1" value="$2" description="$3"
  if [[ "$FORCE" -eq 0 ]] && grep -qxF "$name" <<<"$EXISTING"; then
    echo "skip $name: already set (use --force to replace it)"
    return 0
  fi
  "$CR" variables set "$name" --value "$value" --description "$description" "${APPLY[@]}"
}

# One line per variable: name, JSON value, description, tab-separated (read in file order).
MANIFEST="$(dirname "$0")/variables.json"
SEEDS="$(python3 - "$MANIFEST" <<'PY'
import json, sys
for item in json.load(open(sys.argv[1], encoding="utf-8")):
    fields = (item["name"], json.dumps(item["value"]), item["description"])
    if any("\t" in f or "\n" in f for f in fields):
        sys.exit(f"{item['name']}: a tab or newline in a field")
    print("\t".join(fields))
PY
)"

while IFS=$'\t' read -r name value description; do
  [[ -n "$name" ]] || continue  # an empty manifest
  set_var "$name" "$value" "$description"
done <<<"$SEEDS"
