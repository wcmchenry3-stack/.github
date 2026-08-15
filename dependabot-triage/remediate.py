"""Deciding when a red Dependabot PR is worth asking Dependabot to rebuild.

Some Dependabot PRs arrive red through no fault of the dependency being bumped.
The dominant case in this org is a lockfile that disagrees with its manifest —
`npm ci` refuses before a single test or lint rule runs, and the job that fails
is often named after the thing it never got to (`lint-frontend`), which makes
the failure look like something it isn't.

Asking Dependabot to `recreate` rebuilds the branch from scratch against current
base and regenerates the lockfile. That costs nothing here and needs no new
capability: Dependabot does the work under its own identity, and this agent
still never writes a file.

The important constraint is that **recreate force-pushes**. Any human commit on
the branch is destroyed. Across 90 days, 30 merged Dependabot PRs carried real
human fix-up work, so a recreate that ignored authorship would eventually throw
away someone's afternoon.
"""

from __future__ import annotations

import logging
import re

from models import PullRequest

log = logging.getLogger(__name__)

DEPENDABOT_LOGINS = frozenset({"dependabot", "dependabot[bot]", "app/dependabot"})

# Failure signatures that a rebuild plausibly fixes: the manifest and the lock
# disagree, or resolution was attempted against a stale tree. Anything caused by
# the new version itself will simply fail again, so the list is deliberately
# narrow rather than "any red PR".
RECOVERABLE_SIGNATURES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"can only install packages when your package\.json and", re.I),
        "npm lockfile out of sync with package.json",
    ),
    (re.compile(r"Missing: .+ from lock file", re.I), "package missing from lockfile"),
    (re.compile(r"npm error code EUSAGE", re.I), "npm ci usage error (lock desync)"),
    (
        re.compile(r"lock file.{0,40}(out of date|does not match|is not up to date)", re.I),
        "lockfile stale relative to manifest",
    ),
    (re.compile(r"ERROR: ResolutionImpossible", re.I), "pip could not resolve the tree"),
    (
        re.compile(r"poetry\.lock is not consistent with pyproject\.toml", re.I),
        "poetry lockfile inconsistent",
    ),
)

# Signatures that mean the bump itself is the problem. A rebuild cannot help and
# would burn a full CI cycle — 20-30 minutes on the largest repo here.
UNRECOVERABLE_SIGNATURES: tuple[re.Pattern[str], ...] = (
    re.compile(r"Test Suites?:.*failed", re.I),
    re.compile(r"\d+ (?:test|spec)s? failed", re.I),
    re.compile(r"AssertionError", re.I),
    re.compile(r"error TS\d+", re.I),
    re.compile(r"would be reformatted", re.I),
    re.compile(r"found \d+ vulnerabilit", re.I),
)


def classify_failure(log_text: str) -> tuple[bool, str]:
    """Return ``(recoverable_by_rebuild, human_readable_reason)``.

    Unrecoverable signatures are checked first: a log can contain both, and a
    genuine test failure is the more important fact.
    """
    if not log_text.strip():
        return False, "no log available"
    for pattern in UNRECOVERABLE_SIGNATURES:
        if pattern.search(log_text):
            return False, "failure looks caused by the update itself"
    for pattern, reason in RECOVERABLE_SIGNATURES:
        if pattern.search(log_text):
            return True, reason
    return False, "failure signature not recognised as recoverable"


def has_human_commits(authors: list[str]) -> bool:
    """True when anyone other than Dependabot has committed to the branch."""
    return any(a and a not in DEPENDABOT_LOGINS for a in authors)


def should_recreate(
    pr: PullRequest,
    required: frozenset[str],
    log_text: str,
    commit_authors: list[str],
) -> tuple[bool, str]:
    """Whether to ask Dependabot to rebuild this branch, and why not if not."""
    if not pr.failing_required(required):
        return False, "no required check is failing"
    if has_human_commits(commit_authors):
        return False, "branch carries human commits that a recreate would destroy"
    recoverable, reason = classify_failure(log_text)
    if not recoverable:
        return False, reason
    return True, reason
