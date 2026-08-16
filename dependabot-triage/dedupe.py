"""Phase 1.5 — drop PRs that already received a final verdict and have not changed since.

Sits between harvest and assess. A PR that got a COMMENT verdict last night
(left for a human to look at) and has not moved its head SHA carries no new
information for the model to react to — reassessing it burns tokens and files
an identical "left for review" comment on top of the one already there.

Guard-blocked (SKIP) and deferred PRs are deliberately never deduped here:
their eligibility depends on live CI/guard state that can change without a new
commit (a rerun, a fix landing elsewhere), and remediate's recreate-and-retry
loop depends on being tried again every run. Only a genuine "a human needs to
look at this, and nothing new has arrived" verdict is treated as a repeat.
"""

from __future__ import annotations

from dataclasses import replace

import metrics
from harvest import RepoHarvest
from models import Action, PullRequest, Repeat, RiskTier


def _is_repeat(pr: PullRequest, last: dict | None) -> bool:
    """True when ``last`` is a final COMMENT verdict already delivered on this exact commit."""
    if not last or not pr.head_sha:
        return False
    if last.get("head_sha") != pr.head_sha:
        return False
    if last.get("deferred"):
        return False
    return last.get("action") == Action.COMMENT.value


def _to_repeat(pr: PullRequest, last: dict) -> Repeat:
    at = last.get("at") or ""
    return Repeat(
        repo=pr.repo,
        number=pr.number,
        title=pr.title,
        tier=RiskTier(last.get("tier", RiskTier.MEDIUM.value)),
        action=Action(last.get("action", Action.COMMENT.value)),
        reason=last.get("reason", ""),
        deciding_question=last.get("deciding_question", ""),
        head_sha=pr.head_sha,
        last_seen_at=at[:10],
        last_run_id=str(last.get("run_id", "")),
    )


def split(harvests: list[RepoHarvest], config: dict) -> tuple[list[RepoHarvest], list[Repeat]]:
    """Partition harvested PRs into ones that still need triage and repeats.

    Returns a new list of :class:`RepoHarvest` with repeat PRs removed from
    ``prs`` — the harvests passed in are left untouched — plus the repeats
    themselves, each carrying the verdict and date they were last given.
    """
    if not config.get("dedupe", {}).get("enabled", True):
        return harvests, []

    last_by_slug = metrics.last_decisions()
    filtered: list[RepoHarvest] = []
    repeats: list[Repeat] = []
    for harvest in harvests:
        kept: list[PullRequest] = []
        for pr in harvest.dependabot_prs:
            last = last_by_slug.get(pr.slug)
            if _is_repeat(pr, last):
                repeats.append(_to_repeat(pr, last))
            else:
                kept.append(pr)
        filtered.append(replace(harvest, prs=kept))
    return filtered, repeats
