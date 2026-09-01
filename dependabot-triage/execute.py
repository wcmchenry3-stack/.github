"""Phase 3 — act on the assessment. Deterministic; no model decides anything here.

The loop deliberately does not wait on rebases one at a time. Dependabot lands a
rebase asynchronously — usually in a couple of minutes, sometimes far longer for a
large lockfile — so every rebase is requested up front and PRs are then drained in
whatever order they become ready. Nothing is force-merged to beat the clock; work
that does not finish is deferred to tomorrow, which costs nothing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import diagnose as diagnose_mod
import gh
import guards
import remediate
from assess import adversarial_review
from harvest import RepoHarvest
from llm import Client
from models import Action, Assessment, Decision, PullRequest, RiskTier

log = logging.getLogger(__name__)


@dataclass
class Pending:
    """A PR waiting on Dependabot to land a rebase."""

    pr: PullRequest
    harvest: RepoHarvest
    assessment: Assessment
    original_sha: str
    requested_at: float
    recreated: bool = False


@dataclass
class ExecutionResult:
    decisions: list[Decision] = field(default_factory=list)
    merges_by_repo: dict[str, int] = field(default_factory=dict)

    @property
    def total_merges(self) -> int:
        return sum(self.merges_by_repo.values())


def _comment_body(decision: Decision, run_url: str) -> str:
    """The PR comment. Says what was decided and, when refused, exactly why."""
    if decision.action is Action.MERGE:
        head = "### Merged automatically by nightly Dependabot triage"
        detail = decision.reason
    else:
        head = "### Left for review by nightly Dependabot triage"
        detail = decision.reason

    lines = [head, "", detail, ""]

    failed = [g for g in decision.guard_results if not g.passed]
    if failed:
        lines.append("**Failed preconditions:**")
        lines += [f"- `{g.guard_id}` — {g.detail}" for g in failed]
        lines.append("")
    if decision.diagnosis:
        lines += ["**CI failure analysis:**", "", decision.diagnosis, ""]
    if decision.deciding_question and decision.deciding_question != "NONE":
        lines.append(f"Deciding rubric question: `{decision.deciding_question}`")

    lines += [
        "",
        f"Risk tier: `{decision.tier.value}` · [run log]({run_url})",
        "",
        "_This comment is generated. No file in this PR was modified by the agent — "
        "it may only comment, ask Dependabot to rebase, and merge._",
    ]
    return "\n".join(lines)


def _refresh(pr: PullRequest, harvest: RepoHarvest, owner: str) -> PullRequest:
    """Re-read a PR from the API so guards run against current state."""
    from harvest import harvest_repo

    fresh = harvest_repo(harvest.repo, owner, harvest.base, harvest.merge_method, fetch_diffs=True)
    for candidate in fresh.dependabot_prs:
        if candidate.number == pr.number:
            return candidate
    return pr


def _checks_settled(pr: PullRequest, required: frozenset[str]) -> bool:
    """True once every required check has reached a terminal state."""
    by_name = {c.name: c for c in pr.checks}
    return all(name in by_name and by_name[name].is_settled for name in required)


def _packages_drifted(assessment: Assessment, pr: PullRequest) -> bool:
    """Did a rebase change what this PR actually moves?

    A rebase can pull in different transitive versions than the ones assessed, so
    a stale LOW is never trusted — the caller re-confirms the tier when this is true.
    """
    if not assessment.packages:
        return False
    from harvest import parse_bump

    package, _, to_version = parse_bump(pr.title)
    if not package:
        return False
    return not any(package in entry and to_version in entry for entry in assessment.packages)


# GitHub computes a PR's mergeability in the background after any push and
# reports it as still-settling until that finishes. `gh pr merge` (unlike
# `--auto`) does not wait that out — it just fails with "not mergeable" —
# which is indistinguishable at the surface from a real conflict. These are
# the states that mean GitHub has actually finished and decided against
# merging, as opposed to still computing.
_MERGE_CONFLICT = {"CONFLICTING"}
_MERGE_BLOCKED = {"DIRTY", "BLOCKED", "DRAFT"}


def _merge_state_settled(mergeable: str, state: str) -> bool:
    """True once GitHub has finished computing mergeability, one way or another."""
    return mergeable in _MERGE_CONFLICT or state in _MERGE_BLOCKED


def _merge_with_retry(
    repo: str, owner: str, number: int, method: str, *, config: dict, dry_run: bool
) -> None:
    """``gh.merge``, retried while GitHub is still computing mergeability.

    A failed merge is retried only when it fails with "not mergeable" *and*
    ``gh pr view`` still reports an unsettled state — see
    :func:`_merge_state_settled`. A settled conflict or block is raised
    immediately; there is nothing to wait out there.
    """
    timeouts = config["timeouts"]
    attempts = timeouts.get("merge_retry_attempts", 2)
    wait = timeouts.get("merge_retry_wait", 15)

    for attempt in range(attempts + 1):
        try:
            gh.merge(repo, owner, number, method, dry_run=dry_run)
            return
        except gh.GhError as exc:
            if dry_run or attempt >= attempts or "not mergeable" not in str(exc).lower():
                raise
            try:
                mergeable, state = gh.pr_mergeable(repo, owner, number)
            except gh.GhError:
                raise exc from None  # the mergeable check itself failed; report the merge error
            if _merge_state_settled(mergeable, state):
                raise  # GitHub has decided — a real conflict/block, not a race
            log.info(
                "%s/%s#%s: merge rejected while GitHub was still computing mergeability "
                "(mergeable=%s, mergeStateStatus=%s) — retrying in %ss",
                owner,
                repo,
                number,
                mergeable,
                state,
                wait,
            )
            time.sleep(wait)


def execute(
    harvests: list[RepoHarvest],
    assessments: dict[str, Assessment],
    client: Client,
    config: dict,
    *,
    run_url: str = "",
    dry_run: bool = True,
    result: ExecutionResult | None = None,
) -> ExecutionResult:
    """Run the full decision loop across every repo.

    ``result`` may be supplied by the caller and is mutated in place rather
    than only being returned. That lets the caller share one object across
    its own try/except: when an unexpected `GhError` propagates out of this
    function, whatever was already decided is still visible to it instead of
    being discarded along with the exception — see the call site in
    __main__.py.
    """
    owner = config["owner"]
    caps = config["caps"]
    timeouts = config["timeouts"]
    hold_label = config["hold_label"]

    result = result if result is not None else ExecutionResult()
    pending: list[Pending] = []
    recreates = 0
    started = time.monotonic()

    def budget_left() -> float:
        return timeouts["global_wall_clock"] - (time.monotonic() - started)

    # ---- Pass 1: decide, and fan out every rebase request without waiting -----
    for harvest in harvests:
        ordered = sorted(
            harvest.dependabot_prs,
            key=lambda p: assessments.get(
                p.slug, Assessment(p.repo, p.number, RiskTier.MEDIUM, 0, "")
            ).merge_order,
        )
        for pr in ordered:
            assessment = assessments.get(pr.slug)
            if assessment is None:
                continue

            if assessment.tier is not RiskTier.LOW:
                result.decisions.append(
                    Decision(
                        repo=pr.repo,
                        number=pr.number,
                        title=pr.title,
                        tier=assessment.tier,
                        action=Action.COMMENT,
                        reason=assessment.rationale,
                        deciding_question=assessment.deciding_question,
                        head_sha=pr.head_sha,
                    )
                )
                continue

            checks = guards.run_all_guards(
                pr, harvest.baseline, assessed_sha=pr.head_sha, hold_label=hold_label
            )
            failed = guards.failures(checks)
            blocking = [g for g in failed if g.guard_id != "G10"]

            if blocking:
                # A PR blocked only on red checks may be red because its lockfile
                # disagrees with its manifest, not because the bump is bad. Ask
                # Dependabot to rebuild rather than writing to the branch here.
                only_checks_failed = {g.guard_id for g in blocking} == {"G06"}
                if (
                    only_checks_failed
                    and config.get("remediate", {}).get("enabled", False)
                    and recreates < config.get("remediate", {}).get("max_recreates_per_run", 4)
                ):
                    log_text = (
                        ""
                        if dry_run
                        else diagnose_mod.fetch_log_tail(
                            pr.repo,
                            owner,
                            pr.number,
                            pr.head_ref,
                            pr.failing_required(harvest.baseline.required_contexts),
                        )
                    )
                    authors = [] if dry_run else gh.pr_commit_authors(pr.repo, owner, pr.number)
                    ok, why = remediate.should_recreate(
                        pr, harvest.baseline.required_contexts, log_text, authors
                    )
                    if ok:
                        log.info("%s: asking Dependabot to recreate (%s)", pr.slug, why)
                        gh.request_rebase(pr.repo, owner, pr.number, recreate=True, dry_run=dry_run)
                        recreates += 1
                        pending.append(
                            Pending(
                                pr,
                                harvest,
                                assessment,
                                pr.head_sha,
                                time.monotonic(),
                                recreated=True,
                            )
                        )
                        continue
                    log.info("%s: not recreating — %s", pr.slug, why)

                result.decisions.append(
                    Decision(
                        repo=pr.repo,
                        number=pr.number,
                        title=pr.title,
                        tier=assessment.tier,
                        action=Action.SKIP,
                        reason="Automated merge refused by a precondition check.",
                        guard_results=checks,
                        deciding_question=assessment.deciding_question,
                        head_sha=pr.head_sha,
                    )
                )
                continue

            still_low, objection = (True, "")
            if config.get("adversary", {}).get("enabled", False):
                still_low, objection = adversarial_review(pr, assessment, client, config)
            if not still_low:
                result.decisions.append(
                    Decision(
                        repo=pr.repo,
                        number=pr.number,
                        title=pr.title,
                        tier=RiskTier.MEDIUM,
                        action=Action.COMMENT,
                        reason=f"Downgraded on adversarial review: {objection}",
                        deciding_question=assessment.deciding_question,
                        head_sha=pr.head_sha,
                    )
                )
                continue

            if pr.merge_state_status.upper() == "BEHIND" or any(
                g.guard_id == "G10" for g in failed
            ):
                gh.request_rebase(pr.repo, owner, pr.number, dry_run=dry_run)
                pending.append(Pending(pr, harvest, assessment, pr.head_sha, time.monotonic()))
                continue

            pending.append(Pending(pr, harvest, assessment, "", time.monotonic()))

    # ---- Pass 2: drain, first-ready-first-served -----------------------------
    while pending and budget_left() > 0:
        progressed = False

        for item in list(pending):
            if result.total_merges >= caps["max_merges_total"]:
                break
            if result.merges_by_repo.get(item.harvest.repo, 0) >= caps["max_merges_per_repo"]:
                pending.remove(item)
                result.decisions.append(
                    Decision(
                        repo=item.pr.repo,
                        number=item.pr.number,
                        title=item.pr.title,
                        tier=item.assessment.tier,
                        action=Action.SKIP,
                        reason=f"Per-repo merge cap of {caps['max_merges_per_repo']} reached.",
                        deferred=True,
                        head_sha=item.pr.head_sha,
                    )
                )
                continue

            waited = time.monotonic() - item.requested_at
            fresh = item.pr if dry_run else _refresh(item.pr, item.harvest, owner)

            if item.original_sha and fresh.head_sha == item.original_sha:
                if waited > timeouts["rebase_wait"]:
                    if not item.recreated:
                        gh.request_rebase(
                            fresh.repo, owner, fresh.number, recreate=True, dry_run=dry_run
                        )
                        item.recreated = True
                        item.requested_at = time.monotonic()
                        progressed = True
                        continue
                    pending.remove(item)
                    result.decisions.append(
                        Decision(
                            repo=fresh.repo,
                            number=fresh.number,
                            title=fresh.title,
                            tier=item.assessment.tier,
                            action=Action.COMMENT,
                            reason="Dependabot did not land a rebase in time; deferred to the next run.",
                            deferred=True,
                            rebased=True,
                            head_sha=fresh.head_sha,
                        )
                    )
                    progressed = True
                continue

            required = item.harvest.baseline.required_contexts
            if not _checks_settled(fresh, required):
                if waited > timeouts["checks_wait"]:
                    pending.remove(item)
                    result.decisions.append(
                        Decision(
                            repo=fresh.repo,
                            number=fresh.number,
                            title=fresh.title,
                            tier=item.assessment.tier,
                            action=Action.COMMENT,
                            reason="CI had not finished within the wait budget; deferred to the next run.",
                            deferred=True,
                            head_sha=fresh.head_sha,
                        )
                    )
                    progressed = True
                continue

            pending.remove(item)
            progressed = True

            if item.original_sha and _packages_drifted(item.assessment, fresh):
                result.decisions.append(
                    Decision(
                        repo=fresh.repo,
                        number=fresh.number,
                        title=fresh.title,
                        tier=RiskTier.MEDIUM,
                        action=Action.COMMENT,
                        reason=(
                            "The rebase changed which package versions this PR moves, so the "
                            "original low-risk assessment no longer applies. Left for review."
                        ),
                        rebased=True,
                        head_sha=fresh.head_sha,
                    )
                )
                continue

            live_contexts = (
                item.harvest.baseline.required_contexts
                if dry_run
                else gh.required_contexts(fresh.repo, owner, item.harvest.base)
            )
            checks = guards.run_all_guards(
                fresh,
                item.harvest.baseline,
                assessed_sha=fresh.head_sha,
                live_contexts=live_contexts,
                hold_label=hold_label,
            )

            if not guards.may_merge(checks):
                result.decisions.append(
                    Decision(
                        repo=fresh.repo,
                        number=fresh.number,
                        title=fresh.title,
                        tier=item.assessment.tier,
                        action=Action.SKIP,
                        reason="Preconditions no longer held at merge time.",
                        guard_results=checks,
                        rebased=bool(item.original_sha),
                        head_sha=fresh.head_sha,
                    )
                )
                continue

            try:
                _merge_with_retry(
                    fresh.repo,
                    owner,
                    fresh.number,
                    item.harvest.merge_method,
                    config=config,
                    dry_run=dry_run,
                )
            except gh.GhError as exc:
                # A settled conflict/block, or retries exhausted while GitHub was
                # still deciding. Either way this is a normal, reportable outcome —
                # not a reason to crash the run and lose every other decision.
                result.decisions.append(
                    Decision(
                        repo=fresh.repo,
                        number=fresh.number,
                        title=fresh.title,
                        tier=item.assessment.tier,
                        action=Action.SKIP,
                        reason=f"Merge attempt failed: {exc}",
                        guard_results=checks,
                        rebased=bool(item.original_sha),
                        head_sha=fresh.head_sha,
                    )
                )
                continue

            result.merges_by_repo[fresh.repo] = result.merges_by_repo.get(fresh.repo, 0) + 1
            result.decisions.append(
                Decision(
                    repo=fresh.repo,
                    number=fresh.number,
                    title=fresh.title,
                    tier=RiskTier.LOW,
                    action=Action.MERGE,
                    reason=item.assessment.rationale,
                    guard_results=checks,
                    rebased=bool(item.original_sha),
                    merged=True,
                    head_sha=fresh.head_sha,
                )
            )

        if pending and not progressed:
            if dry_run:
                break
            time.sleep(timeouts["poll_interval"])

    for item in pending:
        result.decisions.append(
            Decision(
                repo=item.pr.repo,
                number=item.pr.number,
                title=item.pr.title,
                tier=item.assessment.tier,
                action=Action.COMMENT,
                reason="Run reached its wall-clock budget; deferred to the next run.",
                deferred=True,
                head_sha=item.pr.head_sha,
            )
        )

    # Mark PRs whose required checks were already failing when the run began.
    # They were not mergeable tonight by anyone, so they are excluded from the
    # autonomy-rate denominator — otherwise the metric drifts into measuring repo
    # health rather than how much work the agent is actually taking on.
    already_red = {
        pr.slug: bool(pr.failing_required(h.baseline.required_contexts))
        for h in harvests
        for pr in h.dependabot_prs
    }
    for decision in result.decisions:
        decision.pre_existing_red = already_red.get(decision.slug, False)

    for decision in result.decisions:
        if decision.action in (Action.COMMENT, Action.SKIP) or decision.merged:
            gh.comment(
                decision.repo,
                owner,
                decision.number,
                _comment_body(decision, run_url),
                dry_run=dry_run,
            )

    return result
