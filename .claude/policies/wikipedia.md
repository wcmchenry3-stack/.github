# Wikipedia / Wikimedia API Policy

Policy references (calibrated against CI workflow `called-wikipedia-policy.yml`):

- https://www.mediawiki.org/wiki/API:Etiquette
- https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use
- https://www.mediawiki.org/wiki/API:REST_API

## Detection

Files matching ANY of these patterns trigger this policy:

- `wikipedia\.org|mediawiki|wikimedia`

## Checks

### Check 1: User-Agent header present [error]

**Pattern**: `User-Agent|user_agent|userAgent`
**Presence**: required
**Message**: Missing User-Agent header. Wikimedia policy requires every API request to include a descriptive User-Agent (app name + contact).
**Reference**: https://www.mediawiki.org/wiki/API:Etiquette#The_User-Agent_header

### Check 2: Rate limiting present [error]

**Pattern**: `sleep|throttle|ratelimit|rate_limit|RateLimit|backoff|retry|Retry|setTimeout|asyncio\.sleep`
**Presence**: required
**Message**: No rate-limiting code found. Wikimedia requires respectful request pacing (e.g., sleep, backoff, retry logic).
**Reference**: https://www.mediawiki.org/wiki/API:Etiquette#Request_limit

### Check 3: Attribution [warning]

**Pattern**: `CC BY-SA|attribution|wikimedia|Creative Commons|licensed under`
**Presence**: required (warning only — attribution often lives in UI templates)
**Message**: No attribution found in this file. Wikipedia content is CC BY-SA licensed. Verify attribution exists in UI/templates.
**Reference**: https://creativecommons.org/licenses/by-sa/4.0/

### Check 4: No raw HTML scraping [error]

**Pattern (forbidden)**: `BeautifulSoup|lxml\.html|from scrapy|import scrapy|html\.parser`
**Also check**: `requests\.(get|post)\s*\([^)]*wikipedia\.org/wiki/` and `fetch\(['"]https?://[a-z]+\.wikipedia\.org/wiki/`
**Presence**: forbidden
**Message**: HTML-scraping library or direct wiki URL request detected. Use the official Wikimedia REST API (`/w/api.php` or `api.wikimedia.org`) instead.
**Reference**: https://www.mediawiki.org/wiki/API:Main_page

## Auto-Fix Guidance

- **User-Agent**: If a `requests.get` or `requests.Session` call targets wikipedia.org without a User-Agent, the agent can add a `headers={"User-Agent": "app-name/1.0 (contact@example.com)"}` parameter. Use the repo name as the app name placeholder.
- **Rate limiting**: Flag for human review — retry strategy depends on the application's request pattern.
- **Attribution**: Flag for human review — attribution belongs in UI, not necessarily in the API module.
- **HTML scraping**: Flag for human review — requires refactoring to use the API instead.
