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
# Use merge-base against the PR target branch (dev, then main) so we
# only see files actually changed on this branch, not everything that
# dev has over main.
MERGE_BASE=$(git merge-base origin/dev HEAD 2>/dev/null || \
             git merge-base origin/main HEAD 2>/dev/null || \
             echo "")
if [ -n "$MERGE_BASE" ]; then
  CHANGED_FILES=$(git diff --name-only "$MERGE_BASE" HEAD 2>/dev/null || echo "")
else
  CHANGED_FILES=$(git diff --name-only HEAD~1 HEAD 2>/dev/null || echo "")
fi

if [ -z "$CHANGED_FILES" ]; then
  exit 0
fi

# ── Check each policy's detection patterns ───────────────────────
TRIGGERED=""

for POLICY in $(jq -r 'to_entries[] | select(.value | type == "object") | .key' "$PATTERNS_FILE" | tr -d '\r'); do
  DETECT=$(jq -r --arg p "$POLICY" '.[$p].detect' "$PATTERNS_FILE" | tr -d '\r')
  SKIP=$(jq -r --arg p "$POLICY" '.[$p].skip // empty' "$PATTERNS_FILE" | tr -d '\r')

  # Guard: skip policy if detect pattern resolved to null or empty
  # (prevents any file with the literal string "null" from being flagged)
  [ -z "$DETECT" ] || [ "$DETECT" = "null" ] && continue

  while IFS= read -r file; do
    # Skip .claude/ directory (contains policy definitions with pattern strings)
    case "$file" in .claude/*) continue ;; esac
    # Skip documentation files — they reference APIs by name but contain no executable code
    case "$file" in *.md|*.mdx|CLAUDE.md) continue ;; esac

    # Skip files matching the skip pattern (checked against full path and basename
    # so patterns can exclude by directory prefix OR by filename)
    if [ -n "$SKIP" ] && { echo "$file" | grep -qE "$SKIP" || echo "$(basename "$file")" | grep -qE "$SKIP"; }; then
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
