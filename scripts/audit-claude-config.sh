#!/usr/bin/env bash
# audit-claude-config.sh
#
# Audits Claude Code content (.claude/) across all org target repos against the
# meta-repo FILE_MAP. Identifies:
#   - Files in a target repo that are NOT in the FILE_MAP  →  potential local additions
#   - FILE_MAP entries that are NOT yet present in a target repo  →  sync gaps
#
# Usage:
#   ./scripts/audit-claude-config.sh [--repos repo1,repo2,...] [--json]
#
# Options:
#   --repos   Comma-separated list of repos to audit (default: all 6 target repos)
#   --json    Emit machine-readable JSON instead of the default table output
#
# Requirements:
#   - gh CLI authenticated (gh auth status)
#   - jq installed
#
# Exit code: always 0 (informational — no CI gate)

set -euo pipefail

ORG="wcmchenry3-stack"

# Default target repos — the 6 repos that receive synced content
DEFAULT_REPOS=(
  "gaming_app"
  "office_holder_cursor"
  "book_app"
  "powerplayshistory_site"
  "wcm_portfolio_site"
  "buffingchi_site"
)

# ---------------------------------------------------------------------------
# FILE_MAP — the .claude/ paths that sync-community-health.yml pushes to each
# target repo. Keep this in sync with the FILE_MAP in that workflow.
# ---------------------------------------------------------------------------
SYNCED_CLAUDE_FILES=(
  ".claude/hooks/lint-on-edit.sh"
  ".claude/hooks/lint-gate.sh"
  ".claude/hooks/policy-gate.sh"
  ".claude/agents/plan-issues.md"
  ".claude/agents/lint-review.md"
  ".claude/agents/policy-compliance.md"
  ".claude/policies/policy-patterns.json"
  ".claude/policies/openai.md"
  ".claude/policies/gemini.md"
  ".claude/policies/wikipedia.md"
  ".claude/policies/claude.md"
  ".claude/policies/design-tokens.md"
  ".claude/settings.json"
)

# Files that are intentionally repo-specific and excluded from sync.
# These appear in target repos but are not meta-repo content.
# Update this list if new repo-specific files are documented.
KNOWN_REPO_SPECIFIC=(
  ".claude/settings.local.json"   # local personal overrides — gitignored in meta-repo
)

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
OUTPUT_JSON=false
REPOS=("${DEFAULT_REPOS[@]}")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repos)
      IFS=',' read -ra REPOS <<< "$2"
      shift 2
      ;;
    --json)
      OUTPUT_JSON=true
      shift
      ;;
    -h|--help)
      head -20 "$0" | grep '^#' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Verify dependencies
# ---------------------------------------------------------------------------
if ! command -v gh &>/dev/null; then
  echo "Error: gh CLI not found. Install from https://cli.github.com" >&2
  exit 1
fi
if ! command -v jq &>/dev/null; then
  echo "Error: jq not found. Install via your package manager." >&2
  exit 1
fi
if ! gh auth status &>/dev/null; then
  echo "Error: gh CLI not authenticated. Run: gh auth login" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
is_known_repo_specific() {
  local path="$1"
  for skip in "${KNOWN_REPO_SPECIFIC[@]}"; do
    [[ "$path" == "$skip" ]] && return 0
  done
  return 1
}

in_file_map() {
  local path="$1"
  for entry in "${SYNCED_CLAUDE_FILES[@]}"; do
    [[ "$path" == "$entry" ]] && return 0
  done
  return 1
}

# ---------------------------------------------------------------------------
# JSON accumulator (used when --json is set)
# ---------------------------------------------------------------------------
JSON_OUTPUT="[]"

# ---------------------------------------------------------------------------
# Audit each repo
# ---------------------------------------------------------------------------
for REPO in "${REPOS[@]}"; do
  FULL_REPO="$ORG/$REPO"

  # Fetch the full file tree from the default branch
  TREE=$(gh api "repos/$FULL_REPO/git/trees/HEAD?recursive=1" \
    --jq '[.tree[] | select(.type == "blob") | .path]' 2>/dev/null) || {
    echo "  WARNING: could not fetch tree for $FULL_REPO (repo may not exist or be inaccessible)" >&2
    continue
  }

  # Files in repo under .claude/
  REPO_CLAUDE_FILES=$(echo "$TREE" | jq -r '.[] | select(startswith(".claude/"))')

  # 1. Files in repo NOT in FILE_MAP (potential local additions worth upstreaming)
  LOCAL_ADDITIONS=()
  while IFS= read -r path; do
    path="${path%$'\r'}"   # strip Windows CRLF artifacts
    [[ -z "$path" ]] && continue
    if ! in_file_map "$path" && ! is_known_repo_specific "$path"; then
      LOCAL_ADDITIONS+=("$path")
    fi
  done <<< "$REPO_CLAUDE_FILES"

  # 2. FILE_MAP entries NOT in repo (sync gaps)
  SYNC_GAPS=()
  for entry in "${SYNCED_CLAUDE_FILES[@]}"; do
    if ! echo "$TREE" | jq -e --arg p "$entry" '.[] | select(. == $p)' &>/dev/null; then
      SYNC_GAPS+=("$entry")
    fi
  done

  if $OUTPUT_JSON; then
    # Build JSON entry for this repo
    local_json=$(printf '%s\n' "${LOCAL_ADDITIONS[@]+"${LOCAL_ADDITIONS[@]}"}" | jq -R . | jq -s .)
    gap_json=$(printf '%s\n' "${SYNC_GAPS[@]+"${SYNC_GAPS[@]}"}" | jq -R . | jq -s .)
    entry=$(jq -n \
      --arg repo "$REPO" \
      --argjson local "$local_json" \
      --argjson gaps "$gap_json" \
      '{repo: $repo, local_additions: $local, sync_gaps: $gaps}')
    JSON_OUTPUT=$(echo "$JSON_OUTPUT" | jq --argjson e "$entry" '. + [$e]')
  else
    # Human-readable table
    echo ""
    echo "┌─────────────────────────────────────────────────────"
    echo "│ Repo: $REPO"
    echo "├─────────────────────────────────────────────────────"

    if [[ ${#LOCAL_ADDITIONS[@]} -eq 0 ]]; then
      echo "│   Local additions (not in FILE_MAP): none"
    else
      echo "│   Local additions (not in FILE_MAP):"
      for f in "${LOCAL_ADDITIONS[@]}"; do
        echo "│     + $f"
      done
    fi

    echo "│"

    if [[ ${#SYNC_GAPS[@]} -eq 0 ]]; then
      echo "│   Sync gaps (FILE_MAP entry missing from repo): none"
    else
      echo "│   Sync gaps (FILE_MAP entry missing from repo):"
      for f in "${SYNC_GAPS[@]}"; do
        echo "│     - $f"
      done
    fi

    echo "└─────────────────────────────────────────────────────"
  fi
done

if $OUTPUT_JSON; then
  echo "$JSON_OUTPUT" | jq .
fi

echo ""
echo "Audit complete. Exit 0 — informational only, no CI gate."
