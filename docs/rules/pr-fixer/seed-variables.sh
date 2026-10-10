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
# environment (API URL, service token) applies.
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

set_var trusted_authors '["OriNachum", "qodo-code-review[bot]"]' \
  "GitHub logins whose PR comments and reviews fire the fixer; only their threads are handed to the agent"
set_var ignored_check_apps '["claude"]' \
  "Check-suite app slugs the settle step ignores (the claude suite stays queued)"
set_var checks_settle_timeout_s '900' \
  "Seconds after a head SHA's first check completion before it settles anyway (settled_by timeout)"
set_var checks_settle_min_s '60' \
  "Minimum seconds before all_completed settles, so a slower app's suite can appear"
set_var fixer_repos '["agentculture/culture-rules-tester"]' \
  "Repositories (owner/name) the fixer runs on (the allow-list); widen with: variables add fixer_repos owner/repo"
set_var fixer_excluded_repos '[]' \
  "Repositories (owner/name) the fixer never runs on, even when in fixer_repos (an override)"
set_var fixer_comment_triggers '["/fix", "@rules-culture-dev"]' \
  "What a PR comment or review comment must carry to start a fixer run: a /command at the start of its body, or an @mention of the App (narrow it to [\"/fix\"] to ignore mentions)"
set_var fixer_stop_triggers '["/stop", "@rules-culture-dev stop"]' \
  "What a PR comment must start with to stop the PR's fixer story (d34): a /command, or the App's @mention followed by the word (narrow it to [\"/stop\"] to ignore mentions)"
set_var fixer_protected_paths \
  '[".github/workflows/**", "sonar-project.properties", ".coveragerc", "setup.cfg", ".flake8", "pyproject.toml"]' \
  "Paths the gate's diff guard refuses (on top of the .github/workflows/** floor)"
