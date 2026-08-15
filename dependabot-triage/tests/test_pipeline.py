"""Tests for the phases around the guard boundary.

Nothing here reaches the network. The pieces that would — GitHub calls and model
calls — are isolated behind :mod:`gh` and :mod:`llm`, so everything below is a
pure function over fixtures.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_baseline, make_pr

import budget as budget_mod
import execute as execute_mod
import harvest as harvest_mod
import metrics as metrics_mod
import report as report_mod
from models import Action, Assessment, Decision, GuardResult, RiskTier

NOW = datetime(2026, 8, 15, 6, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# harvest — diff splitting and title parsing
# ---------------------------------------------------------------------------

MULTI_FILE_DIFF = """diff --git a/package.json b/package.json
index 111..222 100644
--- a/package.json
+++ b/package.json
@@ -10,7 +10,7 @@
-    "vite": "5.0.0"
+    "vite": "5.1.0"
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index 333..444 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -5,7 +5,7 @@
-        uses: actions/checkout@v4
+        uses: actions/checkout@v5
"""


def test_split_diff_separates_each_file():
    patches = harvest_mod.split_diff(MULTI_FILE_DIFF)
    assert set(patches) == {"package.json", ".github/workflows/ci.yml"}
    assert '+    "vite": "5.1.0"' in patches["package.json"]
    assert "actions/checkout@v5" in patches[".github/workflows/ci.yml"]
    assert "vite" not in patches[".github/workflows/ci.yml"]


def test_split_diff_uses_the_destination_path_for_renames():
    diff = "diff --git a/old.txt b/new.txt\n@@\n+x\n"
    assert set(harvest_mod.split_diff(diff)) == {"new.txt"}


def test_split_diff_of_empty_input_is_empty():
    assert harvest_mod.split_diff("") == {}


@pytest.mark.parametrize(
    "title,expected",
    [
        ("chore(deps): bump lodash from 4.17.20 to 4.17.21", ("lodash", "4.17.20", "4.17.21")),
        ("Bump actions/setup-python from 6 to 7", ("actions/setup-python", "6", "7")),
        ("chore(deps-dev): bump vite from 5.0.0 to 6.0.0", ("vite", "5.0.0", "6.0.0")),
        ("Update the expo group", ("", "", "")),
    ],
)
def test_parse_bump_handles_dependabot_title_formats(title, expected):
    assert harvest_mod.parse_bump(title) == expected


@pytest.mark.parametrize(
    "labels,paths,expected",
    [
        (["javascript"], [], "npm"),
        (["python"], [], "pip"),
        (["github_actions"], [], "github-actions"),
        ([], [".github/workflows/ci.yml"], "github-actions"),
        ([], ["frontend/package.json"], "npm"),
        ([], ["backend/requirements.txt"], "pip"),
        ([], ["something/else"], "unknown"),
    ],
)
def test_ecosystem_detection_prefers_labels_then_paths(labels, paths, expected):
    assert harvest_mod.detect_ecosystem(labels, paths) == expected


@pytest.mark.parametrize(
    "raw,conclusion,status",
    [
        ({"bucket": "pass", "state": "SUCCESS"}, "SUCCESS", "COMPLETED"),
        ({"bucket": "fail", "state": "FAILURE"}, "FAILURE", "COMPLETED"),
        ({"bucket": "skipping", "state": ""}, "SKIPPED", "COMPLETED"),
        ({"bucket": "pending", "state": ""}, "", "IN_PROGRESS"),
    ],
)
def test_check_buckets_map_onto_the_checkrun_vocabulary(raw, conclusion, status):
    assert harvest_mod._bucket_to_conclusion(raw) == (conclusion, status)


# ---------------------------------------------------------------------------
# budget — pricing and ceilings
# ---------------------------------------------------------------------------


def test_price_uses_published_rates():
    spend = budget_mod.price(
        "claude-sonnet-5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    )
    assert spend.usd == pytest.approx(3.00 + 15.00)


def test_cache_reads_are_charged_at_a_tenth_of_input():
    spend = budget_mod.price("claude-sonnet-5", {"cache_read_input_tokens": 1_000_000})
    assert spend.usd == pytest.approx(0.30)


def test_cache_writes_carry_a_premium():
    spend = budget_mod.price("claude-sonnet-5", {"cache_creation_input_tokens": 1_000_000})
    assert spend.usd == pytest.approx(3.75)


def test_unknown_model_is_charged_at_the_highest_rate_not_zero():
    """A model-id typo must be alarming, not free."""
    spend = budget_mod.price("claude-typo-9", {"input_tokens": 1_000_000})
    assert spend.usd > 0


def test_per_run_ceiling_stops_further_calls():
    b = budget_mod.Budget(max_per_run=0.01)
    b.record("assess", "claude-sonnet-5", {"input_tokens": 1_000_000})
    with pytest.raises(budget_mod.BudgetExceeded, match="per-run"):
        b.check_before_call()


def test_monthly_ceiling_accounts_for_prior_runs():
    b = budget_mod.Budget(max_per_run=100.0, max_per_month=5.0, month_to_date=4.99)
    b.record("assess", "claude-haiku-4-5", {"input_tokens": 100_000})
    with pytest.raises(budget_mod.BudgetExceeded, match="monthly"):
        b.check_before_call()


def test_budget_under_both_ceilings_permits_the_call():
    b = budget_mod.Budget(max_per_run=1.0, max_per_month=20.0)
    b.record("assess", "claude-haiku-4-5", {"input_tokens": 1000})
    b.check_before_call()  # must not raise


def test_spend_is_attributed_per_phase():
    b = budget_mod.Budget()
    b.record("assess", "claude-sonnet-5", {"input_tokens": 1000})
    b.record("adversary", "claude-haiku-4-5", {"input_tokens": 1000})
    b.record("adversary", "claude-haiku-4-5", {"input_tokens": 1000})
    summary = b.summary()
    assert summary["by_phase"]["adversary"]["calls"] == 2
    assert summary["calls"] == 3


def test_should_chunk_only_above_the_ceiling():
    assert budget_mod.should_chunk(150_001, 150_000)
    assert not budget_mod.should_chunk(150_000, 150_000)


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def _decision(**kw) -> Decision:
    defaults = {
        "repo": "BC-Arcade",
        "number": 1,
        "title": "bump x",
        "tier": RiskTier.LOW,
        "action": Action.MERGE,
        "reason": "ok",
        "merged": True,
    }
    defaults.update(kw)
    return Decision(**defaults)


def test_autonomy_rate_is_merged_over_eligible():
    decisions = [
        _decision(number=1),
        _decision(number=2),
        _decision(number=3, tier=RiskTier.HIGH, action=Action.COMMENT, merged=False),
    ]
    # Rates are stored rounded to 4 dp; they are displayed as whole percentages.
    assert metrics_mod.summarise(decisions)["autonomy_rate"] == pytest.approx(2 / 3, abs=1e-4)


def test_deferred_prs_do_not_count_against_the_autonomy_rate():
    """Work the agent never got to is not work it declined to do."""
    decisions = [
        _decision(number=1),
        _decision(number=2, action=Action.COMMENT, merged=False, deferred=True),
    ]
    stats = metrics_mod.summarise(decisions)
    assert stats["eligible"] == 1
    assert stats["autonomy_rate"] == 1.0


def test_summarise_attributes_skips_to_the_rubric_question():
    decisions = [
        _decision(
            number=1,
            tier=RiskTier.HIGH,
            action=Action.COMMENT,
            merged=False,
            deciding_question="Q5_peer_coupling",
        ),
        _decision(
            number=2,
            tier=RiskTier.HIGH,
            action=Action.COMMENT,
            merged=False,
            deciding_question="Q5_peer_coupling",
        ),
        _decision(
            number=3,
            tier=RiskTier.MEDIUM,
            action=Action.COMMENT,
            merged=False,
            deciding_question="Q1_semver",
        ),
    ]
    counts = metrics_mod.summarise(decisions)["by_deciding_question"]
    assert counts == {"Q5_peer_coupling": 2, "Q1_semver": 1}


def test_summarise_attributes_refusals_to_the_guard_that_fired():
    decision = _decision(
        action=Action.SKIP,
        merged=False,
        guard_results=[
            GuardResult("G01", True, ""),
            GuardResult("G03", False, "touches pytest.ini"),
        ],
    )
    assert metrics_mod.summarise([decision])["by_failed_guard"] == {"G03": 1}


def test_summarise_of_an_empty_run_reports_no_rate():
    stats = metrics_mod.summarise([])
    assert stats["seen"] == 0
    assert stats["autonomy_rate"] is None


def test_history_round_trips_through_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics_mod, "HISTORY_DIR", tmp_path)
    metrics_mod.record_run([_decision()], {"run_usd": 0.22}, run_id="r1", now=NOW, dry_run=False)
    entries = metrics_mod.load_history()
    assert [e["type"] for e in entries] == ["decision", "run"]
    assert entries[1]["budget"]["run_usd"] == 0.22


def test_rolling_window_excludes_dry_runs(tmp_path, monkeypatch):
    """A dry run describes what would have happened; counting it overstates the rate."""
    monkeypatch.setattr(metrics_mod, "HISTORY_DIR", tmp_path)
    metrics_mod.record_run([_decision()], {}, run_id="dry", now=NOW, dry_run=True)
    assert metrics_mod.rolling(now=NOW + timedelta(hours=1))["decisions"] == 0

    metrics_mod.record_run([_decision(number=2)], {}, run_id="live", now=NOW, dry_run=False)
    assert metrics_mod.rolling(now=NOW + timedelta(hours=1))["decisions"] == 1


def test_rolling_window_excludes_entries_older_than_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics_mod, "HISTORY_DIR", tmp_path)
    metrics_mod.record_run(
        [_decision()], {}, run_id="old", now=NOW - timedelta(days=60), dry_run=False
    )
    assert metrics_mod.rolling(days=30, now=NOW)["decisions"] == 0


def test_precision_falls_when_a_regression_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics_mod, "HISTORY_DIR", tmp_path)
    metrics_mod.record_run(
        [_decision(number=n) for n in (1, 2, 3, 4)], {}, run_id="r", now=NOW, dry_run=False
    )
    assert metrics_mod.precision(now=NOW + timedelta(hours=1))["precision"] == 1.0

    metrics_mod.record_regression("BC-Arcade", 1, "r", "base branch went red", NOW)
    assert metrics_mod.precision(now=NOW + timedelta(hours=1))["precision"] == 0.75


def test_month_to_date_spend_sums_live_runs_only(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics_mod, "HISTORY_DIR", tmp_path)
    metrics_mod.record_run([], {"run_usd": 0.20}, run_id="a", now=NOW, dry_run=False)
    metrics_mod.record_run([], {"run_usd": 0.30}, run_id="b", now=NOW, dry_run=False)
    metrics_mod.record_run([], {"run_usd": 9.99}, run_id="c", now=NOW, dry_run=True)
    assert metrics_mod.month_to_date_spend(NOW) == pytest.approx(0.50)


def test_malformed_history_lines_are_skipped_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics_mod, "HISTORY_DIR", tmp_path)
    (tmp_path / "2026-08.jsonl").write_text('{"type":"run"}\nnot json\n', encoding="utf-8")
    assert len(metrics_mod.load_history()) == 1


# ---------------------------------------------------------------------------
# execute — helpers that decide comment text and drift
# ---------------------------------------------------------------------------


def test_merge_comment_states_the_agent_cannot_edit_files():
    body = execute_mod._comment_body(_decision(), "https://example/run/1")
    assert "Merged automatically" in body
    assert "never edits a file" not in body  # wording lives in the email
    assert "may only comment" in body


def test_refusal_comment_names_every_failed_guard():
    decision = _decision(
        action=Action.SKIP,
        merged=False,
        guard_results=[
            GuardResult("G03", False, "touches pytest.ini"),
            GuardResult("G06", False, "required checks not green"),
        ],
    )
    body = execute_mod._comment_body(decision, "https://example/run/1")
    assert "`G03`" in body and "`G06`" in body
    assert "touches pytest.ini" in body


def test_comment_includes_the_deciding_rubric_question():
    decision = _decision(action=Action.COMMENT, merged=False, deciding_question="Q5_peer_coupling")
    assert "Q5_peer_coupling" in execute_mod._comment_body(decision, "")


def test_package_drift_detected_when_rebase_changes_the_target_version():
    assessment = Assessment("r", 1, RiskTier.LOW, 0, "", packages=["lodash@4.17.21"])
    same = make_pr(title="bump lodash from 4.17.20 to 4.17.21")
    moved = make_pr(title="bump lodash from 4.17.20 to 4.17.22")
    assert not execute_mod._packages_drifted(assessment, same)
    assert execute_mod._packages_drifted(assessment, moved)


def test_no_drift_reported_when_the_assessment_listed_no_packages():
    assessment = Assessment("r", 1, RiskTier.LOW, 0, "", packages=[])
    assert not execute_mod._packages_drifted(assessment, make_pr())


def test_checks_settled_requires_every_required_context():
    from conftest import REQUIRED, green_checks

    from models import CheckRun

    assert execute_mod._checks_settled(make_pr(checks=green_checks()), REQUIRED)

    running = green_checks()[:-1] + [
        CheckRun(name=sorted(REQUIRED)[-1], conclusion="", status="IN_PROGRESS")
    ]
    assert not execute_mod._checks_settled(make_pr(checks=running), REQUIRED)


# ---------------------------------------------------------------------------
# report — rendering is deterministic
# ---------------------------------------------------------------------------


def _render(decisions, **kw):
    defaults = {
        "summary": "Summary text.",
        "decisions": decisions,
        "stats": metrics_mod.summarise(decisions),
        "rolling": {"window_days": 30, "autonomy_rate": 0.71},
        "precision": {"precision": 1.0},
        "budget": {"run_usd": 0.22, "month_usd": 4.8, "max_per_month": 20.0},
        "run_url": "https://example/run/1",
        "when": NOW,
        "dry_run": False,
    }
    defaults.update(kw)
    return report_mod.render_html(**defaults)


def test_email_groups_every_repo_into_one_message():
    """The whole stack lands in a single email, not one per repo."""
    decisions = [
        _decision(repo="BC-Arcade", number=1),
        _decision(repo="buffingchi_site", number=2),
        _decision(repo="RulersAI", number=3, action=Action.COMMENT, merged=False),
    ]
    body = _render(decisions)
    for repo in ("BC-Arcade", "buffingchi_site", "RulersAI"):
        assert repo in body


def test_email_shows_autonomy_rate_and_precision():
    body = _render([_decision()])
    assert "71%" in body
    assert "100%" in body


def test_dry_run_is_labelled_so_it_cannot_be_mistaken_for_a_live_run():
    assert "Dry run" in _render([_decision()], dry_run=True)


def test_aborted_run_leads_with_the_abort_banner():
    body = _render([], aborted="monthly ceiling reached")
    assert "Run aborted" in body
    assert "monthly ceiling reached" in body


def test_titles_are_html_escaped():
    body = _render([_decision(title="<script>alert(1)</script>")])
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_empty_run_renders_without_error():
    assert "No open Dependabot pull requests" in _render([])


def test_subject_line_summarises_the_night():
    stats = metrics_mod.summarise(
        [_decision(), _decision(number=2, merged=False, action=Action.COMMENT)]
    )
    config = {"report": {"subject_prefix": "Dependabot Triage"}}
    assert report_mod.subject_line(stats, config, NOW) == (
        "Dependabot Triage — 1 merged, 1 pending — Sat 15 Aug"
    )


def test_aborted_subject_is_unmistakable():
    config = {"report": {"subject_prefix": "Dependabot Triage"}}
    subject = report_mod.subject_line({"merged": 0, "seen": 0}, config, NOW, aborted="budget")
    assert "ABORTED" in subject


def test_summary_falls_back_when_no_client_is_available():
    assert report_mod.compose_summary({}, None, {}) == "Nightly Dependabot triage completed."


def test_summary_reports_the_abort_reason_without_calling_a_model():
    text = report_mod.compose_summary({}, None, {}, aborted="tamper tripwire fired")
    assert "tamper tripwire fired" in text
    assert "Nothing further was merged" in text


def test_send_is_suppressed_only_by_the_explicit_flag():
    config = {"report": {"from_address": "a@b.c", "to_address": "d@e.f"}}
    assert report_mod.send("<p>x</p>", "subject", config, suppress=True) is False


def test_a_dry_run_still_emails(monkeypatch):
    """A dry run suppresses GitHub mutations, never the report.

    The report changes nothing and is the whole point of running dry; the
    two-week rollout period would produce no signal at all if it were withheld.
    """
    sent = {}
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setattr(
        report_mod.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")),
    )
    config = {"report": {"from_address": "a@b.c", "to_address": "d@e.f"}}
    # suppress defaults to False, so it attempts delivery rather than skipping.
    try:
        report_mod.send("<p>x</p>", "subject", config)
    except AssertionError as exc:
        sent["attempted"] = str(exc) == "network"
    assert sent.get("attempted"), "send() should attempt delivery when not explicitly suppressed"


def test_dry_run_subject_is_unmistakable():
    config = {"report": {"subject_prefix": "Dependabot Triage"}}
    stats = {"merged": 3, "seen": 5}
    live = report_mod.subject_line(stats, config, NOW)
    dry = report_mod.subject_line(stats, config, NOW, dry_run=True)
    assert dry.startswith("[DRY RUN]")
    assert "would merge" in dry
    assert "[DRY RUN]" not in live and "3 merged" in live


def test_ledger_shape_is_json_serialisable():
    decisions = [_decision(guard_results=[GuardResult("G01", True, "ok")])]
    payload = {
        "decisions": [
            {"repo": d.repo, "number": d.number, "action": d.action.value} for d in decisions
        ]
    }
    assert json.loads(json.dumps(payload))["decisions"][0]["action"] == "MERGE"


# ---------------------------------------------------------------------------
# Permission errors must never look like "nothing is configured"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "gh api failed (1): Resource not accessible by personal access token",
        "gh api failed (1): Must have admin rights to Repository.",
        "gh api failed (1): HTTP 403: Forbidden",
        "gh api failed (1): Resource not accessible by integration",
    ],
)
def test_permission_errors_are_recognised(message):
    import gh

    assert gh._is_permission_error(gh.GhError(message))


@pytest.mark.parametrize(
    "message",
    [
        "gh api failed (1): Branch not protected",
        "gh api failed (1): HTTP 404: Not Found",
    ],
)
def test_genuine_absence_is_not_a_permission_error(message):
    """A repo with no protection is a real, valid state — not an error."""
    import gh

    assert not gh._is_permission_error(gh.GhError(message))


def test_unreadable_protection_is_a_distinct_exception_type():
    """So the orchestrator can abort on it specifically, rather than guessing."""
    import gh

    assert issubclass(gh.ProtectionUnreadable, gh.GhError)


# ---------------------------------------------------------------------------
# The autonomy rate must measure the agent, not repo health
# ---------------------------------------------------------------------------


def test_already_red_prs_are_excluded_from_the_denominator():
    """A PR whose CI was red before the run was never mergeable by anyone.

    The first full run merged 3 of 18, which read as a 17% autonomy rate. Six of
    those PRs had failing required checks before the run started and eight were
    majors that are never auto-merged by policy — the genuinely eligible pool was
    four. Counting the unmergeable ones makes the metric track repo health.
    """
    decisions = [
        _decision(number=1),
        _decision(number=2, merged=False, action=Action.COMMENT, pre_existing_red=True),
        _decision(number=3, merged=False, action=Action.COMMENT, pre_existing_red=True),
    ]
    stats = metrics_mod.summarise(decisions)
    assert stats["pre_existing_red"] == 2
    assert stats["eligible"] == 1
    assert stats["autonomy_rate"] == 1.0


def test_rolling_window_also_excludes_already_red(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics_mod, "HISTORY_DIR", tmp_path)
    metrics_mod.record_run(
        [
            _decision(number=1),
            _decision(number=2, merged=False, action=Action.COMMENT, pre_existing_red=True),
        ],
        {},
        run_id="r",
        now=NOW,
        dry_run=False,
    )
    assert metrics_mod.rolling(now=NOW + timedelta(hours=1))["autonomy_rate"] == 1.0


def test_corpus_states_todays_date_for_recency_questions():
    """Q13 asks how fresh a release is; without a date the model guesses."""
    import assess as assess_mod
    from harvest import RepoHarvest

    h = RepoHarvest(
        repo="r", base="dev", merge_method="squash", baseline=make_baseline(), prs=[make_pr()]
    )
    assert "Today's date is 2026-03-04." in assess_mod.build_corpus([h], today="2026-03-04")
