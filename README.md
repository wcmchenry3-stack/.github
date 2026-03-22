# wcmchenry3-stack/.github

Shared GitHub Actions reusable workflows and community health files for all `wcmchenry3-stack` repositories.

## Reusable Workflows

All workflows are prefixed `called-` and use `on: workflow_call`. Call them from any repo with `secrets: inherit`.

| Workflow | Purpose |
|----------|---------|
| `called-secret-scan.yml` | gitleaks full-history secret scan |
| `called-lint-python.yml` | black + ruff |
| `called-lint-frontend.yml` | eslint + prettier (optional a11y flag) |
| `called-test-python.yml` | pytest + coverage threshold |
| `called-test-frontend.yml` | jest/vitest + coverage threshold |
| `called-build-frontend.yml` | `npm run build` |
| `called-cve-python.yml` | pip-audit |
| `called-cve-frontend.yml` | npm audit --audit-level=high |
| `called-deploy-render.yml` | Render deploy hook + ZAP post-deploy scan |
| `called-zap-scheduled.yml` | ZAP baseline scan (caller owns the schedule) |

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

## Community Files

`PULL_REQUEST_TEMPLATE.md`, `ISSUE_TEMPLATE/`, and `SECURITY.md` auto-apply to all repos in this account that don't override them.
