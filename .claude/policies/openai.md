# OpenAI API Policy

Policy references (calibrated against CI workflow `called-openai-policy.yml`):
- https://openai.com/policies/usage-policies
- https://openai.com/policies/terms-of-use
- https://platform.openai.com/docs/guides/rate-limits

## Detection

Files matching ANY of these patterns trigger this policy:

- `from openai|import openai|openai\.com|OpenAI\(|client\.chat\.completions|client\.completions\.create|OPENAI_API_KEY`

Skip `.env` / `.env.*` files (variable declarations, not logic).

## Checks

### Check 1: API key not hardcoded [error]

**Pattern**: `sk-[a-zA-Z0-9\-_]{20,}`
**Presence**: forbidden
**Also check**: `(api_key|OPENAI_API_KEY)\s*=\s*["'][^"'$\{]{10,}` (string literal assignment)
**Message**: Hardcoded OpenAI API key detected. Store keys in environment variables and access via `os.environ` or `process.env`.
**Reference**: https://platform.openai.com/docs/api-reference/authentication

### Check 2: Rate limit / error handling [error]

**Pattern**: `429|RateLimitError|rate_limit|retry|backoff|sleep|Retry|exponential|tenacity|httpx\.HTTPStatusError`
**Presence**: required
**Message**: No rate-limit or retry handling found. OpenAI enforces token-per-minute limits; unhandled 429s will crash production. Add retry/backoff logic.
**Reference**: https://platform.openai.com/docs/guides/rate-limits

### Check 3: Model version pinned [warning]

**Pattern**: `["'](gpt-4o|gpt-4|gpt-3\.5-turbo|o1|o3|o4-mini|gpt-4-turbo)["']`
**Presence**: forbidden (unversioned aliases)
**Message**: Unversioned model alias detected. OpenAI silently updates these — pin to a dated version (e.g., `gpt-4o-2024-11-20`).
**Reference**: https://platform.openai.com/docs/models

### Check 4: Token limit set [error]

**Pattern**: `max_tokens|max_completion_tokens`
**Presence**: required
**Message**: No token limit found. Without a cap, large responses can exhaust quota and cause cost spikes.
**Reference**: https://platform.openai.com/docs/api-reference/chat/create#chat-create-max_completion_tokens

## Auto-Fix Guidance

- **Model pinning**: Replace unversioned aliases with latest dated version. Check OpenAI docs for current dated versions.
- **Token limit**: If a `client.chat.completions.create(` call lacks `max_tokens` or `max_completion_tokens`, add `max_completion_tokens=4096` as a reasonable default.
- **Rate limiting**: If no retry logic exists, suggest adding exponential backoff. Do NOT auto-insert — flag for human review since retry strategy depends on the application.
- **Hardcoded keys**: NEVER auto-fix by moving the key — flag immediately. The key may already be compromised.
