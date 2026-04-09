# Security Policy

## Supported Versions

Only the latest release on `main` is actively maintained and receives security updates.

| Branch | Supported |
|--------|-----------|
| `main` (latest) | Yes |
| Older releases | No |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report privately via [GitHub's private vulnerability reporting](https://github.com/wcmchenry3-stack) or email the maintainer directly (see GitHub profile for contact details).

**Include in your report:**
- Description of the vulnerability
- Steps to reproduce
- Potential impact assessment
- Any suggested mitigations (optional)

**Response timeline:**
- Acknowledgement within **48 hours**
- Status update within **7 days**
- Fix or mitigation plan within **30 days** for confirmed vulnerabilities

## Disclosure Policy

- Vulnerabilities will be remediated before public disclosure.
- Reporters will be credited in release notes (unless they prefer anonymity).
- We follow a **90-day coordinated disclosure** timeline.

## Automated Security Controls

Every pull request is gated on the following automated checks:

| Check | Tool | What it catches |
|-------|------|----------------|
| SAST | Bandit | Python code security issues (injection, insecure patterns) |
| SCA — Python | pip-audit | Known CVEs in Python dependencies |
| SCA — Frontend | npm audit | Known CVEs in Node / Expo dependencies |
| Secret scanning | GitHub Secret Scanning + Gitleaks | Credentials committed to source |
| DAST (post-deploy) | OWASP ZAP | Live application vulnerabilities (XSS, SQLi, etc.) |
| DAST (weekly) | OWASP ZAP active scan | Ongoing penetration testing against deployed services |

*Not every check applies to every project — mobile-only repos skip frontend npm audit; static sites skip Python checks. All checks are listed here so the full posture is visible even when a given check is not yet wired up.*

## Security Architecture

### Authentication & Authorisation
- RS256 JWT with Google OAuth 2.0; refresh token rotation with JTI revocation
- Role-based access control enforced server-side on every endpoint; no client-side trust

### Database
- SQLAlchemy ORM only — no raw SQL anywhere in the codebase
- All queries use parameterised values; string interpolation into queries is rejected in review
- Database credentials loaded exclusively from environment variables; never committed

### Rate Limiting
- Per-user (authenticated) and per-IP (unauthenticated) limits via slowapi
- Every public endpoint is throttled — no unprotected routes
- Authenticated routes key by user ID; unauthenticated routes key by IP

### Input Validation & Injection Prevention
- All user input validated at the API boundary before any processing
- File uploads: extension, MIME type, size, and magic-bytes validation before storage or processing
- No `shell=True` in subprocess calls; no dynamic SQL string construction

### Security Headers
- `Content-Security-Policy` (CSP)
- `Strict-Transport-Security` (HSTS) — enforced in production
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Permissions-Policy`
- `Referrer-Policy`

### CORS
- Explicit origin allowlist; wildcard origins (`*`) rejected at configuration validation time
- Credentials not permitted with wildcard origins

### Mobile (iOS & Android)
- No secrets, API keys, or credentials bundled in the app binary
- All credentials resolved via authenticated server-side endpoints
- `PrivacyInfo.xcprivacy` maintained and kept current for App Store compliance
- Certificate pinning evaluated on each release for sensitive endpoints
- Privacy manifest reviewed before each App Store / Google Play submission

### Secrets Management
- All secrets stored in environment variables or GitHub Actions secrets — never in source
- `.env` files are gitignored and never committed
- Gitleaks pre-commit and CI scan enforced on every push
