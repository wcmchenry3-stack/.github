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

import gh
import guards
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


def execute(
    harvests: list[RepoHarvest],
    assessments: dict[str, Assessment],
    client: Client,
    config: dict,
    *,
    run_url: str = "",
    dry_run: bool = True,
) -> ExecutionResult:
    """Run the full decision loop across every repo."""
    owner = config["owner"]
    caps = config["caps"]
    timeouts = config["timeouts"]
    hold_label = config["hold_label"]

    result = ExecutionResult()
    pending: list[Pending] = []
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
                    )
                )
                continue

            checks = guards.run_all_guards(
                pr, harvest.baseline, assessed_sha=pr.head_sha, hold_label=hold_label
            )
            failed = guards.failures(checks)
            blocking = [g for g in failed if g.guard_id != "G10"]

            if blocking:
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
                    )
                )
                continue

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
                    )
                )
                continue

            gh.merge(fresh.repo, owner, fresh.number, item.harvest.merge_method, dry_run=dry_run)
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
            )
        )

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
