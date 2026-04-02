# Google Gemini API Policy

Policy references:
- https://ai.google.dev/gemini-api/terms
- https://ai.google.dev/gemini-api/docs/api-key
- https://ai.google.dev/gemini-api/docs/safety-settings
- https://ai.google.dev/gemini-api/docs/models/gemini

## Detection

Files matching ANY of these patterns trigger this policy:

- `google\.generativeai|from google.*generativeai|genai\.|GenerativeModel|GEMINI_API_KEY|GOOGLE_AI_API_KEY`

Skip `.env` / `.env.*` files (variable declarations, not logic).

## Checks

### Check 1: API key not hardcoded [error]

**Pattern**: `(GEMINI_API_KEY|GOOGLE_AI_API_KEY|api_key)\s*=\s*["'][A-Za-z0-9\-_]{20,}["']`
**Also check**: `genai\.configure\(\s*api_key\s*=\s*["'][^"'$\{]{10,}`
**Presence**: forbidden
**Message**: Hardcoded Gemini API key detected. Store keys in environment variables and access via `os.environ`.
**Reference**: https://ai.google.dev/gemini-api/docs/api-key

### Check 2: Rate limit / error handling [error]

**Pattern**: `retry|backoff|sleep|ResourceExhausted|429|quota|tenacity|Retry|exponential`
**Presence**: required
**Message**: No rate-limit or error handling found. Gemini API enforces quota limits; unhandled ResourceExhausted errors will crash production. Add retry/backoff logic.
**Reference**: https://ai.google.dev/gemini-api/docs/troubleshooting

### Check 3: Model version pinned [warning]

**Pattern**: `["'](gemini-pro|gemini-1\.5-pro|gemini-1\.5-flash|gemini-2\.0-flash|gemini-2\.5-pro|gemini-2\.5-flash)["']`
**Presence**: forbidden (prefer versioned model names with date suffixes)
**Message**: Unversioned Gemini model alias detected. Google may update these — pin to a specific version when available.
**Reference**: https://ai.google.dev/gemini-api/docs/models/gemini

### Check 4: Safety settings configured [warning]

**Pattern**: `safety_settings|HarmCategory|HarmBlockThreshold|SafetySetting`
**Presence**: required (warning only)
**Message**: No safety settings configured. Gemini API provides configurable safety filters — review defaults and set explicit thresholds appropriate for your use case.
**Reference**: https://ai.google.dev/gemini-api/docs/safety-settings

## Auto-Fix Guidance

- **Model pinning**: Replace unversioned aliases with the latest versioned name if known. Flag for review if uncertain.
- **Safety settings**: If a `GenerativeModel(` or `genai.GenerativeModel(` call lacks `safety_settings`, suggest adding explicit settings. Do NOT auto-insert — safety thresholds require human judgment.
- **Rate limiting**: Flag for human review — retry strategy depends on application needs.
- **Hardcoded keys**: NEVER auto-fix by moving the key — flag immediately. The key may be compromised.
