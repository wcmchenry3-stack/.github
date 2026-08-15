"""Phase 2 — collective risk assessment, plus the adversarial second opinion.

The assessment is one call over every PR in the stack so cross-repo and cross-PR
reasoning (ordering, coupled families) is possible at all. Its output is
schema-validated data; the executor never sees model prose.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from budget import should_chunk
from harvest import RepoHarvest, parse_bump
from llm import Client, ModelResponseInvalid
from models import Assessment, PullRequest, RiskTier

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
SCHEMA = json.loads((HERE / "schema" / "assessment.schema.json").read_text(encoding="utf-8"))
ASSESS_PROMPT = (HERE / "prompts" / "assess.md").read_text(encoding="utf-8")
ADVERSARY_PROMPT = (HERE / "prompts" / "adversary.md").read_text(encoding="utf-8")

# Dependabot embeds full release notes; enough to judge, not enough to blow the
# context budget on a forty-PR night.
BODY_CHARS = 3000


def _describe_pr(pr: PullRequest, required: frozenset[str], base: str) -> str:
    """Render one PR as the evidence block the rubric is applied to."""
    package, from_v, to_v = parse_bump(pr.title)
    lines = [
        f"### {pr.repo}#{pr.number}",
        f"title: {pr.title}",
        f"ecosystem: {pr.ecosystem}",
        f"base branch: {base}"
        + ("  (merges here reach a Render deploy)" if base == "main" else ""),
        f"grouped: {'yes' if pr.is_group else 'no'}",
        f"labels: {', '.join(pr.labels) or 'none'}",
    ]
    if package:
        lines.append(f"package: {package} {from_v} -> {to_v}")
    lines.append(f"changed files: {', '.join(pr.paths) or 'none'}")

    if required:
        lines.append(
            f"required checks in this repo ({len(required)}): {', '.join(sorted(required))}"
        )
    else:
        lines.append(
            "required checks in this repo: NONE — this repo's branch protection "
            "requires no status checks, so a green PR proves very little here"
        )

    failing = [c.name for c in pr.checks if c.is_settled and not c.is_success]
    lines.append(f"currently failing checks: {', '.join(failing) if failing else 'none'}")
    lines.append(f"merge state: {pr.merge_state_status} / {pr.mergeable}")

    for f in pr.files:
        if f.path.startswith(".github/workflows/") and f.patch:
            lines.append(f"workflow diff for {f.path}:\n{f.patch[:1200]}")

    if pr.body:
        lines.append(f"release notes (truncated):\n{pr.body[:BODY_CHARS]}")
    return "\n".join(lines)


def build_corpus(harvests: list[RepoHarvest], today: str | None = None) -> str:
    """Assemble the evidence for every open Dependabot PR across the stack.

    The date is stated explicitly because Q13 asks how fresh a release is, and a
    model with no reference point will guess. An adversarial reviewer once
    blocked a good merge by calling week-old changelog dates "future-dated".
    """
    today = today or datetime.now(UTC).strftime("%Y-%m-%d")
    blocks: list[str] = [f"Today's date is {today}."]
    for harvest in harvests:
        prs = harvest.dependabot_prs
        if not prs:
            continue
        blocks.append(f"## Repository: {harvest.repo} (base: {harvest.base})")
        for pr in prs:
            blocks.append(_describe_pr(pr, harvest.baseline.required_contexts, harvest.base))
    return "\n\n".join(blocks)


def _to_assessment(raw: dict) -> Assessment:
    return Assessment(
        repo=raw["repo"],
        number=int(raw["number"]),
        tier=RiskTier(raw["tier"]),
        merge_order=int(raw.get("merge_order", 0)),
        rationale=raw.get("rationale", "").strip(),
        deciding_question=raw.get("deciding_question", "NONE"),
        evidence=raw.get("evidence", {}) or {},
        packages=list(raw.get("packages") or []),
    )


def assess(harvests: list[RepoHarvest], client: Client, config: dict) -> dict[str, Assessment]:
    """Classify every open Dependabot PR. Returns ``{"repo#number": Assessment}``.

    Any PR the model fails to return an assessment for is defaulted to MEDIUM —
    a silent omission must never read as permission to merge.
    """
    pr_total = sum(len(h.dependabot_prs) for h in harvests)
    if pr_total == 0:
        return {}
    corpus = build_corpus(harvests)

    model = config["models"]["assess"]
    effort = config["effort"]["assess"]
    ceiling = config["budget"]["assessment_token_ceiling"]
    max_prs = config["budget"].get("max_prs_per_assessment", 8)
    max_out = config["budget"].get("assessment_max_output_tokens", 32000)

    pr_count = pr_total
    token_count = client.count_tokens(model, ASSESS_PROMPT, corpus)
    log.info("assessment corpus is %d tokens across %d PR(s)", token_count, pr_count)

    # The binding constraint is *output*, not input: each PR yields a rationale
    # plus per-question evidence, so a large batch truncates long before the
    # input is anywhere near the context limit. Chunk on PR count as well.
    if should_chunk(token_count, ceiling) or pr_count > max_prs:
        log.info(
            "assessing per repo: %d PRs / %d tokens exceeds limit of %d PRs / %d tokens",
            pr_count,
            token_count,
            max_prs,
            ceiling,
        )
        raw_items: list[dict] = []
        for harvest in harvests:
            if not harvest.dependabot_prs:
                continue
            chunk = build_corpus([harvest])
            payload, _ = client.complete(
                phase="assess",
                model=model,
                system=ASSESS_PROMPT,
                prompt=chunk,
                effort=effort,
                max_tokens=max_out,
                cache_system=True,
                schema=SCHEMA,
            )
            raw_items.extend(payload.get("assessments", []))
    else:
        payload, _ = client.complete(
            phase="assess",
            model=model,
            system=ASSESS_PROMPT,
            prompt=corpus,
            effort=effort,
            max_tokens=max_out,
            cache_system=True,
            schema=SCHEMA,
        )
        raw_items = payload.get("assessments", [])

    out: dict[str, Assessment] = {}
    for raw in raw_items:
        try:
            item = _to_assessment(raw)
        except (KeyError, ValueError) as exc:
            raise ModelResponseInvalid(f"malformed assessment entry {raw!r}: {exc}") from exc
        out[item.slug] = item

    for harvest in harvests:
        for pr in harvest.dependabot_prs:
            if pr.slug not in out:
                log.warning("no assessment returned for %s; defaulting to MEDIUM", pr.slug)
                out[pr.slug] = Assessment(
                    repo=pr.repo,
                    number=pr.number,
                    tier=RiskTier.MEDIUM,
                    merge_order=0,
                    rationale="No assessment was returned for this PR, so it was left for review.",
                    deciding_question="NONE",
                )
    return out


def adversarial_review(
    pr: PullRequest, assessment: Assessment, client: Client, config: dict
) -> tuple[bool, str]:
    """Second opinion on a LOW rating. Returns ``(still_low, objection)``.

    Deliberately runs on a different, weaker model than the assessor so it is not
    simply the same reasoning agreeing with itself. A concrete objection downgrades
    the PR to MEDIUM; the executor then comments instead of merging.
    """
    prompt = "\n".join(
        [
            f"Repository: {pr.repo}  PR #{pr.number}",
            f"Title: {pr.title}",
            f"Ecosystem: {pr.ecosystem}   Grouped: {'yes' if pr.is_group else 'no'}",
            f"Changed files: {', '.join(pr.paths)}",
            f"Packages moved: {', '.join(assessment.packages) or 'unknown'}",
            f"Reviewer's rationale: {assessment.rationale}",
            "",
            "Release notes (truncated):",
            pr.body[:BODY_CHARS] or "(none supplied)",
        ]
    )
    verdict, _ = client.complete(
        phase="adversary",
        model=config["models"]["adversary"],
        system=ADVERSARY_PROMPT,
        prompt=prompt,
        effort=config["effort"]["adversary"],
        max_tokens=500,
    )
    text = str(verdict).strip()
    if text.upper().startswith("UNSAFE"):
        objection = text.split(":", 1)[1].strip() if ":" in text else text
        log.info("adversary downgraded %s: %s", pr.slug, objection)
        return False, objection
    return True, ""
