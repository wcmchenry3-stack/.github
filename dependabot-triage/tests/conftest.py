"""Fixtures shared across the triage test suite.

The builders below construct realistic PR payloads cheaply so individual tests
read as "this specific thing is wrong with this PR" rather than a wall of setup.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The package is a flat module directory rather than an installed distribution,
# so make it importable without a packaging step.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import ChangedFile, CheckRun, PullRequest, RepoBaseline  # noqa: E402

REQUIRED = frozenset(
    {
        "lint / Lint frontend (eslint + prettier)",
        "test / Test frontend (jest/vitest)",
        "secret-scan / Secret scan (gitleaks)",
    }
)


def green_checks(required: frozenset[str] = REQUIRED) -> list[CheckRun]:
    return [CheckRun(name=n, conclusion="SUCCESS", status="COMPLETED") for n in sorted(required)]


def make_pr(**overrides) -> PullRequest:
    """A clean, mergeable npm patch bump that passes every guard."""
    defaults = {
        "repo": "buffingchi_site",
        "number": 101,
        "title": "chore(deps): bump lodash from 4.17.20 to 4.17.21",
        "author": "app/dependabot",
        "base_ref": "dev",
        "head_sha": "a" * 40,
        "files": [
            ChangedFile(
                path="package.json",
                additions=1,
                deletions=1,
                patch='@@ -12,7 +12,7 @@\n-    "lodash": "4.17.20"\n+    "lodash": "4.17.21"\n',
            ),
            ChangedFile(path="package-lock.json", additions=8, deletions=8, patch=""),
        ],
        "labels": ["dependencies", "javascript"],
        "checks": green_checks(),
        "mergeable": "MERGEABLE",
        "merge_state_status": "CLEAN",
        "ecosystem": "npm",
    }
    defaults.update(overrides)
    return PullRequest(**defaults)


def make_baseline(**overrides) -> RepoBaseline:
    defaults = {
        "repo": "buffingchi_site",
        "required_contexts": REQUIRED,
        "workflow_file_shas": {".github/workflows/ci.yml": "deadbeef"},
        "dependabot_config_sha": "cafebabe",
        "thresholds": {"cov_fail_under": 80.0},
    }
    defaults.update(overrides)
    return RepoBaseline(**defaults)


def workflow_pr(patch: str, **overrides) -> PullRequest:
    """A github-actions ecosystem PR carrying the supplied workflow diff."""
    return make_pr(
        title="chore(deps): bump actions/setup-python from 6 to 7",
        ecosystem="github-actions",
        labels=["dependencies", "github_actions"],
        files=[ChangedFile(path=".github/workflows/ci.yml", additions=1, deletions=1, patch=patch)],
        **overrides,
    )


@pytest.fixture
def pr() -> PullRequest:
    return make_pr()


@pytest.fixture
def baseline() -> RepoBaseline:
    return make_baseline()
