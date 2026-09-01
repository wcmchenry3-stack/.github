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
        "merge_retry_attempts": 2,
        "merge_retry_wait": 0,
    },
    "hold_label": "dependabot-triage:hold",
    "adversary": {"enabled": True},
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
# Remediate: asking Dependabot to recreate a red-but-recoverable PR
# ---------------------------------------------------------------------------


def test_recoverable_check_failure_gets_a_recreate_not_an_immediate_skip(monkeypatch):
    """BookshelfAI#437: npm ci died on a lockfile desync before lint even ran.

    The only failing precondition is G06 (a required check red), and that
    check's own link names the run that failed — so with remediate enabled and
    a log that matches a recoverable signature, the PR should get a
    `@dependabot recreate` rather than being left for review outright.
    """
    checks = green_checks()[:-1] + [
        CheckRun(
            name=sorted(REQUIRED)[-1],
            conclusion="FAILURE",
            details_url="https://github.com/acme/widget/actions/runs/777/job/1",
        )
    ]
    pr = make_pr(checks=checks)
    config = {
        **CONFIG,
        "remediate": {"enabled": True, "max_recreates_per_run": 4},
        "timeouts": {**CONFIG["timeouts"], "rebase_wait": 0},
    }

    monkeypatch.setattr(execute_mod.gh, "pr_commit_authors", lambda *a, **k: [])
    monkeypatch.setattr(
        diagnose_mod.gh,
        "_run",
        lambda args, timeout=120: "npm error code EUSAGE\nMissing: x from lock file\n",
    )
    monkeypatch.setattr(execute_mod, "_refresh", lambda pr, harvest, owner: pr)

    result = execute_mod.execute(
        [_harvest([pr])], {pr.slug: _low(pr)}, FakeClient(), config, dry_run=False
    )

    assert (
        "request_rebase",
        pr.repo,
        pr.number,
        {"recreate": True, "dry_run": False},
    ) in execute_mod.gh._calls
    assert result.decisions[0].reason != "Automated merge refused by a precondition check."


def test_unrecoverable_check_failure_still_skips_immediately(monkeypatch):
    """A real test failure must not burn a recreate — a rebuild cannot fix it."""
    checks = green_checks()[:-1] + [
        CheckRun(
            name=sorted(REQUIRED)[-1],
            conclusion="FAILURE",
            details_url="https://github.com/acme/widget/actions/runs/778/job/1",
        )
    ]
    pr = make_pr(checks=checks)
    config = {**CONFIG, "remediate": {"enabled": True, "max_recreates_per_run": 4}}

    monkeypatch.setattr(execute_mod.gh, "pr_commit_authors", lambda *a, **k: [])
    monkeypatch.setattr(
        diagnose_mod.gh,
        "_run",
        lambda args, timeout=120: "1 test failed\nAssertionError: expected 2 got 3\n",
    )

    result = execute_mod.execute(
        [_harvest([pr])], {pr.slug: _low(pr)}, FakeClient(), config, dry_run=False
    )

    assert not any(call[0] == "request_rebase" for call in execute_mod.gh._calls)
    assert result.decisions[0].action is Action.SKIP
    assert result.decisions[0].reason == "Automated merge refused by a precondition check."


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


def test_adversary_is_skipped_when_disabled():
    """Disabled by default after 3 objections, 3 fabrications, 0 real catches."""
    config = {**CONFIG, "adversary": {"enabled": False}}
    pr = make_pr()
    client = FakeClient(adversary="UNSAFE: something invented")
    result = execute_mod.execute(
        [_harvest([pr])], {pr.slug: _low(pr)}, client, config, dry_run=True
    )
    assert "adversary" not in client.calls
    assert result.decisions[0].action is Action.MERGE


def test_absent_adversary_config_defaults_to_off():
    """A missing key must not silently re-enable a layer we switched off."""
    config = {k: v for k, v in CONFIG.items() if k != "adversary"}
    pr = make_pr()
    client = FakeClient(adversary="UNSAFE: invented")
    execute_mod.execute([_harvest([pr])], {pr.slug: _low(pr)}, client, config, dry_run=True)
    assert "adversary" not in client.calls


# ---------------------------------------------------------------------------
# Merge retry — incident 2026-08-31: PR #173 failed with "not mergeable"
# because GitHub was still computing mergeability after a push, which
# `gh pr merge` does not wait out. See _merge_with_retry.
# ---------------------------------------------------------------------------


class _FlakyMerge:
    """Fails with a "not mergeable" GhError a fixed number of times, then succeeds."""

    def __init__(self, fail_times: int, *, mergeable="MERGEABLE", state="CLEAN"):
        self.fail_times = fail_times
        self.mergeable = mergeable
        self.state = state
        self.calls = 0

    def merge(self, repo, owner, number, method, *, dry_run=False):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise execute_mod.gh.GhError(
                f"gh pr merge {number} --repo {owner}/{repo} --{method} --delete-branch "
                f"failed (1): X Pull request {owner}/{repo}#{number} is not mergeable: "
                "the merge commit cannot be cleanly created."
            )

    def pr_mergeable(self, repo, owner, number):
        return self.mergeable, self.state


def test_transient_merge_failure_is_retried_until_it_succeeds(monkeypatch):
    flaky = _FlakyMerge(fail_times=2)  # still-computing on the first two tries
    monkeypatch.setattr(execute_mod.gh, "merge", flaky.merge)
    monkeypatch.setattr(execute_mod.gh, "pr_mergeable", flaky.pr_mergeable)
    monkeypatch.setattr(execute_mod.time, "sleep", lambda _seconds: None)

    config = {**CONFIG["timeouts"], "merge_retry_attempts": 3, "merge_retry_wait": 0}
    execute_mod._merge_with_retry(
        "wcm_portfolio_site",
        "wcmchenry3-stack",
        173,
        "merge",
        config={"timeouts": config},
        dry_run=False,
    )
    assert flaky.calls == 3  # two failures, then the try that succeeds


def test_settled_conflict_is_raised_immediately_not_retried(monkeypatch):
    flaky = _FlakyMerge(fail_times=99, mergeable="CONFLICTING", state="DIRTY")
    monkeypatch.setattr(execute_mod.gh, "merge", flaky.merge)
    monkeypatch.setattr(execute_mod.gh, "pr_mergeable", flaky.pr_mergeable)
    monkeypatch.setattr(execute_mod.time, "sleep", lambda _seconds: None)

    with pytest.raises(execute_mod.gh.GhError):
        execute_mod._merge_with_retry(
            "wcm_portfolio_site",
            "wcmchenry3-stack",
            173,
            "merge",
            config={"timeouts": CONFIG["timeouts"]},
            dry_run=False,
        )
    assert flaky.calls == 1  # GitHub already decided — no point retrying a real conflict


def test_merge_retry_gives_up_after_its_configured_budget(monkeypatch):
    flaky = _FlakyMerge(fail_times=99)  # never settles within the retry budget
    monkeypatch.setattr(execute_mod.gh, "merge", flaky.merge)
    monkeypatch.setattr(execute_mod.gh, "pr_mergeable", flaky.pr_mergeable)
    monkeypatch.setattr(execute_mod.time, "sleep", lambda _seconds: None)

    config = {**CONFIG["timeouts"], "merge_retry_attempts": 2, "merge_retry_wait": 0}
    with pytest.raises(execute_mod.gh.GhError):
        execute_mod._merge_with_retry(
            "wcm_portfolio_site",
            "wcmchenry3-stack",
            173,
            "merge",
            config={"timeouts": config},
            dry_run=False,
        )
    assert flaky.calls == 3  # the initial attempt plus 2 retries, then give up


def test_settled_merge_failure_becomes_a_skip_decision_not_a_crash(monkeypatch):
    """A real conflict must never abort the run — every other decision still gets reported."""

    def fail_merge(repo, owner, number, method, *, dry_run=False):
        raise execute_mod.gh.GhError(
            f"gh pr merge {number} --repo {owner}/{repo} --{method} --delete-branch "
            f"failed (1): X Pull request {owner}/{repo}#{number} is not mergeable: "
            "the merge commit cannot be cleanly created."
        )

    monkeypatch.setattr(execute_mod.gh, "merge", fail_merge)
    monkeypatch.setattr(execute_mod.gh, "pr_mergeable", lambda *a, **k: ("CONFLICTING", "DIRTY"))
    monkeypatch.setattr(execute_mod.gh, "required_contexts", lambda *a, **k: REQUIRED)
    monkeypatch.setattr(execute_mod, "_refresh", lambda pr, harvest, owner: pr)

    pr = make_pr()
    result = execute_mod.execute(
        [_harvest([pr])], {pr.slug: _low(pr)}, FakeClient(), CONFIG, dry_run=False
    )

    assert result.total_merges == 0
    assert result.decisions[0].action is Action.SKIP
    assert "not mergeable" in result.decisions[0].reason


def test_a_late_failure_still_leaves_prior_decisions_on_the_shared_result(monkeypatch):
    """Incident 2026-08-31: execute() raising mid-run silently emptied the report.

    __main__.py now passes in the ExecutionResult it will read afterwards, so
    even when execute() raises, whatever was decided before the failure is
    still there — the caller's variable and execute()'s are the same object.
    """
    prs = [make_pr(number=1), make_pr(number=2)]

    def flaky_comment(repo, owner, number, body, *, dry_run=False):
        if number == 2:
            raise execute_mod.gh.GhError("transient failure posting the second comment")

    monkeypatch.setattr(execute_mod.gh, "comment", flaky_comment)

    shared = execute_mod.ExecutionResult()
    with pytest.raises(execute_mod.gh.GhError):
        execute_mod.execute(
            [_harvest(prs)],
            {p.slug: _low(p) for p in prs},
            FakeClient(),
            CONFIG,
            dry_run=True,
            result=shared,
        )

    # Both PRs were already decided (and dry-run "merged") before the comment
    # loop hit the failure — none of that is lost just because execute() raised.
    assert shared.total_merges == 2
    assert len(shared.decisions) == 2
