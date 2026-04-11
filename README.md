# wcmchenry3-stack/.github

Shared GitHub Actions workflows, community health files, and Claude Code
configuration for the **wcmchenry3-stack** organization.

## Repo purpose

This is a meta-repository. It does not contain application code. It provides:

- **Reusable CI/CD workflows** (`.github/workflows/called-*.yml`) — called from individual repos via `workflow_call`
- **Community health files** (`community-health/`) — synced to all org repos via `sync-community-health.yml`
- **Claude Code agents, hooks, and settings** (`.claude/`) — synced to all org repos via `sync-community-health.yml`
- **Org-level policy enforcement** — controls whether policy violations fail checks or are advisory-only

> **Note:** Community health files and Claude Code tooling do **not** auto-apply to org repos.
> They are explicitly pushed to each repo by `sync-community-health.yml` on every merge to `main`.

## Reusable Workflows

All workflows are prefixed `called-` and use `on: workflow_call`. Call them from any repo with `secrets: inherit`.

| Workflow | Purpose |
|----------|---------|
| `called-secret-scan.yml` | gitleaks full-history secret scan |
| `called-gate-main-source.yml` | Ensures PRs to main only come from `dev` or `release/*` |
| `called-sync-main-to-dev.yml` | Auto-syncs main back to dev after merge |
| `called-lint-python.yml` | black + ruff |
| `called-lint-frontend.yml` | eslint + prettier (optional a11y flag) |
| `called-test-python.yml` | pytest + coverage threshold |
| `called-test-frontend.yml` | jest/vitest + coverage threshold |
| `called-build-frontend.yml` | `npm run build` |
| `called-cve-python.yml` | pip-audit |
| `called-cve-frontend.yml` | npm audit --audit-level=high |
| `called-perf-frontend.yml` | Lighthouse CI — audits built dist, uploads results as artifacts |
| `called-perf-backend.yml` | Locust load test against a live URL with threshold assertions |
| `called-deploy-render.yml` | Render deploy hook + ZAP post-deploy scan |
| `called-zap-scheduled.yml` | ZAP baseline scan (caller owns the schedule) |
| `called-openai-policy.yml` | OpenAI API compliance check |
| `called-gemini-policy.yml` | Google Gemini API compliance check |
| `called-wikipedia-policy.yml` | Wikimedia API compliance check — restricted to `office_holder_cursor` |
| `called-design-token-check.yml` | Design tokens and WCAG 2.2 AA checks for frontend code |
| `called-commitlint.yml` | Enforces Conventional Commits format on PR titles |
| `called-schema-migration.yml` | Schema/migration change detection, Alembic model sync, backward compat grep |

## Caller Pattern

```yaml
jobs:
  secret-scan:
    uses: wcmchenry3-stack/.github/.github/workflows/called-secret-scan.yml@main

  lint-python:
    uses: wcmchenry3-stack/.github/.github/workflows/called-lint-python.yml@main
    with:
      python-version: '3.11'
      working-directory: '.'

  test-python:
    uses: wcmchenry3-stack/.github/.github/workflows/called-test-python.yml@main
    with:
      coverage-threshold: 80
    secrets: inherit
```

## Restricted Workflows

Some workflows are repo-scoped and will fail immediately if called from any other repository.

| Workflow | Permitted repos | Reason |
|----------|----------------|--------|
| `called-wikipedia-policy.yml` | `wcmchenry3-stack/office_holder_cursor` | Only repo using the Wikimedia API |
| `called-openai-policy.yml` | configurable via `allowed-repos` input | Repos using the OpenAI API |

## Community Health Files

Community health files live in `community-health/` and are **synced to all org repos**
by `sync-community-health.yml` whenever they change on `main`.

| File | Synced to (dest) |
|------|-----------------|
| `community-health/SECURITY.md` | `SECURITY.md` |
| `community-health/CODE_OF_CONDUCT.md` | `CODE_OF_CONDUCT.md` |
| `community-health/CONTRIBUTING.md` | `CONTRIBUTING.md` |
| `community-health/SUPPORT.md` | `SUPPORT.md` |
| `community-health/PULL_REQUEST_TEMPLATE.md` | `.github/PULL_REQUEST_TEMPLATE.md` |
| `community-health/ISSUE_TEMPLATE/bug_report.md` | `.github/ISSUE_TEMPLATE/bug_report.md` |
| `community-health/ISSUE_TEMPLATE/feature_request.md` | `.github/ISSUE_TEMPLATE/feature_request.md` |
| `community-health/workflows/commitlint.yml` | `.github/workflows/commitlint.yml` |
| `community-health/workflows/release-please.yml` | `.github/workflows/release-please.yml` |
| `community-health/workflows/openai-policy.yml` | `.github/workflows/openai-policy.yml` |
| `community-health/workflows/gemini-policy.yml` | `.github/workflows/gemini-policy.yml` |
| `community-health/workflows/design-token-check.yml` | `.github/workflows/design-token-check.yml` |
| `community-health/workflows/schema-migration.yml` | `.github/workflows/schema-migration.yml` |

## Claude Code Tooling

Claude Code hooks, agents, policies, and settings are also synced to all org repos
by `sync-community-health.yml`.

| Source (this repo) | Synced to (dest) |
|--------------------|-----------------|
| `.claude/hooks/lint-on-edit.sh` | `.claude/hooks/lint-on-edit.sh` |
| `.claude/hooks/lint-gate.sh` | `.claude/hooks/lint-gate.sh` |
| `.claude/hooks/policy-gate.sh` | `.claude/hooks/policy-gate.sh` |
| `.claude/agents/plan-issues.md` | `.claude/agents/plan-issues.md` |
| `.claude/agents/lint-review.md` | `.claude/agents/lint-review.md` |
| `.claude/agents/policy-compliance.md` | `.claude/agents/policy-compliance.md` |
| `.claude/policies/policy-patterns.json` | `.claude/policies/policy-patterns.json` |
| `.claude/policies/openai.md` | `.claude/policies/openai.md` |
| `.claude/policies/gemini.md` | `.claude/policies/gemini.md` |
| `.claude/policies/wikipedia.md` | `.claude/policies/wikipedia.md` |
| `.claude/policies/claude.md` | `.claude/policies/claude.md` |
| `.claude/policies/design-tokens.md` | `.claude/policies/design-tokens.md` |
| `.claude/settings.json` _(hooks only)_ | `.claude/settings.json` _(merged, not overwritten)_ |

The `settings.json` sync merges only the `PreToolUse` and `PostToolUse` hook arrays —
repo-specific settings are preserved.

## Policy Enforcement

`policy-enforcement.yml` (in `.github/`) controls whether policy check failures
**block PRs** or are **advisory only**.

- Default: `advisory` — violations are annotated in the PR UI but do not fail the check.
- Set a repo to `required` under `overrides:` once it actively uses the API in question.

See [`CLAUDE.md`](CLAUDE.md) for full details on adding new policies and the enforcement model.
