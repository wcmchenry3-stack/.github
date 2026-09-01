"""Tests for fetch_log_tail's run selection.

The bug this guards against: BookshelfAI#437 failed `npm ci` in its
`lint-frontend` job with a textbook-recoverable lockfile-desync error, but the
same push also triggered several other independent workflows (doc-reminder,
design-token-check, openai-policy-check, ...). `fetch_log_tail` used to ask for
"the most recent run on this branch", which landed on one of those unrelated
green runs and returned an empty log — so remediate.should_recreate saw "no
log available" and never asked Dependabot to rebuild, even though the failure
was exactly the case that feature exists for.
"""

from __future__ import annotations

import diagnose as diagnose_mod
import gh
from models import CheckRun


def _check(details_url: str = "", conclusion: str = "FAILURE") -> CheckRun:
    return CheckRun(name="lint-frontend", conclusion=conclusion, details_url=details_url)


# ---------------------------------------------------------------------------
# _run_id_from_checks
# ---------------------------------------------------------------------------


def test_run_id_is_read_straight_off_the_failing_checks_link():
    checks = [_check("https://github.com/acme/widget/actions/runs/33412408733/job/99554893897")]
    assert diagnose_mod._run_id_from_checks(checks) == "33412408733"


def test_first_check_without_a_parseable_link_is_skipped_not_fatal():
    checks = [
        _check(""),  # e.g. a synthetic MISSING check
        _check("https://github.com/acme/widget/actions/runs/42/job/1"),
    ]
    assert diagnose_mod._run_id_from_checks(checks) == "42"


def test_no_usable_link_anywhere_returns_empty():
    assert diagnose_mod._run_id_from_checks([_check(""), _check("not a url")]) == ""


def test_empty_check_list_returns_empty():
    assert diagnose_mod._run_id_from_checks([]) == ""


# ---------------------------------------------------------------------------
# fetch_log_tail
# ---------------------------------------------------------------------------


def test_fetch_log_tail_reads_the_run_named_by_the_failing_check(monkeypatch):
    """The BookshelfAI#437 case: skip run-list entirely, view the named run."""
    calls: list[list[str]] = []

    def fake_run(args, timeout=120):
        calls.append(args)
        assert args[:3] == ["run", "view", "33412408733"]
        return "npm error code EUSAGE\nMissing: react-native-worklets@0.8.3 from lock file\n"

    monkeypatch.setattr(gh, "_run", fake_run)

    checks = [_check("https://github.com/acme/widget/actions/runs/33412408733/job/99554893897")]
    text = diagnose_mod.fetch_log_tail("widget", "acme", 437, "dependabot/npm/foo", checks)

    assert "Missing: react-native-worklets@0.8.3 from lock file" in text
    # Never fell back to listing runs on the branch.
    assert all(call[:2] != ["run", "list"] for call in calls)


def test_fetch_log_tail_falls_back_to_branch_lookup_without_a_usable_link(monkeypatch):
    def fake_run(args, timeout=120):
        if args[:2] == ["run", "list"]:
            assert "--branch" in args and "dependabot/npm/foo" in args
            return '[{"databaseId": 999}]'
        assert args[:3] == ["run", "view", "999"]
        return "some log tail"

    monkeypatch.setattr(gh, "_run", fake_run)

    text = diagnose_mod.fetch_log_tail("widget", "acme", 437, "dependabot/npm/foo", [_check("")])
    assert text == "some log tail"


def test_fetch_log_tail_returns_empty_without_head_ref_or_link():
    assert diagnose_mod.fetch_log_tail("widget", "acme", 437, "", [_check("")]) == ""


def test_fetch_log_tail_is_best_effort_on_a_gh_error(monkeypatch):
    checks = [_check("https://github.com/acme/widget/actions/runs/1/job/2")]

    def fake_run(args, timeout=120):
        raise gh.GhError("boom")

    monkeypatch.setattr(gh, "_run", fake_run)
    assert diagnose_mod.fetch_log_tail("widget", "acme", 437, "branch", checks) == ""
