#!/usr/bin/env bash
# policy-gate.sh — PreToolUse hook for Claude Code
# Blocks `gh pr create` when changed files match API policy detection
# patterns, prompting the policy-compliance sub-agent to review.
set -uo pipefail

# ── Read tool input from stdin ───────────────────────────────────
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)

# Only gate on PR-creation commands
if ! echo "$COMMAND" | grep -qE 'gh\s+pr\s+create'; then
  exit 0
fi

# ── Locate policy-patterns.json ──────────────────────────────────
PATTERNS_FILE=".claude/policies/policy-patterns.json"
if [ ! -f "$PATTERNS_FILE" ]; then
  # No policies configured — pass through
  exit 0
fi

# ── Get changed files ────────────────────────────────────────────
# Try PR context first (origin/main...HEAD), fall back to HEAD~1
CHANGED_FILES=$(git diff --name-only origin/main...HEAD 2>/dev/null || \
                git diff --name-only HEAD~1 HEAD 2>/dev/null || \
                echo "")

if [ -z "$CHANGED_FILES" ]; then
  exit 0
fi

# ── Check each policy's detection patterns ───────────────────────
TRIGGERED=""

for POLICY in $(jq -r 'keys[]' "$PATTERNS_FILE"); do
  DETECT=$(jq -r --arg p "$POLICY" '.[$p].detect' "$PATTERNS_FILE")
  SKIP=$(jq -r --arg p "$POLICY" '.[$p].skip // empty' "$PATTERNS_FILE")

  while IFS= read -r file; do
    # Skip .claude/ directory (contains policy definitions with pattern strings)
    case "$file" in .claude/*) continue ;; esac

    # Skip files matching the skip pattern
    if [ -n "$SKIP" ] && echo "$(basename "$file")" | grep -qE "$SKIP"; then
      continue
    fi

    # Check if file exists and matches detection pattern
    if [ -f "$file" ] && grep -qE "$DETECT" "$file" 2>/dev/null; then
      TRIGGERED+="  - $POLICY → $file\n"
      break  # One match per policy is enough to trigger
    fi
  done <<< "$CHANGED_FILES"
done

# ── Verdict ──────────────────────────────────────────────────────
if [ -n "$TRIGGERED" ]; then
  {
    echo "BLOCKED: Policy-relevant files changed. Invoke the policy-compliance agent to review."
    echo ""
    echo "Triggered policies:"
    echo -e "$TRIGGERED"
  } >&2
  exit 2
fi

exit 0
