#!/usr/bin/env bash
# lint-on-edit.sh — PostToolUse hook for Claude Code
# Immediately lints the file that was just written or edited.
# Surfaces issues to Claude before they accumulate and block PR creation.
#
# Triggered by: PostToolUse on Write, Edit
# Exit 0 = clean / linter not present (no noise)
# Exit 1 = lint errors found (Claude sees stderr and fixes them)

set -uo pipefail

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# Nothing to lint
[ -z "$FILE" ] || [ ! -f "$FILE" ] && exit 0

EXT="${FILE##*.}"
REPORT=""
FAIL=0

# ── JavaScript / TypeScript ───────────────────────────────────────────────
case "$EXT" in
  js|jsx|ts|tsx|mjs|cjs)
    if [ -x "node_modules/.bin/eslint" ]; then
      OUT=$(npx eslint "$FILE" 2>&1) || {
        FAIL=1
        REPORT+="ESLint errors in $FILE:\n$OUT\n\n"
      }
    fi
    if [ -x "node_modules/.bin/prettier" ]; then
      OUT=$(npx prettier --check "$FILE" 2>&1) || {
        FAIL=1
        REPORT+="Prettier formatting in $FILE:\n$OUT\n\n"
      }
    fi
    ;;

# ── Python ────────────────────────────────────────────────────────────────
  py)
    if command -v ruff &>/dev/null; then
      OUT=$(ruff check --no-fix "$FILE" 2>&1) || {
        FAIL=1
        REPORT+="ruff errors in $FILE:\n$OUT\n\n"
      }
    fi
    if command -v black &>/dev/null; then
      OUT=$(black --check --quiet "$FILE" 2>&1) || {
        FAIL=1
        REPORT+="black formatting in $FILE:\n$OUT\n\n"
      }
    fi
    ;;

# ── Shell scripts ─────────────────────────────────────────────────────────
  sh|bash)
    if command -v shellcheck &>/dev/null; then
      OUT=$(shellcheck "$FILE" 2>&1) || {
        FAIL=1
        REPORT+="shellcheck issues in $FILE:\n$OUT\n\n"
      }
    fi
    ;;

# ── YAML ──────────────────────────────────────────────────────────────────
  yml|yaml)
    # Always validate syntax — Python stdlib, no deps
    OUT=$(python3 -c "
import yaml, sys
try:
    yaml.safe_load(open(sys.argv[1]))
except yaml.YAMLError as e:
    print(e, file=sys.stderr)
    sys.exit(1)
" "$FILE" 2>&1) || {
      FAIL=1
      REPORT+="YAML syntax error in $FILE:\n$OUT\n\n"
    }
    ;;

# ── JSON ──────────────────────────────────────────────────────────────────
  json)
    OUT=$(python3 -c "
import json, sys
try:
    json.load(open(sys.argv[1]))
except json.JSONDecodeError as e:
    print(e, file=sys.stderr)
    sys.exit(1)
" "$FILE" 2>&1) || {
      FAIL=1
      REPORT+="JSON syntax error in $FILE:\n$OUT\n\n"
    }
    ;;
esac

# ── Verdict ───────────────────────────────────────────────────────────────
if [ "$FAIL" -ne 0 ]; then
  printf 'Lint issue(s) found — fix before moving on:\n\n%b' "$REPORT" >&2
  exit 1
fi

exit 0
