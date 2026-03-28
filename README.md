# wcmchenry3-stack/.github

Shared GitHub Actions reusable workflows and community health files for all `wcmchenry3-stack` repositories.

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
| `called-perf-frontend.yml` | Lighthouse CI — audits built dist, uploads results as artifacts (informational) |
| `called-perf-backend.yml` | Locust load test against a live URL with threshold assertions |
| `called-deploy-render.yml` | Render deploy hook + ZAP post-deploy scan |
| `called-zap-scheduled.yml` | ZAP baseline scan (caller owns the schedule) |
| `called-wikipedia-policy.yml` | Wikimedia API compliance check — restricted to `office_holder_cursor` |
| `called-openai-policy.yml` | OpenAI API compliance check — restricted to permitted repos (comma-separated list) |

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
| `called-openai-policy.yml` | `wcmchenry3-stack/office_holder_cursor`, `wcmchenry3-stack/book_app` | Repos using the OpenAI API |

**Caller pattern for `office_holder_cursor` (Wikipedia):**

```yaml
jobs:
  wikipedia-policy:
    uses: wcmchenry3-stack/.github/.github/workflows/called-wikipedia-policy.yml@main
    with:
      allowed-repo: 'wcmchenry3-stack/office_holder_cursor'
```

**Caller pattern for OpenAI repos:**

```yaml
jobs:
  openai-policy:
    uses: wcmchenry3-stack/.github/.github/workflows/called-openai-policy.yml@main
    with:
      allowed-repos: 'wcmchenry3-stack/office_holder_cursor,wcmchenry3-stack/book_app'
```

To add a new OpenAI-using repo: append `,wcmchenry3-stack/new-repo` to `allowed-repos` in the caller — no changes needed to the workflow itself.

Companion scheduled workflows run on the 1st of each month to detect policy page changes and open a GitHub issue here if updates are found. Both support `workflow_dispatch` for on-demand checks.

## Community Files

`PULL_REQUEST_TEMPLATE.md`, `ISSUE_TEMPLATE/`, and `SECURITY.md` auto-apply to all repos in this account that don't override them.
