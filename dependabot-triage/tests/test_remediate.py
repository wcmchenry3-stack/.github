"""Tests for deciding when a red Dependabot PR is worth rebuilding.

The motivating case: five BookshelfAI PRs failed a check named `lint-frontend`,
which made them look like lint failures. They were `npm ci` refusing to run
because the lockfile disagreed with package.json — nothing to do with linting,
and not caused by the bump.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import REQUIRED, green_checks, make_pr  # noqa: E402

import remediate  # noqa: E402
from models import CheckRun  # noqa: E402

NPM_DESYNC = """
npm error code EUSAGE
npm error `npm ci` can only install packages when your package.json and
package-lock.json or npm-shrinkwrap.json are in sync.
npm error Missing: react-native-worklets@0.8.3 from lock file
"""
PIP_UNRESOLVABLE = "ERROR: Cannot install -r requirements.txt\nERROR: ResolutionImpossible"
REAL_TEST_FAILURE = """
FAIL src/components/Card.test.tsx
  * renders the title
    AssertionError: expected 'a' to equal 'b'
Test Suites: 1 failed, 12 passed
"""
LINT_FAILURE = "would be reformatted: src/index.ts\nOh no! 1 file would be reformatted."


def red_pr(**kw):
    checks = green_checks()[:-1] + [CheckRun(name=sorted(REQUIRED)[-1], conclusion="FAILURE")]
    return make_pr(checks=checks, **kw)


@pytest.mark.parametrize("text", [NPM_DESYNC, PIP_UNRESOLVABLE])
def test_lockfile_desyncs_are_recoverable_by_a_rebuild(text):
    recoverable, reason = remediate.classify_failure(text)
    assert recoverable, reason


@pytest.mark.parametrize("text", [REAL_TEST_FAILURE, LINT_FAILURE])
def test_failures_caused_by_the_update_are_not_recoverable(text):
    """A rebuild cannot fix these and would burn a full CI cycle."""
    assert not remediate.classify_failure(text)[0]


def test_a_real_test_failure_wins_over_a_desync_line_in_the_same_log():
    """Logs can contain both; the genuine failure is the more important fact."""
    assert not remediate.classify_failure(NPM_DESYNC + REAL_TEST_FAILURE)[0]


def test_empty_log_is_not_treated_as_recoverable():
    recoverable, reason = remediate.classify_failure("   ")
    assert not recoverable and "no log" in reason


def test_unrecognised_failure_is_left_alone():
    recoverable, reason = remediate.classify_failure("Segmentation fault (core dumped)")
    assert not recoverable and "not recognised" in reason


@pytest.mark.parametrize("authors", [["dependabot[bot]"], ["app/dependabot", "dependabot"], []])
def test_dependabot_only_branches_have_no_human_commits(authors):
    assert not remediate.has_human_commits(authors)


def test_a_human_commit_is_detected():
    assert remediate.has_human_commits(["dependabot[bot]", "wcmchenry3-stack"])


def test_human_commits_block_recreate_even_on_a_clean_desync():
    """90 days of history had 30 PRs carrying real human fix-up commits."""
    ok, why = remediate.should_recreate(
        red_pr(), REQUIRED, NPM_DESYNC, ["dependabot[bot]", "wcmchenry3-stack"]
    )
    assert not ok
    assert "human commits" in why


def test_recreates_a_dependabot_only_desync():
    ok, why = remediate.should_recreate(red_pr(), REQUIRED, NPM_DESYNC, ["dependabot[bot]"])
    assert ok and "lock" in why


def test_does_not_recreate_when_nothing_is_failing():
    ok, why = remediate.should_recreate(make_pr(), REQUIRED, NPM_DESYNC, ["dependabot[bot]"])
    assert not ok and "no required check is failing" in why


def test_does_not_recreate_a_genuine_test_failure():
    assert not remediate.should_recreate(
        red_pr(), REQUIRED, REAL_TEST_FAILURE, ["dependabot[bot]"]
    )[0]
