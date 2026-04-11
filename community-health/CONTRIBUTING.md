# Contributing

Thank you for your interest in contributing to the `wcmchenry3-stack` organization.

## Getting started

1. Fork the repository and create your branch from `dev` (or `main` if no `dev` branch exists).
2. Run the existing tests and make sure they pass before making changes.
3. Make your changes, adding tests for any new functionality.
4. Ensure all CI checks pass locally where possible.
5. Open a pull request targeting `dev`.

## Branch naming

```
feat/<short-description>
fix/<short-description>
chore/<short-description>
docs/<short-description>
```

## Commit and PR title format

All repositories use [Conventional Commits](https://www.conventionalcommits.org/).
PR titles (which become the squash-merge commit message) must follow:

```
<type>[!]: <subject>
```

| Type       | When to use                                 |
| ---------- | ------------------------------------------- |
| `feat`     | New feature or behavior                     |
| `fix`      | Bug fix                                     |
| `chore`    | Tooling, deps, config — no behavior change  |
| `docs`     | Documentation only                          |
| `test`     | Adding or updating tests                    |
| `refactor` | Code change that is neither fix nor feature |
| `ci`       | CI/CD changes                               |
| `perf`     | Performance improvement                     |
| `style`    | Formatting, whitespace                      |

Append `!` after the type (e.g. `feat!:`) to signal a breaking change.

## Code standards

- **Coverage:** 80% floor enforced in CI — new code must include tests.
- **Security:** No raw SQL, no `shell=True`, no secrets in source. See `SECURITY.md`.
- **Accessibility:** WCAG 2.2 AA required on every interactive element in frontend projects.
- **i18n:** All user-facing strings must use `i18next` — no hardcoded UI copy.
- **Rate limiting:** Every HTTP endpoint must be rate-limited.

## Pull request checklist

Before requesting review, confirm:

- [ ] All tests pass
- [ ] No new lint errors
- [ ] No secrets committed (`gitleaks` passes)
- [ ] Coverage did not drop below 80%
- [ ] Accessibility checklist completed (frontend PRs)
- [ ] PR title follows Conventional Commits format

## Reporting bugs

Open a GitHub issue using the **Bug report** template. For security
vulnerabilities, follow the process in `SECURITY.md` — **do not open a
public issue**.
