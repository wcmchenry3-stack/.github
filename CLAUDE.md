# wcmchenry3-stack/.github

Shared GitHub Actions workflows, community health files, and Claude Code
configuration for the **wcmchenry3-stack** organization.

## Repo purpose

This is a meta-repository. It does not contain application code. It provides:
- Reusable CI/CD workflows (`.github/workflows/called-*.yml`)
- Org-wide PR/issue templates and security policy
- Shared Claude Code agents, hooks, and settings (`.claude/`)

## Claude Code — lint-review agent

A `PreToolUse` hook gates every `gh pr create` call behind a lint check.

**Flow:**
1. You run `gh pr create` (or the user asks you to submit a PR).
2. The `lint-gate.sh` hook runs linters in check mode.
3. If linting passes — the PR is created normally.
4. If linting fails — the hook blocks with exit 2 and prints the errors.
5. **You must then invoke the `lint-review` agent** (`Agent` tool, prompt:
   "Run the lint-review agent to auto-fix lint issues in this repo.") to
   auto-fix the problems.
6. After the agent finishes, stage and commit the fixes, then retry PR creation.

## Claude Code — policy-compliance agent

A `PreToolUse` hook gates every `gh pr create` call behind a policy check.

**Flow:**
1. You run `gh pr create` (or the user asks you to submit a PR).
2. The `policy-gate.sh` hook checks changed files against detection patterns
   in `.claude/policies/policy-patterns.json`.
3. If no policy-relevant files changed — the PR proceeds normally.
4. If policy-relevant files are detected — the hook blocks with exit 2 and
   lists which policies were triggered.
5. **You must then invoke the `policy-compliance` agent** (`Agent` tool,
   prompt: "Run the policy-compliance agent to review policy violations.")
   to check compliance and auto-fix what it can.
6. After the agent finishes, stage and commit fixes, then retry PR creation.

**Adding a new policy:**
1. Create `.claude/policies/{name}.md` following the existing format.
2. Add the detection pattern to `.claude/policies/policy-patterns.json`.
3. No hook or agent changes needed.

**Current policies:** OpenAI, Wikipedia/Wikimedia, Google Gemini, Design Tokens & Accessibility.

## Design Tokens & Accessibility policy (`called-design-token-check.yml`)

`called-design-token-check.yml` validates that frontend code does not contain:
- Hard-coded CSS color values (hex, `rgb()`, `rgba()`, `hsl()`, `hsla()`) — use design tokens
- Hard-coded `font-size` in `px` — use tokens or relative units (`rem`/`em`)
- Hard-coded `font-family` string literals — use tokens
- `tabindex` > 0 — breaks natural DOM tab order (WCAG 2.4.3)
- Event handlers (`onclick`, `onkeydown`, etc.) on non-interactive elements (WCAG 4.1.2)
- `<img>` without `alt` attribute (WCAG 1.1.1)
- `aria-hidden="true"` on focusable elements (WCAG 4.1.2)

**Skip patterns** (files excluded from checking):
`node_modules/`, `dist/`, `build/`, `.cache/`, `coverage/`, `public/`, `tokens/`,
`*.min.css`, `*.min.js`, `*.stories.*`, `*.test.*`, `*.spec.*`, `__snapshots__/`,
`*.svg`, `*.md`, `*.mdx`, `*.tokens.json`, `tailwind.config.*`, `theme.*.ts/js`

**Enforcement:** defaults to `advisory` in `.github/policy-enforcement.yml`.
Flip a repo to `required` under `overrides:` once its frontend is using a design system.

**Policy definition:** `.claude/policies/design-tokens.md` — update alongside the workflow.

## Org-level policy enforcement (`policy-enforcement.yml`)

`called-openai-policy.yml`, `called-gemini-policy.yml`, and
`called-design-token-check.yml` read `.github/policy-enforcement.yml` from this
meta-repo at job start to decide whether violations **fail the check** or are
**advisory only**.

**The model:**
- Individual repos *inherit* the policy scaffolding (agents, hooks, `.claude/policies/`).
- The org (this repo) *controls* whether findings block PRs.

Defaults are `advisory` — violations are annotated in the PR UI but do not
fail the check. Flip a repo's key to `required` in
`overrides:` once it actually starts calling the API in question.

Wikipedia uses a stricter model (`allowed-repo:` input gate) and is not
listed in `policy-enforcement.yml`.

## Policy change monitors

Two mechanisms keep org workflows calibrated against upstream policy changes:

### Wikipedia (`scheduled-wikipedia-policy-monitor.yml`)
- Runs monthly via cron. Calls the MediaWiki API to fetch current revision IDs
  for `API:Etiquette`, `API:REST_API`, and `Policy:Terms_of_Use`.
- Compares against baseline in `.github/wikipedia-policy-revisions.json`.
- On change: opens a GitHub issue with direct diff links, commits updated baseline.
- No external credentials — uses `GITHUB_TOKEN` (automatic).

### OpenAI RSS (`scheduled-openai-news-monitor.yml`)
- Runs weekly (Monday 09:00 UTC). Fetches `https://openai.com/news/rss.xml` and
  filters new items for policy-relevant keywords (policy, terms, guidelines, etc.).
- Tracks seen item GUIDs in `.github/openai-news-baseline.json` to avoid duplicates.
- First run seeds the baseline without opening issues; subsequent runs alert on new items.
- On match: opens a GitHub issue tagged `policy-update` with a link to the article.
- No external credentials — uses `GITHUB_TOKEN` (automatic).

### OpenAI email fallback (`cloudflare/openai-policy-worker/`)
- Cloudflare Email Worker deployed to `wcmchenry3.workers.dev`.
- Receives forwarded OpenAI newsletter emails via `openai-policy@buffingchi.com`
  (Email Routing configured on buffingchi.com).
- Opens a GitHub issue tagged `policy-update` for each received email.
- Requires secret: `GITHUB_TOKEN` (fine-grained PAT, `issues: write` on this repo)
  — already set via `wrangler secret put GITHUB_TOKEN`.

## Cloudflare Workers

### In-app feedback worker (`cloudflare/feedback-worker/`)
HTTP Worker deployed to `wcmchenry3.workers.dev`. Receives JSON feedback submissions
from app UI widgets, validates and enriches them via the Claude API, and opens a GitHub
issue in the originating repo.

**Endpoint:** `POST /feedback`

**Request body:**
```json
{
  "appId": "gaming_app",        // required — must be in APP_REPO_MAP
  "title": "string",            // required, max 255 chars
  "description": "string",      // required, max 10 000 chars
  "type": "bug" | "feature",    // required
  "screenshotBase64": "string", // optional, max ~2 MB base64
  "sessionLogs": "string"       // optional, max 50 000 chars
}
```

**Response (201):** `{ "issueNumber": 42, "issueUrl": "https://github.com/..." }`

**Secrets (set via `wrangler secret put <NAME>`):**

| Secret | Description |
|--------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key — uses `claude-haiku-4-5-20251001` |
| `GITHUB_TOKEN` | Fine-grained PAT; `issues:write` on `gaming_app` + `book_app`, `gists:write` on token owner account |
| `ALLOWED_ORIGINS` | Comma-separated allowed app origins, e.g. `https://gaming.wcmchenry3.com,https://book.wcmchenry3.com` |
| `APP_REPO_MAP` | Comma-separated `appId:org/repo` pairs, e.g. `gaming_app:wcmchenry3-stack/gaming_app,book_app:wcmchenry3-stack/book_app` |

**KV namespace (set in `wrangler.toml` — replace placeholder IDs):**
```bash
wrangler kv namespace create RATE_LIMIT_KV
wrangler kv namespace create RATE_LIMIT_KV --preview
```

**Adding a new app:**
1. Add `newapp:wcmchenry3-stack/newapp` to `APP_REPO_MAP` secret.
2. Add the app's origin to `ALLOWED_ORIGINS` secret.
3. Redeploy: `wrangler deploy` from `cloudflare/feedback-worker/`.
4. The `user-feedback` label is auto-created in the target repo on first submission.

**Rate limiting:** 5 submissions per IP per 10 minutes + 50/min per appId burst limit (Cloudflare KV).
**Content safety:** Claude classifies each submission — spam/abuse/PII/prompt-injection returns 422 before any issue is created.
**Screenshots:** Stored as a private GitHub Gist (`screenshot.b64`); the issue body links to the Gist.
**Session logs:** Appended as a collapsed `<details>` block in the issue body (truncated at 20 000 chars).
**Theme contract:** Each app skins the widget via `frontend/feedback-theme.css` — see [`.claude/policies/feedback-widget-tokens.md`](.claude/policies/feedback-widget-tokens.md) for the full token reference and new-app integration checklist.

## Versioning

All org repos use [Semantic Versioning](https://semver.org/) with automated
releases powered by [release-please](https://github.com/googleapis/release-please).

### Pre-live convention

Apps start at `0.1.0` and stay in `0.x.y` until officially launched.
`1.0.0` is the official launch milestone — cut via a `feat!:` PR or by
manually editing `.release-please-manifest.json`.

### Conventional Commits (enforced on PR titles)

Repos use squash-merge, so the PR title becomes the commit on `main`.
The `commitlint.yml` workflow gates every PR against this format:

```
<type>[!]: <subject>
```

| PR title prefix | Version bump (pre-1.0.0) |
|---|---|
| `feat:` | minor — `0.1.0 → 0.2.0` |
| `fix:` / `perf:` | patch — `0.2.0 → 0.2.1` |
| `feat!:` or `BREAKING CHANGE:` footer | major — `0.2.1 → 1.0.0` |
| `docs:` / `chore:` / `ci:` / `test:` / `style:` / `refactor:` / `build:` | no bump, hidden from CHANGELOG |

### Release process

1. Merge any `feat:` or `fix:` PR to `main`.
2. release-please automatically opens a Release PR (`chore: release X.Y.Z`)
   with an updated `CHANGELOG.md` and bumped version file.
3. Review the CHANGELOG in the PR, then merge it.
4. Git tag `vX.Y.Z` and a GitHub Release are created automatically.

### Seeding a new repo

Run the `seed-versioning.yml` workflow via `workflow_dispatch`:
- `target-repo`: the repo name (e.g. `gaming_app`)
- `release-type`: `node` (default, for `package.json` repos) or `python`
- `initial-version`: starting version (default: `0.1.0`)

This creates `release-please-config.json` and `.release-please-manifest.json`
in the target repo via a PR. Merge the PR, then release-please is active.

### Config files (per app repo)

| File | Purpose |
|---|---|
| `release-please-config.json` | release-please behaviour (release type, CHANGELOG sections) |
| `.release-please-manifest.json` | current version tracker — do not edit manually except to cut 1.0.0 |

## Audit script (`scripts/audit-claude-config.sh`)

`scripts/audit-claude-config.sh` compares the `.claude/` contents of each target
repo against the FILE_MAP in `sync-community-health.yml`. Run it periodically to
catch sync drift or local additions that should be upstreamed.

```bash
./scripts/audit-claude-config.sh              # full audit, all 6 repos
./scripts/audit-claude-config.sh --json       # machine-readable output
./scripts/audit-claude-config.sh --repos gaming_app,book_app   # subset
```

Requires: `gh` CLI authenticated, `jq`.

### Audit findings (2026-04-11)

All 6 target repos have zero sync gaps — every FILE_MAP entry is present.

**`wcm_portfolio_site`** has 6 extra `.claude/` files not in the FILE_MAP.
These are intentionally repo-specific and should **not** be upstreamed:

| File | Reason excluded |
|------|----------------|
| `.claude/accessibility.md` | Portfolio-specific color palette and focus ring rules |
| `.claude/code-style.md` | Portfolio file naming, named-export convention, Tailwind class order |
| `.claude/git-workflow.md` | Portfolio pre-commit command sequence |
| `.claude/i18n.md` | Portfolio locale list (13 locales) and i18next namespace map |
| `.claude/testing.md` | Portfolio component test case catalogue |
| `.claude/translation-philosophy.md` | Bill McHenry brand/marketing translation brief |

All other repos: fully synced, no local additions.

## CI hardening lessons

Lessons from real incidents in org repos — follow these to avoid the fix-the-fix chain anti-pattern.

### Always use `npm ci`, never `npm install`

`npm ci` installs from `package-lock.json` exactly, catching lockfile drift and preventing
"works locally, breaks in CI" failures. All reusable workflows already enforce this.

### `pod install` requires retry logic

CocoaPods CDN (`cdn.cocoapods.org`) has transient timeouts. Both `called-ios-build-check.yml`
and `called-ios-e2e.yml` already wrap `pod install` in a 3-attempt retry loop with backoff.
Any new iOS workflow must include the same pattern.

### Test CI scripts against a clean environment first

Lesson from gaming_app PRs #82–#86 — five consecutive PRs each fixing a side effect of the
previous one (missing Node.js on runner, wrong PATH, circular symlink). One test on a fresh
runner would have caught all of it. Before merging a new workflow:

1. Run it on a throwaway branch against a real runner.
2. Verify it starts from a clean state (no cached `node_modules`, no existing Pods).
3. Batch all fixes into one PR — never chain "fix the fix" PRs.

### Never use `npm install` in Render deploy scripts

Render's build environment mirrors `npm ci` behavior. Using `npm install` in a deploy
script can silently resolve different versions than CI tested against. The
`called-render-preflight.yml` workflow explicitly validates this before deploy.

## Conventions

- Workflows are prefixed `called-` and use `workflow_call` triggers.
- All repos should inherit `.claude/` from this meta-repo when possible.
- `settings.local.json` is gitignored — use it for personal overrides.

## Removed workflows

- `scheduled-openai-policy-check.yml` — removed. Used `curl -f` to scrape `openai.com/policies/*`,
  which is Cloudflare-protected and blocks GitHub Actions runners (exit 22). Never successfully ran.
- `scheduled-wikipedia-policy-check.yml` — removed. MediaWiki API curl calls failed on every run
  (exit 2). Never successfully ran. Use Wikimedia's built-in watchlist/email notifications instead.
