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

**Current policies:** OpenAI, Wikipedia/Wikimedia, Google Gemini.

## Org-level policy enforcement (`policy-enforcement.yml`)

`called-openai-policy.yml` and `called-gemini-policy.yml` read
`.github/policy-enforcement.yml` from this meta-repo at job start to
decide whether violations **fail the check** or are **advisory only**.

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

## Conventions

- Workflows are prefixed `called-` and use `workflow_call` triggers.
- All repos should inherit `.claude/` from this meta-repo when possible.
- `settings.local.json` is gitignored — use it for personal overrides.

## Removed workflows

- `scheduled-openai-policy-check.yml` — removed. Used `curl -f` to scrape `openai.com/policies/*`,
  which is Cloudflare-protected and blocks GitHub Actions runners (exit 22). Never successfully ran.
- `scheduled-wikipedia-policy-check.yml` — removed. MediaWiki API curl calls failed on every run
  (exit 2). Never successfully ran. Use Wikimedia's built-in watchlist/email notifications instead.
