"""Tests for the decision loop and the assessment plumbing.

GitHub and the model are both replaced with fakes, so these tests assert what the
agent *decides* rather than what any external service does. Running with
``dry_run=True`` keeps the loop deterministic: nothing sleeps, and the drain pass
exits as soon as it stops making progress.
"""

from __future__ import annotations

import pytest
from conftest import REQUIRED, green_checks, make_baseline, make_pr

import assess as assess_mod
import diagnose as diagnose_mod
import execute as execute_mod
from harvest import RepoHarvest
from llm import ModelResponseInvalid
from models import Action, Assessment, CheckRun, RiskTier

CONFIG = {
    "owner": "wcmchenry3-stack",
    "caps": {"max_merges_per_repo": 5, "max_merges_total": 15},
    "timeouts": {
        "rebase_wait": 1200,
        "checks_wait": 2700,
        "poll_interval": 0,
        "global_wall_clock": 14400,
    },
    "hold_label": "dependabot-triage:hold",
    "models": {
        "assess": "claude-sonnet-5",
        "adversary": "claude-haiku-4-5",
        "diagnose": "claude-haiku-4-5",
        "summary": "claude-haiku-4-5",
    },
    "effort": {"assess": "medium", "adversary": "low", "diagnose": "low", "summary": "low"},
    "budget": {
        "assessment_token_ceiling": 150000,
        "max_prs_per_assessment": 8,
        "assessment_max_output_tokens": 32000,
    },
}


class FakeClient:
    """Returns canned responses and records what it was asked."""

    def __init__(
        self,
        *,
        adversary="SAFE — nothing specific found.",
        assessment=None,
        diagnosis="Pre-existing failure.\n\nCONFIDENCE: HIGH",
        tokens=1000,
    ):
        self.adversary = adversary
        self.assessment = assessment or {"assessments": []}
        self.diagnosis = diagnosis
        self.tokens = tokens
        self.calls: list[str] = []

    def count_tokens(self, model, system, prompt):
        return self.tokens

    def complete(
        self,
        *,
        phase,
        model,
        system,
        prompt,
        effort="medium",
        max_tokens=8000,
        cache_system=False,
        schema=None,
    ):
        self.calls.append(phase)
        if phase == "assess":
            return self.assessment, {}
        if phase == "adversary":
            return self.adversary, {}
        if phase.startswith("diagnose"):
            return self.diagnosis, {}
        return "Summary.", {}


@pytest.fixture(autouse=True)
def no_github(monkeypatch):
    """Fail loudly if any test reaches for a real GitHub call."""
    for name in ("comment", "request_rebase", "merge"):
        monkeypatch.setattr(execute_mod.gh, name, _record(name))
    execute_mod.gh._calls = []  # type: ignore[attr-defined]
    yield


def _record(name):
    def inner(repo, owner, number, *args, **kwargs):
        execute_mod.gh._calls.append((name, repo, number, kwargs))  # type: ignore[attr-defined]

    return inner


def _harvest(prs, *, required=REQUIRED, merge_method="squash") -> RepoHarvest:
    return RepoHarvest(
        repo=prs[0].repo if prs else "buffingchi_site",
        base="dev",
        merge_method=merge_method,
        baseline=make_baseline(required_contexts=required),
        prs=prs,
    )


def _low(pr, order=0, packages=None) -> Assessment:
    return Assessment(
        repo=pr.repo,
        number=pr.number,
        tier=RiskTier.LOW,
        merge_order=order,
        rationale="Patch bump to a build-only dependency.",
        deciding_question="NONE",
        packages=packages or [],
    )


def _run(harvests, assessments, client=None):
    return execute_mod.execute(
        harvests, assessments, client or FakeClient(), CONFIG, run_url="u", dry_run=True
    )


# ---------------------------------------------------------------------------
# The merge path
# ---------------------------------------------------------------------------


def test_clean_low_risk_pr_is_merged():
    pr = make_pr()
    result = _run([_harvest([pr])], {pr.slug: _low(pr)})
    assert [d.action for d in result.decisions] == [Action.MERGE]
    assert result.total_merges == 1


def test_medium_risk_pr_is_commented_never_merged():
    pr = make_pr()
    assessment = Assessment(
        pr.repo, pr.number, RiskTier.MEDIUM, 0, "Major version bump.", "Q1_semver"
    )
    result = _run([_harvest([pr])], {pr.slug: assessment})
    assert result.decisions[0].action is Action.COMMENT
    assert result.total_merges == 0
    assert result.decisions[0].deciding_question == "Q1_semver"


@pytest.mark.parametrize("tier", [RiskTier.MEDIUM, RiskTier.HIGH, RiskTier.BLOCKED])
def test_only_low_is_ever_eligible_for_merge(tier):
    pr = make_pr()
    result = _run([_harvest([pr])], {pr.slug: Assessment(pr.repo, pr.number, tier, 0, "no")})
    assert result.total_merges == 0


def test_guard_failure_overrides_a_low_assessment():
    """The model saying LOW is never sufficient on its own."""
    from models import ChangedFile

    pr = make_pr(files=[ChangedFile(path="pytest.ini")])
    result = _run([_harvest([pr])], {pr.slug: _low(pr)})
    decision = result.decisions[0]
    assert decision.action is Action.SKIP
    assert decision.failed_guard in ("G02", "G03")


def test_red_required_check_prevents_merge():
    checks = green_checks()[:-1] + [CheckRun(name=sorted(REQUIRED)[-1], conclusion="FAILURE")]
    pr = make_pr(checks=checks)
    result = _run([_harvest([pr])], {pr.slug: _low(pr)})
    assert result.decisions[0].action is Action.SKIP


def test_repo_without_required_checks_cannot_auto_merge():
    pr = make_pr()
    harvest = _harvest([pr], required=frozenset())
    result = _run([harvest], {pr.slug: _low(pr)})
    assert result.decisions[0].action is Action.SKIP


# ---------------------------------------------------------------------------
# Adversarial review
# ---------------------------------------------------------------------------


def test_adversary_objection_downgrades_a_low_pr():
    pr = make_pr()
    client = FakeClient(adversary="UNSAFE: react-dom is not bumped alongside react")
    result = _run([_harvest([pr])], {pr.slug: _low(pr)}, client)
    decision = result.decisions[0]
    assert decision.action is Action.COMMENT
    assert decision.tier is RiskTier.MEDIUM
    assert "react-dom" in decision.reason


def test_adversary_runs_on_every_low_pr():
    prs = [make_pr(number=n) for n in (1, 2, 3)]
    client = FakeClient()
    _run([_harvest(prs)], {p.slug: _low(p) for p in prs}, client)
    assert client.calls.count("adversary") == 3


def test_adversary_is_not_consulted_for_non_low_prs():
    pr = make_pr()
    client = FakeClient()
    _run([_harvest([pr])], {pr.slug: Assessment(pr.repo, pr.number, RiskTier.HIGH, 0, "x")}, client)
    assert "adversary" not in client.calls


# ---------------------------------------------------------------------------
# Rebase handling
# ---------------------------------------------------------------------------


def test_behind_branch_triggers_a_dependabot_rebase_request():
    pr = make_pr(merge_state_status="BEHIND")
    _run([_harvest([pr])], {pr.slug: _low(pr)})
    assert ("request_rebase", pr.repo, pr.number, {"dry_run": True}) in execute_mod.gh._calls


def test_a_pr_awaiting_rebase_is_deferred_not_merged():
    """Nothing is merged while its rebase is still outstanding."""
    pr = make_pr(merge_state_status="BEHIND")
    result = _run([_harvest([pr])], {pr.slug: _low(pr)})
    assert result.total_merges == 0
    assert result.decisions[0].deferred


def test_package_drift_after_rebase_downgrades_to_review():
    assessment = _low(make_pr(), packages=["lodash@4.17.21"])
    pr = make_pr(title="bump lodash from 4.17.20 to 4.17.99", merge_state_status="BEHIND")
    assert execute_mod._packages_drifted(assessment, pr)


# ---------------------------------------------------------------------------
# Caps and ordering
# ---------------------------------------------------------------------------


def test_per_repo_cap_stops_further_merges():
    config = {**CONFIG, "caps": {"max_merges_per_repo": 2, "max_merges_total": 15}}
    prs = [make_pr(number=n) for n in range(1, 6)]
    result = execute_mod.execute(
        [_harvest(prs)],
        {p.slug: _low(p) for p in prs},
        FakeClient(),
        config,
        dry_run=True,
    )
    assert result.merges_by_repo["buffingchi_site"] == 2
    assert any(d.deferred for d in result.decisions)


def test_total_cap_stops_merges_across_repos():
    config = {**CONFIG, "caps": {"max_merges_per_repo": 10, "max_merges_total": 3}}
    harvests = [
        _harvest([make_pr(repo=repo, number=n) for n in range(1, 4)])
        for repo in ("BC-Arcade", "buffingchi_site")
    ]
    assessments = {p.slug: _low(p) for h in harvests for p in h.prs}
    result = execute_mod.execute(harvests, assessments, FakeClient(), config, dry_run=True)
    assert result.total_merges <= 3


def test_merge_order_is_respected_within_a_repo():
    first = make_pr(number=10)
    second = make_pr(number=20)
    result = _run(
        [_harvest([second, first])],
        {first.slug: _low(first, order=1), second.slug: _low(second, order=2)},
    )
    merged = [d.number for d in result.decisions if d.merged]
    assert merged == [10, 20]


def test_hold_label_stops_a_low_pr_from_merging():
    pr = make_pr(labels=["dependencies", "dependabot-triage:hold"])
    result = _run([_harvest([pr])], {pr.slug: _low(pr)})
    assert result.decisions[0].action is Action.SKIP


def test_every_decision_produces_a_pr_comment():
    prs = [make_pr(number=1), make_pr(number=2)]
    _run([_harvest(prs)], {p.slug: _low(p) for p in prs})
    commented = {n for name, _, n, _ in execute_mod.gh._calls if name == "comment"}
    assert commented == {1, 2}


# ---------------------------------------------------------------------------
# assess()
# ---------------------------------------------------------------------------


def test_missing_assessment_defaults_to_medium_not_merge():
    """A PR the model forgot must never be treated as approved."""
    pr = make_pr()
    result = assess_mod.assess([_harvest([pr])], FakeClient(), CONFIG)
    assert result[pr.slug].tier is RiskTier.MEDIUM
    assert "No assessment was returned" in result[pr.slug].rationale


def test_assessment_is_parsed_into_the_model_type():
    pr = make_pr()
    payload = {
        "assessments": [
            {
                "repo": pr.repo,
                "number": pr.number,
                "tier": "LOW",
                "merge_order": 1,
                "rationale": "Patch bump.",
                "deciding_question": "NONE",
                "packages": ["lodash@4.17.21"],
                "evidence": {},
            }
        ]
    }
    result = assess_mod.assess([_harvest([pr])], FakeClient(assessment=payload), CONFIG)
    assert result[pr.slug].tier is RiskTier.LOW
    assert result[pr.slug].packages == ["lodash@4.17.21"]


def test_malformed_assessment_entry_aborts_rather_than_guessing():
    pr = make_pr()
    payload = {"assessments": [{"repo": pr.repo, "number": pr.number, "tier": "SORT-OF-FINE"}]}
    with pytest.raises(ModelResponseInvalid):
        assess_mod.assess([_harvest([pr])], FakeClient(assessment=payload), CONFIG)


def test_no_prs_means_no_model_call_at_all():
    client = FakeClient()
    assert assess_mod.assess([_harvest([])], client, CONFIG) == {}
    assert client.calls == []


def test_oversized_corpus_is_assessed_per_repo():
    prs = [make_pr(number=1)]
    client = FakeClient(tokens=999_999)
    assess_mod.assess([_harvest(prs)], client, CONFIG)
    assert client.calls.count("assess") == 1  # one call per repo, not one giant call


def test_corpus_flags_a_repo_with_no_required_checks():
    pr = make_pr()
    corpus = assess_mod.build_corpus([_harvest([pr], required=frozenset())])
    assert "NONE" in corpus and "proves very little" in corpus


def test_corpus_marks_main_base_as_deploy_reaching():
    pr = make_pr(base_ref="main")
    harvest = _harvest([pr])
    harvest.base = "main"
    assert "Render deploy" in assess_mod.build_corpus([harvest])


def test_corpus_includes_the_workflow_diff_for_action_bumps():
    from models import ChangedFile

    patch = "@@\n-        uses: actions/setup-python@v6\n+        uses: actions/setup-python@v7\n"
    pr = make_pr(
        ecosystem="github-actions",
        files=[ChangedFile(path=".github/workflows/ci.yml", patch=patch)],
    )
    assert "setup-python@v7" in assess_mod.build_corpus([_harvest([pr])])


# ---------------------------------------------------------------------------
# diagnose()
# ---------------------------------------------------------------------------


def test_diagnosis_is_empty_when_nothing_is_failing():
    assert diagnose_mod.diagnose(make_pr(), REQUIRED, FakeClient(), CONFIG, dry_run=True) == ""


def test_diagnosis_strips_the_confidence_marker():
    checks = green_checks()[:-1] + [CheckRun(name=sorted(REQUIRED)[-1], conclusion="FAILURE")]
    text = diagnose_mod.diagnose(
        make_pr(checks=checks), REQUIRED, FakeClient(), CONFIG, dry_run=True
    )
    assert "CONFIDENCE" not in text
    assert "Pre-existing failure." in text


def test_low_confidence_diagnosis_escalates_to_the_stronger_model():
    checks = green_checks()[:-1] + [CheckRun(name=sorted(REQUIRED)[-1], conclusion="FAILURE")]
    client = FakeClient(diagnosis="Not sure.\n\nCONFIDENCE: LOW")
    diagnose_mod.diagnose(make_pr(checks=checks), REQUIRED, client, CONFIG, dry_run=True)
    assert "diagnose-escalated" in client.calls


def test_large_pr_batch_is_chunked_even_when_input_is_small():
    """18 PRs overran a 16k output cap on the first full run.

    Input size was only ~31k tokens — nowhere near the ceiling — so chunking
    has to trigger on PR count, because the binding constraint is output.
    """
    prs = [make_pr(number=n) for n in range(1, 10)]  # 9 > max_prs_per_assessment
    client = FakeClient(tokens=1000)  # small input, deliberately
    assess_mod.assess([_harvest(prs)], client, CONFIG)
    assert client.calls.count("assess") == 1  # one call per repo, not one for all


def test_small_batch_stays_a_single_cross_repo_call():
    """Chunking costs cross-PR reasoning, so it should not trigger needlessly."""
    a = _harvest([make_pr(repo="BC-Arcade", number=1)])
    b = _harvest([make_pr(repo="RulersAI", number=2)])
    client = FakeClient(tokens=1000)
    assess_mod.assess([a, b], client, CONFIG)
    assert client.calls.count("assess") == 1


def test_assessment_requests_the_configured_output_budget():
    """A truncated response aborts the run, so the cap must come from config."""
    seen = {}

    class Recorder(FakeClient):
        def complete(self, **kw):
            seen[kw["phase"]] = kw.get("max_tokens")
            return super().complete(**kw)

    assess_mod.assess([_harvest([make_pr()])], Recorder(), CONFIG)
    assert seen["assess"] == 32000


def test_adversary_receives_the_manifest_diff_not_just_names():
    """Two live false positives came from the adversary having no diff to cite.

    It invented a `latest-minor` constraint for sharp — the real diff was
    `^0.35.2` to `^0.35.3` — and blocked a good merge. It can only be asked for
    concrete evidence if it is given concrete evidence.
    """
    from models import ChangedFile

    seen = {}

    class Recorder(FakeClient):
        def complete(self, **kw):
            seen[kw["phase"]] = kw["prompt"]
            return super().complete(**kw)

    pr = make_pr(
        files=[
            ChangedFile(
                path="package.json",
                patch='@@\n-    "sharp": "^0.35.2",\n+    "sharp": "^0.35.3",\n',
            ),
            ChangedFile(path="package-lock.json", patch="@@\n+ noise\n"),
        ]
    )
    _run([_harvest([pr])], {pr.slug: _low(pr)}, Recorder())
    prompt = seen["adversary"]
    assert '"sharp": "^0.35.3"' in prompt, "manifest diff missing from adversary prompt"
    assert "noise" not in prompt, "lockfile should not be included"


def test_lockfile_only_pr_tells_the_adversary_so_explicitly():
    """Silence would invite the model to guess at constraints it cannot see."""
    from models import ChangedFile

    seen = {}

    class Recorder(FakeClient):
        def complete(self, **kw):
            seen[kw["phase"]] = kw["prompt"]
            return super().complete(**kw)

    pr = make_pr(files=[ChangedFile(path="package-lock.json", patch="@@\n+ x\n")])
    _run([_harvest([pr])], {pr.slug: _low(pr)}, Recorder())
    assert "lockfile-only update" in seen["adversary"]
