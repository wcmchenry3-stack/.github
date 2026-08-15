"""Deterministic merge preconditions — the security boundary of the agent.

Nothing in this module calls a model. Every function is pure over its inputs so
the whole boundary is testable from fixtures. The executor may only merge a pull
request when :func:`run_all_guards` returns no failures, and it re-runs the full
set against freshly fetched API state immediately before the merge call.

Guard IDs are stable and appear verbatim in the ledger, the PR comment, and the
nightly email, so a failure can always be traced back to the rule that caused it.
"""

from __future__ import annotations

import fnmatch
import re

from models import ChangedFile, GuardResult, PullRequest, RepoBaseline

DEPENDABOT_AUTHORS = frozenset({"app/dependabot", "dependabot[bot]", "dependabot"})

# Paths a Dependabot PR is permitted to touch. Matched with fnmatch against the
# repo-relative path; `**/` prefixes cover monorepo subdirectories.
PATH_ALLOWLIST: tuple[str, ...] = (
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "**/package.json",
    "**/package-lock.json",
    "**/npm-shrinkwrap.json",
    "**/yarn.lock",
    "**/pnpm-lock.yaml",
    "**/bun.lockb",
    "requirements.txt",
    "requirements-*.txt",
    "**/requirements.txt",
    "**/requirements-*.txt",
    "pyproject.toml",
    "**/pyproject.toml",
    "poetry.lock",
    "**/poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "**/Pipfile",
    "**/Pipfile.lock",
    "Gemfile.lock",
    "**/Gemfile.lock",
    "Podfile.lock",
    "**/Podfile.lock",
    "go.mod",
    "go.sum",
    "**/go.mod",
    "**/go.sum",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
)

# Checked before the allowlist and always wins. These are the files that define
# whether CI is meaningful; a dependency bump has no business editing them.
PATH_DENYLIST: tuple[str, ...] = (
    ".gitleaks.toml",
    "**/.gitleaks.toml",
    "codecov.yml",
    ".codecov.yml",
    "**/codecov.yml",
    "pytest.ini",
    "**/pytest.ini",
    "setup.cfg",
    "**/setup.cfg",
    "tox.ini",
    "**/tox.ini",
    ".coveragerc",
    "**/.coveragerc",
    ".eslintrc*",
    "**/.eslintrc*",
    "eslint.config.*",
    "**/eslint.config.*",
    "jest.config.*",
    "**/jest.config.*",
    "vitest.config.*",
    "**/vitest.config.*",
    "playwright.config.*",
    "**/playwright.config.*",
    ".pre-commit-config.yaml",
    "**/.pre-commit-config.yaml",
    "CODEOWNERS",
    ".github/CODEOWNERS",
    "docs/CODEOWNERS",
    ".github/dependabot.yml",
    ".github/dependabot.yaml",
    "renovate.json",
    ".github/renovate.json",
)

# A workflow file may change only version pins. Covers bare and SHA-pinned forms,
# with or without a trailing comment, and with or without a YAML list dash.
USES_PIN_RE = re.compile(r"^[+-]\s*(?:-\s+)?uses:\s*\S+@\S+\s*(?:#.*)?$")

# Machine-generated files whose contents are package metadata we do not control,
# so a forbidden token appearing inside one is not evidence of tampering.
GENERATED_LOCKFILES: tuple[str, ...] = (
    "*package-lock.json",
    "*npm-shrinkwrap.json",
    "*yarn.lock",
    "*pnpm-lock.yaml",
    "*bun.lockb",
    "*poetry.lock",
    "*Pipfile.lock",
    "*Gemfile.lock",
    "*Podfile.lock",
    "*go.sum",
)

# Tokens that weaken a check. Scanned against *added* lines only, outside
# generated lockfiles. Each entry is (regex, human-readable explanation).
FORBIDDEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"continue-on-error\s*:\s*true"), "disables failure propagation in CI"),
    (re.compile(r"\bif\s*:\s*false\b"), "disables a CI job or step outright"),
    (re.compile(r"\|\|\s*true\b"), "swallows a non-zero exit code"),
    (re.compile(r"--no-verify\b"), "bypasses git hooks"),
    (re.compile(r"--audit-level[= ]"), "changes the vulnerability failure threshold"),
    (re.compile(r"--cov-fail-under[= ]"), "changes the coverage failure threshold"),
    (re.compile(r"\bfail_ci_if_error\s*:\s*false"), "stops coverage upload failures failing CI"),
    (re.compile(r"\b(?:describe|it|test)\.(?:skip|only)\s*\("), "skips or isolates tests"),
    (re.compile(r"@pytest\.mark\.(?:skip|xfail)"), "skips or expects-fail a test"),
    (re.compile(r"eslint-disable"), "suppresses a lint rule"),
    (re.compile(r"#\s*type:\s*ignore"), "suppresses a type error"),
    (re.compile(r"--legacy-peer-deps\b"), "masks an unresolved peer-dependency conflict"),
    (re.compile(r"npm\s+(?:install|ci)[^\n]*--force\b"), "forces past dependency resolution"),
    (re.compile(r"\bSKIP\s*=\s*\S+"), "skips pre-commit hooks"),
)

# Numeric quality gates. Values may rise or hold, never fall (guard G08).
THRESHOLD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cov_fail_under", re.compile(r"--cov-fail-under[= ](\d+(?:\.\d+)?)")),
    ("coverage_threshold", re.compile(r"coverage-threshold\s*:\s*(\d+(?:\.\d+)?)")),
    ("min_coverage", re.compile(r"minimum_coverage\s*:\s*(\d+(?:\.\d+)?)")),
)

# npm audit levels ordered loosest to strictest; may only tighten.
AUDIT_LEVELS: tuple[str, ...] = ("info", "low", "moderate", "high", "critical")


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    """True when ``path`` matches any fnmatch pattern.

    ``**/x`` is expanded to also match a bare ``x`` at the repository root so a
    single pattern covers both layouts.
    """
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:]):
            return True
    return False


def _is_generated_lockfile(path: str) -> bool:
    return _matches_any(path, GENERATED_LOCKFILES)


def _is_workflow(path: str) -> bool:
    return path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))


# --------------------------------------------------------------------------
# Individual guards
# --------------------------------------------------------------------------


def guard_author_is_dependabot(pr: PullRequest) -> GuardResult:
    """G01 — only Dependabot's own PRs are ever in scope."""
    ok = pr.author in DEPENDABOT_AUTHORS
    return GuardResult(
        "G01",
        ok,
        "author is Dependabot" if ok else f"author is {pr.author!r}, not Dependabot",
    )


def guard_paths_allowed(pr: PullRequest) -> GuardResult:
    """G02 — every changed path sits in the manifest/lockfile/workflow allowlist."""
    stray = [p for p in pr.paths if not _matches_any(p, PATH_ALLOWLIST)]
    if stray:
        return GuardResult(
            "G02", False, f"changed paths outside the allowlist: {', '.join(sorted(stray))}"
        )
    return GuardResult("G02", True, f"all {len(pr.paths)} changed paths allowlisted")


def guard_no_denylisted_paths(pr: PullRequest) -> GuardResult:
    """G03 — no CI-defining file is touched, whatever the allowlist says."""
    hits = [p for p in pr.paths if _matches_any(p, PATH_DENYLIST)]
    if hits:
        return GuardResult(
            "G03", False, f"touches CI-defining files: {', '.join(sorted(hits))}"
        )
    return GuardResult("G03", True, "no CI-defining files touched")


def guard_workflow_pins_only(pr: PullRequest) -> GuardResult:
    """G04 — inside ``.github/workflows/``, only ``uses:`` version pins may change.

    This is what makes it safe to let a ``github-actions`` ecosystem PR through
    at all: the diff is mechanically constrained to version bumps, so it cannot
    smuggle in a ``continue-on-error`` or delete a job.
    """
    offenders: list[str] = []
    for f in pr.files:
        if not _is_workflow(f.path):
            continue
        if not f.patch:
            offenders.append(f"{f.path}: no patch available to verify")
            continue
        for line in f.changed_lines():
            if line.strip() in ("+", "-"):
                continue  # blank-line churn
            if not USES_PIN_RE.match(line):
                offenders.append(f"{f.path}: {line.strip()[:80]}")
    if offenders:
        return GuardResult(
            "G04", False, "workflow diff contains non-pin changes: " + "; ".join(offenders[:5])
        )
    return GuardResult("G04", True, "workflow changes are version pins only")


def guard_no_forbidden_patterns(pr: PullRequest) -> GuardResult:
    """G05 — no added line weakens a check.

    Scans added lines only, skipping machine-generated lockfiles whose contents
    are upstream package metadata rather than anything this repo authored.
    """
    hits: list[str] = []
    for f in pr.files:
        if _is_generated_lockfile(f.path):
            continue
        for line in f.added_lines():
            for pattern, why in FORBIDDEN_PATTERNS:
                if pattern.search(line):
                    hits.append(f"{f.path}: {why} ({line.strip()[:60]})")
                    break
    if hits:
        return GuardResult("G05", False, "forbidden changes: " + "; ".join(hits[:5]))
    return GuardResult("G05", True, "no check-weakening patterns in added lines")


def guard_required_checks_green(pr: PullRequest, baseline: RepoBaseline) -> GuardResult:
    """G06 — every required context succeeded, and the required set has not shrunk.

    A required check that is red, still running, or entirely absent from the PR
    all block the merge — :meth:`PullRequest.failing_required` reports a missing
    context as ``MISSING`` rather than silently treating it as passing.

    A repo with no required contexts at all is refused outright: "CI is green"
    carries no information when nothing is required, which is exactly BC-Arcade's
    `dev` ruleset today.
    """
    if not baseline.required_contexts:
        return GuardResult(
            "G06",
            False,
            "repo declares no required status checks, so a green PR proves nothing",
        )

    failing = pr.failing_required(baseline.required_contexts)
    if failing:
        names = ", ".join(f"{c.name}={c.conclusion or c.status}" for c in failing)
        return GuardResult("G06", False, f"required checks not green: {names}")
    return GuardResult(
        "G06", True, f"all {len(baseline.required_contexts)} required checks green"
    )


def guard_protection_unchanged(
    baseline: RepoBaseline, live_contexts: frozenset[str]
) -> GuardResult:
    """G07 — branch protection's required-context set has not been weakened."""
    removed = baseline.required_contexts - live_contexts
    if removed:
        return GuardResult(
            "G07",
            False,
            f"required contexts removed since run start: {', '.join(sorted(removed))}",
        )
    return GuardResult("G07", True, "branch protection unchanged")


def guard_head_sha_unchanged(pr: PullRequest, assessed_sha: str) -> GuardResult:
    """G08 — the commit being merged is the one that was assessed and tested."""
    if not assessed_sha:
        return GuardResult("G08", False, "no assessed SHA recorded")
    ok = pr.head_sha == assessed_sha
    return GuardResult(
        "G08",
        ok,
        "head SHA matches assessment"
        if ok
        else f"head moved {assessed_sha[:7]} -> {pr.head_sha[:7]} since assessment",
    )


def guard_thresholds_not_lowered(
    baseline: RepoBaseline, current: dict[str, float]
) -> GuardResult:
    """G09 — numeric quality gates may rise or hold, never fall."""
    lowered = [
        f"{k}: {baseline.thresholds[k]} -> {v}"
        for k, v in current.items()
        if k in baseline.thresholds and v < baseline.thresholds[k]
    ]
    if lowered:
        return GuardResult("G09", False, "thresholds lowered: " + "; ".join(lowered))
    return GuardResult("G09", True, "no thresholds lowered")


def guard_mergeable(pr: PullRequest) -> GuardResult:
    """G10 — GitHub itself considers the PR cleanly mergeable.

    ``UNSTABLE`` is accepted because it means a non-required check is red; the
    required set is judged by G06, which is the set that actually gates merges.
    """
    if pr.mergeable.upper() == "CONFLICTING":
        return GuardResult("G10", False, "PR has merge conflicts")
    if pr.merge_state_status.upper() in ("DIRTY", "BLOCKED", "DRAFT"):
        return GuardResult("G10", False, f"merge state is {pr.merge_state_status}")
    if pr.merge_state_status.upper() == "BEHIND":
        return GuardResult("G10", False, "branch is behind base; needs rebase first")
    return GuardResult("G10", True, f"merge state {pr.merge_state_status or 'CLEAN'}")


def guard_not_held(pr: PullRequest, hold_label: str = "dependabot-triage:hold") -> GuardResult:
    """G11 — the manual kill switch on an individual PR."""
    if hold_label in pr.labels:
        return GuardResult("G11", False, f"{hold_label!r} label present")
    return GuardResult("G11", True, "no hold label")


# --------------------------------------------------------------------------
# Aggregate
# --------------------------------------------------------------------------


def run_all_guards(
    pr: PullRequest,
    baseline: RepoBaseline,
    assessed_sha: str,
    live_contexts: frozenset[str] | None = None,
    current_thresholds: dict[str, float] | None = None,
    hold_label: str = "dependabot-triage:hold",
) -> list[GuardResult]:
    """Run every merge precondition. An empty failure list is the only path to merge."""
    contexts = baseline.required_contexts if live_contexts is None else live_contexts
    thresholds = current_thresholds if current_thresholds is not None else baseline.thresholds
    return [
        guard_author_is_dependabot(pr),
        guard_paths_allowed(pr),
        guard_no_denylisted_paths(pr),
        guard_workflow_pins_only(pr),
        guard_no_forbidden_patterns(pr),
        guard_required_checks_green(pr, baseline),
        guard_protection_unchanged(baseline, contexts),
        guard_head_sha_unchanged(pr, assessed_sha),
        guard_thresholds_not_lowered(baseline, thresholds),
        guard_mergeable(pr),
        guard_not_held(pr, hold_label),
    ]


def failures(results: list[GuardResult]) -> list[GuardResult]:
    return [r for r in results if not r.passed]


def may_merge(results: list[GuardResult]) -> bool:
    return not failures(results)


# --------------------------------------------------------------------------
# Threshold extraction (used to build and re-check the baseline)
# --------------------------------------------------------------------------


def extract_thresholds(text: str) -> dict[str, float]:
    """Pull numeric quality gates out of CI config or a manifest.

    Where a key appears more than once the strictest (highest) value wins, so a
    later loosened duplicate cannot mask a stricter earlier declaration.
    """
    found: dict[str, float] = {}
    for key, pattern in THRESHOLD_PATTERNS:
        for match in pattern.finditer(text):
            value = float(match.group(1))
            found[key] = max(found.get(key, value), value)
    audit = re.search(r"--audit-level[= ](\w+)", text)
    if audit and audit.group(1).lower() in AUDIT_LEVELS:
        found["audit_level"] = float(AUDIT_LEVELS.index(audit.group(1).lower()))
    return found


def workflow_pin_changes(files: list[ChangedFile]) -> list[str]:
    """Human-readable summary of the ``uses:`` pins a PR moves."""
    out: list[str] = []
    for f in files:
        if not _is_workflow(f.path):
            continue
        for line in f.added_lines():
            match = re.search(r"uses:\s*(\S+)@(\S+)", line)
            if match:
                out.append(f"{match.group(1)}@{match.group(2)}")
    return out
