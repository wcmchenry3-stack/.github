# Claude / Anthropic API Policy

Policy references:

- https://www.anthropic.com/policies/aup
- https://docs.anthropic.com/en/docs/about-claude/models
- https://docs.anthropic.com/en/api/rate-limits
- https://docs.anthropic.com/en/api/messages

## Detection

Files matching ANY of these patterns trigger this policy:

- `anthropic|@anthropic-ai/sdk|from anthropic|import anthropic|Anthropic\(|ANTHROPIC_API_KEY|claude-|claude_agent_sdk`

Skip `.env` / `.env.*` files (variable declarations, not logic).

## Checks

### Check 1: API key not hardcoded [error]

**Pattern**: `(ANTHROPIC_API_KEY|api_key)\s*=\s*["']sk-ant-[^"']{20,}["']`
**Also check**: `sk-ant-[a-zA-Z0-9\-_]{20,}` (literal key pattern)
**Presence**: forbidden
**Message**: Hardcoded Anthropic API key detected. Store keys in environment variables and access via `os.environ` or `process.env`.
**Reference**: https://docs.anthropic.com/en/api/getting-started

### Check 2: Rate limit / error handling [error]

**Pattern**: `429|RateLimitError|rate_limit|retry|backoff|sleep|Retry|exponential|tenacity|overloaded_error`
**Presence**: required
**Message**: No rate-limit or retry handling found. Anthropic enforces request-per-minute limits; unhandled 429s or overloaded_error responses will crash production. Add retry/backoff logic.
**Reference**: https://docs.anthropic.com/en/api/rate-limits

### Check 3: Model version pinned [warning]

**Pattern**: `["'](claude-opus-4|claude-sonnet-4|claude-haiku-4|claude-3-5-sonnet|claude-3-5-haiku|claude-3-opus|claude-3-sonnet|claude-3-haiku)["']`
**Presence**: forbidden (prefer dated versions like `claude-sonnet-4-20250514`)
**Message**: Unversioned Claude model alias detected. Anthropic may update these — pin to a dated version (e.g., `claude-sonnet-4-20250514`) to prevent unexpected behavior changes.
**Reference**: https://docs.anthropic.com/en/docs/about-claude/models

### Check 4: Max tokens set [error]

**Pattern**: `max_tokens`
**Presence**: required
**Message**: No max_tokens parameter found. The Anthropic API requires max_tokens on every request. Without it, the request will fail or produce unexpected results.
**Reference**: https://docs.anthropic.com/en/api/messages

## Auto-Fix Guidance

- **Model pinning**: Replace unversioned aliases with the latest dated version. Current recommendations: `claude-opus-4-20250514`, `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`.
- **Max tokens**: If a `client.messages.create(` or `anthropic.messages.create(` call lacks `max_tokens`, add `max_tokens=4096` as a reasonable default.
- **Rate limiting**: Flag for human review — retry strategy depends on the application. Suggest exponential backoff with `overloaded_error` and `429` handling.
- **Hardcoded keys**: NEVER auto-fix by moving the key — flag immediately. The key may be compromised and should be rotated.
