"""Tests for the repeat-detection phase between harvest and assess.

A repeat is only ever a *previous, unchanged, non-deferred COMMENT verdict* —
everything else (a new commit, a first-time PR, a deferred decision, or a
guard-blocked SKIP) must still go through full triage. See dedupe.py for why
SKIP and deferred decisions are excluded on purpose.
"""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import make_baseline, make_pr

import dedupe as dedupe_mod
import metrics as metrics_mod
from harvest import RepoHarvest
from models import Action, Decision, RiskTier

NOW = datetime(2026, 8, 15, 6, 0, tzinfo=UTC)

CONFIG = {"dedupe": {"enabled": True}}


def _record(monkeypatch, tmp_path, **decision_kwargs) -> None:
    monkeypatch.setattr(metrics_mod, "HISTORY_DIR", tmp_path)
    defaults = {
        "repo": "buffingchi_site",
        "number": 101,
        "title": "chore(deps): bump lodash from 4.17.20 to 4.17.21",
        "tier": RiskTier.HIGH,
        "action": Action.COMMENT,
        "reason": "Major version bump, needs a human look.",
        "head_sha": "a" * 40,
    }
    defaults.update(decision_kwargs)
    metrics_mod.record_run(
        [Decision(**defaults)], {}, run_id="r1", now=NOW, dry_run=True
    )


def _harvest(pr) -> RepoHarvest:
    return RepoHarvest(
        repo="buffingchi_site", base="dev", merge_method="squash", baseline=make_baseline(), prs=[pr]
    )


def test_unchanged_comment_verdict_is_deduped_as_a_repeat(tmp_path, monkeypatch):
    _record(monkeypatch, tmp_path, head_sha="a" * 40)
    pr = make_pr(head_sha="a" * 40)

    filtered, repeats = dedupe_mod.split([_harvest(pr)], CONFIG)

    assert filtered[0].dependabot_prs == []
    assert [r.slug for r in repeats] == ["buffingchi_site#101"]
    assert repeats[0].tier is RiskTier.HIGH
    assert repeats[0].reason == "Major version bump, needs a human look."
    assert repeats[0].last_run_id == "r1"
    assert repeats[0].last_seen_at == "2026-08-15"


def test_new_head_sha_is_not_deduped(tmp_path, monkeypatch):
    _record(monkeypatch, tmp_path, head_sha="a" * 40)
    pr = make_pr(head_sha="b" * 40)  # rebased since the last verdict

    filtered, repeats = dedupe_mod.split([_harvest(pr)], CONFIG)

    assert [p.number for p in filtered[0].dependabot_prs] == [101]
    assert repeats == []


def test_pr_with_no_prior_decision_is_not_deduped(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics_mod, "HISTORY_DIR", tmp_path)
    pr = make_pr()

    filtered, repeats = dedupe_mod.split([_harvest(pr)], CONFIG)

    assert [p.number for p in filtered[0].dependabot_prs] == [101]
    assert repeats == []


def test_deferred_decision_is_not_deduped(tmp_path, monkeypatch):
    """A deferred verdict is explicitly meant to be retried, not repeated as final."""
    _record(monkeypatch, tmp_path, head_sha="a" * 40, deferred=True)
    pr = make_pr(head_sha="a" * 40)

    filtered, repeats = dedupe_mod.split([_harvest(pr)], CONFIG)

    assert [p.number for p in filtered[0].dependabot_prs] == [101]
    assert repeats == []


def test_skip_decision_is_not_deduped(tmp_path, monkeypatch):
    """Guard-blocked PRs always retry — their eligibility can change without a new commit."""
    _record(monkeypatch, tmp_path, head_sha="a" * 40, action=Action.SKIP, tier=RiskTier.LOW)
    pr = make_pr(head_sha="a" * 40)

    filtered, repeats = dedupe_mod.split([_harvest(pr)], CONFIG)

    assert [p.number for p in filtered[0].dependabot_prs] == [101]
    assert repeats == []


def test_dedupe_disabled_is_a_pass_through(tmp_path, monkeypatch):
    _record(monkeypatch, tmp_path, head_sha="a" * 40)
    pr = make_pr(head_sha="a" * 40)
    harvests = [_harvest(pr)]

    filtered, repeats = dedupe_mod.split(harvests, {"dedupe": {"enabled": False}})

    assert filtered is harvests
    assert repeats == []


def test_last_decisions_returns_the_most_recent_entry_per_pr(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics_mod, "HISTORY_DIR", tmp_path)
    metrics_mod.record_run(
        [Decision("r", 1, "t", RiskTier.HIGH, Action.COMMENT, "first look", head_sha="a" * 40)],
        {},
        run_id="r1",
        now=NOW,
        dry_run=True,
    )
    metrics_mod.record_run(
        [Decision("r", 1, "t", RiskTier.MEDIUM, Action.COMMENT, "second look", head_sha="b" * 40)],
        {},
        run_id="r2",
        now=datetime(2026, 8, 16, 6, 0, tzinfo=UTC),
        dry_run=True,
    )

    last = metrics_mod.last_decisions()

    assert last["r#1"]["head_sha"] == "b" * 40
    assert last["r#1"]["reason"] == "second look"
    assert last["r#1"]["run_id"] == "r2"
