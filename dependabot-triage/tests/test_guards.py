"""Tests for the merge-precondition boundary.

The suite is organised around the question the boundary exists to answer: can a
pull request that weakens a check reach a merge? Every hostile fixture below is
a way someone (or something) might try, and each must be refused.
"""

from __future__ import annotations

import pytest
from conftest import REQUIRED, green_checks, make_baseline, make_pr, workflow_pr

import guards
from models import ChangedFile, CheckRun

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_clean_patch_bump_passes_every_guard(pr, baseline):
    results = guards.run_all_guards(pr, baseline, assessed_sha=pr.head_sha)
    assert guards.may_merge(results), [str(r) for r in guards.failures(results)]


def test_guard_ids_are_unique_and_stable(pr, baseline):
    results = guards.run_all_guards(pr, baseline, assessed_sha=pr.head_sha)
    ids = [r.guard_id for r in results]
    assert ids == sorted(ids), "guard IDs should be emitted in stable order"
    assert len(ids) == len(set(ids)), "guard IDs must be unique"


# ---------------------------------------------------------------------------
# G01 — authorship
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("author", ["app/dependabot", "dependabot[bot]", "dependabot"])
def test_accepts_known_dependabot_identities(author):
    assert guards.guard_author_is_dependabot(make_pr(author=author)).passed


@pytest.mark.parametrize("author", ["wcmchenry3", "app/renovate", "dependabot-preview[bot]", ""])
def test_rejects_impersonators_and_other_bots(author):
    result = guards.guard_author_is_dependabot(make_pr(author=author))
    assert not result.passed
    assert "not Dependabot" in result.detail


def test_impersonator_cannot_merge_even_with_a_perfect_diff(baseline):
    """The whole point: a human PR shaped like a bump is still out of scope."""
    pr = make_pr(author="wcmchenry3")
    results = guards.run_all_guards(pr, baseline, assessed_sha=pr.head_sha)
    assert not guards.may_merge(results)
    assert "G01" in {r.guard_id for r in guards.failures(results)}


# ---------------------------------------------------------------------------
# G02 — path allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "package.json",
        "frontend/package-lock.json",
        "backend/requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "poetry.lock",
        "ios/Podfile.lock",
        "go.sum",
        ".github/workflows/ci.yml",
        ".github/workflows/deploy.yaml",
    ],
)
def test_allowlist_covers_real_manifest_layouts(path):
    pr = make_pr(files=[ChangedFile(path=path)])
    assert guards.guard_paths_allowed(pr).passed, path


@pytest.mark.parametrize(
    "path",
    ["src/index.js", "backend/app/main.py", "README.md", "Dockerfile", "render.yaml"],
)
def test_rejects_source_and_infra_files(path):
    pr = make_pr(files=[ChangedFile(path=path)])
    result = guards.guard_paths_allowed(pr)
    assert not result.passed
    assert path in result.detail


def test_a_single_stray_file_fails_an_otherwise_clean_pr(baseline):
    pr = make_pr(files=[ChangedFile(path="package.json"), ChangedFile(path="src/api/client.ts")])
    results = guards.run_all_guards(pr, baseline, assessed_sha=pr.head_sha)
    assert not guards.may_merge(results)


# ---------------------------------------------------------------------------
# G03 — CI-defining files (hostile fixtures from the plan)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "pytest.ini",
        "setup.cfg",
        "tox.ini",
        ".coveragerc",
        "codecov.yml",
        ".gitleaks.toml",
        ".eslintrc.json",
        "frontend/vitest.config.ts",
        "jest.config.js",
        "playwright.config.ts",
        ".pre-commit-config.yaml",
        ".github/CODEOWNERS",
        ".github/dependabot.yml",
    ],
)
def test_denylist_blocks_every_ci_defining_file(path):
    pr = make_pr(files=[ChangedFile(path=path)])
    result = guards.guard_no_denylisted_paths(pr)
    assert not result.passed
    assert path in result.detail


def test_pr_that_also_edits_pytest_ini_is_refused(baseline):
    """Hostile fixture: a real bump smuggling a test-config change alongside it."""
    pr = make_pr(
        files=[
            ChangedFile(path="requirements.txt", patch="@@\n-pytest==8.0.0\n+pytest==8.1.0\n"),
            ChangedFile(
                path="pytest.ini", patch="@@\n-addopts = --cov-fail-under=80\n+addopts =\n"
            ),
        ]
    )
    results = guards.run_all_guards(pr, baseline, assessed_sha=pr.head_sha)
    failed = {r.guard_id for r in guards.failures(results)}
    assert not guards.may_merge(results)
    assert "G03" in failed


# ---------------------------------------------------------------------------
# G04 — workflow diffs restricted to version pins
# ---------------------------------------------------------------------------


def test_accepts_a_plain_version_pin_bump():
    patch = (
        "@@ -20,7 +20,7 @@\n"
        "-        uses: actions/setup-python@v6\n"
        "+        uses: actions/setup-python@v7\n"
    )
    assert guards.guard_workflow_pins_only(workflow_pr(patch)).passed


def test_accepts_sha_pinned_actions_with_trailing_version_comment():
    patch = (
        "@@ -8,7 +8,7 @@\n"
        "-      - uses: actions/checkout@8ade135a41bc03ea155e62e844d188df1ea18608 # v4.1.0\n"
        "+      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2\n"
    )
    assert guards.guard_workflow_pins_only(workflow_pr(patch)).passed


def test_rejects_a_workflow_diff_that_disables_a_step():
    """Hostile fixture: the exact 'just turn the check off' move."""
    patch = (
        "@@ -30,6 +30,7 @@\n"
        "-        uses: actions/setup-node@v4\n"
        "+        uses: actions/setup-node@v5\n"
        "+        continue-on-error: true\n"
    )
    result = guards.guard_workflow_pins_only(workflow_pr(patch))
    assert not result.passed
    assert "non-pin changes" in result.detail


def test_rejects_a_workflow_diff_that_deletes_a_job():
    patch = "@@ -40,10 +40,2 @@\n-  cve-frontend:\n-    uses: ./.github/workflows/cve.yml\n"
    assert not guards.guard_workflow_pins_only(workflow_pr(patch)).passed


def test_rejects_workflow_change_with_no_patch_available():
    """Absent evidence is not evidence of safety."""
    result = guards.guard_workflow_pins_only(workflow_pr(""))
    assert not result.passed
    assert "no patch available" in result.detail


def test_ignores_blank_line_churn_in_workflow_diffs():
    patch = "@@ -20,7 +20,7 @@\n-        uses: actions/cache@v3\n+        uses: actions/cache@v4\n-\n+\n"
    assert guards.guard_workflow_pins_only(workflow_pr(patch)).passed


def test_non_workflow_files_are_not_subject_to_the_pin_rule(pr):
    assert guards.guard_workflow_pins_only(pr).passed


# ---------------------------------------------------------------------------
# G05 — forbidden patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "  continue-on-error: true",
        "    if: false",
        "  run: npm audit --audit-level=critical || true",
        "  run: pytest --cov-fail-under=50",
        "        fail_ci_if_error: false",
        "  it.only('renders', () => {})",
        "  describe.skip('auth', () => {})",
        "@pytest.mark.skip(reason='flaky')",
        "@pytest.mark.xfail",
        "// eslint-disable-next-line no-unused-vars",
        "value = thing  # type: ignore",
        "  run: npm ci --legacy-peer-deps",
        "  run: npm install --force",
        "  run: SKIP=gitleaks pre-commit run",
        "  run: git commit --no-verify -m x",
    ],
)
def test_every_check_weakening_token_is_caught(line):
    pr = make_pr(files=[ChangedFile(path="package.json", patch=f"@@\n+{line}\n")])
    result = guards.guard_no_forbidden_patterns(pr)
    assert not result.passed, line


def test_forbidden_tokens_inside_generated_lockfiles_are_ignored():
    """Upstream package metadata is not something this repo authored."""
    pr = make_pr(
        files=[
            ChangedFile(
                path="package-lock.json",
                patch='@@\n+      "description": "run with --legacy-peer-deps for old npm"\n',
            )
        ]
    )
    assert guards.guard_no_forbidden_patterns(pr).passed


def test_removed_lines_do_not_trigger_the_scan():
    """Deleting a `continue-on-error` is a strengthening, not a weakening."""
    pr = make_pr(
        files=[
            ChangedFile(path=".github/workflows/ci.yml", patch="@@\n-  continue-on-error: true\n")
        ]
    )
    assert guards.guard_no_forbidden_patterns(pr).passed


def test_pr_lowering_coverage_threshold_is_refused(baseline):
    """Hostile fixture from the plan."""
    pr = make_pr(
        files=[
            ChangedFile(
                path="pyproject.toml",
                patch="@@\n-addopts = --cov-fail-under=80\n+addopts = --cov-fail-under=40\n",
            )
        ]
    )
    results = guards.run_all_guards(pr, baseline, assessed_sha=pr.head_sha)
    assert not guards.may_merge(results)
    assert "G05" in {r.guard_id for r in guards.failures(results)}


# ---------------------------------------------------------------------------
# G06 — required checks
# ---------------------------------------------------------------------------


def test_red_required_check_blocks_merge(baseline):
    checks = green_checks()[:-1] + [
        CheckRun(name="secret-scan / Secret scan (gitleaks)", conclusion="FAILURE")
    ]
    result = guards.guard_required_checks_green(make_pr(checks=checks), baseline)
    assert not result.passed
    assert "secret-scan" in result.detail


def test_in_progress_required_check_blocks_merge(baseline):
    checks = green_checks()[:-1] + [
        CheckRun(name="secret-scan / Secret scan (gitleaks)", conclusion="", status="IN_PROGRESS")
    ]
    assert not guards.guard_required_checks_green(make_pr(checks=checks), baseline).passed


def test_missing_required_check_is_treated_as_failing(baseline):
    """A check that never ran must never read as a passing check."""
    result = guards.guard_required_checks_green(make_pr(checks=green_checks()[:-1]), baseline)
    assert not result.passed


def test_repo_with_no_required_checks_cannot_auto_merge():
    """BC-Arcade's `dev` today: green CI proves nothing, so refuse."""
    empty = make_baseline(required_contexts=frozenset())
    result = guards.guard_required_checks_green(make_pr(), empty)
    assert not result.passed
    assert "proves nothing" in result.detail


def test_extra_non_required_failures_do_not_block(baseline):
    checks = green_checks() + [CheckRun(name="perf (optional)", conclusion="FAILURE")]
    assert guards.guard_required_checks_green(make_pr(checks=checks), baseline).passed


def test_skipped_and_neutral_count_as_success(baseline):
    checks = [
        CheckRun(name=n, conclusion=c)
        for n, c in zip(sorted(REQUIRED), ["SUCCESS", "SKIPPED", "NEUTRAL"], strict=True)
    ]
    assert guards.guard_required_checks_green(make_pr(checks=checks), baseline).passed


# ---------------------------------------------------------------------------
# G07 — branch-protection tamper detection
# ---------------------------------------------------------------------------


def test_removing_a_required_context_mid_run_is_detected(baseline):
    weakened = frozenset(list(REQUIRED)[:-1])
    result = guards.guard_protection_unchanged(baseline, weakened)
    assert not result.passed
    assert "removed since run start" in result.detail


def test_adding_a_required_context_is_allowed(baseline):
    strengthened = REQUIRED | {"new-check"}
    assert guards.guard_protection_unchanged(baseline, strengthened).passed


# ---------------------------------------------------------------------------
# G08 — head SHA stability
# ---------------------------------------------------------------------------


def test_head_moving_after_assessment_blocks_merge(pr):
    assert not guards.guard_head_sha_unchanged(pr, "b" * 40).passed


def test_missing_assessed_sha_blocks_merge(pr):
    assert not guards.guard_head_sha_unchanged(pr, "").passed


# ---------------------------------------------------------------------------
# G09 — threshold ratchet
# ---------------------------------------------------------------------------


def test_lowered_threshold_is_refused(baseline):
    result = guards.guard_thresholds_not_lowered(baseline, {"cov_fail_under": 60.0})
    assert not result.passed
    assert "80.0 -> 60.0" in result.detail


@pytest.mark.parametrize("value", [80.0, 85.0])
def test_holding_or_raising_a_threshold_is_allowed(baseline, value):
    assert guards.guard_thresholds_not_lowered(baseline, {"cov_fail_under": value}).passed


def test_new_threshold_key_is_not_treated_as_a_regression(baseline):
    assert guards.guard_thresholds_not_lowered(baseline, {"brand_new": 10.0}).passed


# ---------------------------------------------------------------------------
# G10 / G11 — mergeability and kill switch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mergeable,state",
    [("CONFLICTING", "DIRTY"), ("MERGEABLE", "BEHIND"), ("MERGEABLE", "BLOCKED")],
)
def test_unmergeable_states_are_refused(mergeable, state):
    pr = make_pr(mergeable=mergeable, merge_state_status=state)
    assert not guards.guard_mergeable(pr).passed


def test_unstable_is_acceptable_because_required_checks_are_judged_separately():
    assert guards.guard_mergeable(make_pr(merge_state_status="UNSTABLE")).passed


def test_hold_label_stops_a_perfectly_good_pr(baseline):
    pr = make_pr(labels=["dependencies", "dependabot-triage:hold"])
    results = guards.run_all_guards(pr, baseline, assessed_sha=pr.head_sha)
    assert not guards.may_merge(results)
    assert "G11" in {r.guard_id for r in guards.failures(results)}


# ---------------------------------------------------------------------------
# Threshold extraction
# ---------------------------------------------------------------------------


def test_extracts_coverage_and_audit_levels_from_ci_config():
    text = """
    - run: pytest --cov=. --cov-fail-under=80
      coverage-threshold: 85
    - run: npm audit --audit-level=high
    """
    found = guards.extract_thresholds(text)
    assert found["cov_fail_under"] == 80.0
    assert found["coverage_threshold"] == 85.0
    assert found["audit_level"] == float(guards.AUDIT_LEVELS.index("high"))


def test_strictest_value_wins_when_a_key_appears_twice():
    """A loosened duplicate later in the file must not mask the stricter one."""
    found = guards.extract_thresholds("--cov-fail-under=90\n--cov-fail-under=40")
    assert found["cov_fail_under"] == 90.0


def test_extraction_returns_empty_when_nothing_is_configured():
    assert guards.extract_thresholds("name: CI\non: push\n") == {}


def test_workflow_pin_changes_summarises_bumped_actions():
    patch = "@@\n-        uses: actions/setup-python@v6\n+        uses: actions/setup-python@v7\n"
    assert guards.workflow_pin_changes(workflow_pr(patch).files) == ["actions/setup-python@v7"]


def test_workflow_pin_changes_deduplicates_an_action_pinned_in_several_jobs():
    """RulersAI#663 bumps setup-python in two jobs; the comment should say it once."""
    patch = (
        "@@ -20,7 +20,7 @@\n"
        "-        uses: actions/setup-python@v6\n"
        "+        uses: actions/setup-python@v7\n"
        "@@ -55,7 +55,7 @@\n"
        "-        uses: actions/setup-python@v6\n"
        "+        uses: actions/setup-python@v7\n"
    )
    assert guards.workflow_pin_changes(workflow_pr(patch).files) == ["actions/setup-python@v7"]


def test_workflow_pin_changes_ignores_non_workflow_files():
    files = [
        ChangedFile(path="package.json", patch="@@\n+        uses: not/a-workflow@v1\n"),
        ChangedFile(path=".github/workflows/ci.yml", patch="@@\n+        uses: actions/cache@v4\n"),
    ]
    assert guards.workflow_pin_changes(files) == ["actions/cache@v4"]


# ---------------------------------------------------------------------------
# Path matching helper
# ---------------------------------------------------------------------------


def test_double_star_pattern_also_matches_at_the_repository_root():
    """`**/x` must cover a root-level `x`, so monorepo and flat layouts share one rule."""
    assert guards._matches_any("Podfile.lock", ("**/Podfile.lock",))
    assert guards._matches_any("ios/Podfile.lock", ("**/Podfile.lock",))
    assert not guards._matches_any("Podfile", ("**/Podfile.lock",))


# ---------------------------------------------------------------------------
# Aggregate behaviour
# ---------------------------------------------------------------------------


def test_run_all_guards_reports_every_failure_not_just_the_first(baseline):
    pr = make_pr(
        author="someone-else",
        labels=["dependabot-triage:hold"],
        files=[ChangedFile(path="src/main.py")],
        merge_state_status="BEHIND",
    )
    failed = {r.guard_id for r in guards.failures(guards.run_all_guards(pr, baseline, "x" * 40))}
    assert {"G01", "G02", "G08", "G10", "G11"} <= failed


def test_baseline_thresholds_are_used_when_current_not_supplied(pr, baseline):
    """Omitting live thresholds must not silently pass the ratchet check."""
    results = guards.run_all_guards(pr, baseline, assessed_sha=pr.head_sha)
    assert next(r for r in results if r.guard_id == "G09").passed
