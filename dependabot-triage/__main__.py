"""Entry point. Wires the phases together and guarantees the email always goes out.

Run order: harvest -> assess -> execute -> diagnose -> record -> report. Any
failure after harvest still produces a report explaining what stopped, because a
silent night is indistinguishable from a healthy one and that is how automation
quietly rots.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

import assess as assess_mod
import diagnose as diagnose_mod
import execute as execute_mod
import gh
import guards
import harvest as harvest_mod
import metrics
import report as report_mod
from budget import Budget, BudgetExceeded
from llm import Client, ModelResponseInvalid
from models import Action

HERE = Path(__file__).parent
log = logging.getLogger("triage")


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def check_tripwires(harvests: list[harvest_mod.RepoHarvest], owner: str) -> list[str]:
    """Re-check every tamper baseline. Any drift ends the run.

    This is the layer that catches a required check disappearing, a workflow file
    changing, or a coverage threshold dropping while the run was in flight —
    whatever the cause.
    """
    problems: list[str] = []
    for h in harvests:
        live_contexts = gh.required_contexts(h.repo, owner, h.base)
        result = guards.guard_protection_unchanged(h.baseline, live_contexts)
        if not result.passed:
            problems.append(f"{h.repo}: {result.detail}")

        live_shas = gh.file_shas(h.repo, owner, ".github/workflows", h.base)
        for path, sha in h.baseline.workflow_file_shas.items():
            if live_shas.get(path) != sha:
                problems.append(f"{h.repo}: workflow {path} changed during the run")

        live_thresholds: dict[str, float] = {}
        for path in list(live_shas) + ["pyproject.toml", "package.json"]:
            text = gh.file_text(h.repo, owner, path, h.base)
            for key, value in guards.extract_thresholds(text).items():
                live_thresholds[key] = max(live_thresholds.get(key, value), value)
        ratchet = guards.guard_thresholds_not_lowered(h.baseline, live_thresholds)
        if not ratchet.passed:
            problems.append(f"{h.repo}: {ratchet.detail}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dependabot-triage")
    parser.add_argument("--config", type=Path, default=HERE / "config.yml")
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="assess and report without commenting, rebasing, or merging",
    )
    parser.add_argument("--repos", default="", help="comma-separated subset of configured repos")
    parser.add_argument("--run-url", default=os.environ.get("RUN_URL", ""))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--ledger", type=Path, default=HERE / "out" / "ledger.json")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="skip the report email (local testing); dry runs still email by default",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    if args.repos:
        wanted = {name.strip() for name in args.repos.split(",") if name.strip()}
        for entry in config["repos"]:
            entry["enabled"] = entry["name"] in wanted

    now = datetime.now(UTC)
    owner = config["owner"]
    aborted = ""

    budget = Budget(
        max_per_run=config["budget"]["max_per_run"],
        max_per_month=config["budget"]["max_per_month"],
        month_to_date=metrics.month_to_date_spend(now),
    )

    # --- Phase 1 ---------------------------------------------------------
    # A permission gap here must not look like a quiet night. Reading branch
    # protection needs 'Administration: Read'; without it the API's refusal is
    # easy to mistake for "no checks are required", which would make the agent
    # silently refuse every PR while appearing to work.
    try:
        harvests = harvest_mod.harvest_all(config)
    except gh.ProtectionUnreadable as exc:
        log.error("%s", exc)
        stats = metrics.summarise([])
        body = report_mod.render_html(
            summary="",
            decisions=[],
            stats=stats,
            rolling=metrics.rolling(now=now),
            precision=metrics.precision(now=now),
            budget=budget.summary(),
            run_url=args.run_url,
            when=now,
            dry_run=args.dry_run,
            aborted=str(exc),
        )
        report_mod.send(
            body,
            report_mod.subject_line(stats, config, now, aborted=str(exc), dry_run=args.dry_run),
            config,
            suppress=args.no_email,
        )
        return 1

    open_prs = [pr for h in harvests for pr in h.dependabot_prs]

    if not open_prs:
        log.info("no open Dependabot PRs; exiting before any model call")
        stats = metrics.summarise([])
        # Write the ledger even on the quiet path, so "no artifact" always means
        # the job died rather than "there was nothing to do".
        args.ledger.parent.mkdir(parents=True, exist_ok=True)
        args.ledger.write_text(
            json.dumps(
                {
                    "run_id": args.run_id,
                    "at": now.isoformat(),
                    "dry_run": args.dry_run,
                    "aborted": "",
                    "stats": stats,
                    "budget": budget.summary(),
                    "decisions": [],
                    "repos_checked": [
                        {"repo": h.repo, "required_checks": sorted(h.baseline.required_contexts)}
                        for h in harvests
                    ],
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        body = report_mod.render_html(
            summary="No open Dependabot pull requests tonight; nothing to do.",
            decisions=[],
            stats=stats,
            rolling=metrics.rolling(),
            precision=metrics.precision(),
            budget=budget.summary(),
            run_url=args.run_url,
            when=now,
            dry_run=args.dry_run,
        )
        report_mod.send(
            body,
            report_mod.subject_line(stats, config, now, dry_run=args.dry_run),
            config,
            suppress=args.no_email,
        )
        return 0

    log.info("%d open Dependabot PR(s) across %d repo(s)", len(open_prs), len(harvests))

    client = Client(budget, dry_run=args.dry_run)
    result = execute_mod.ExecutionResult()

    try:
        # --- Phase 2 -----------------------------------------------------
        assessments = assess_mod.assess(harvests, client, config)

        # --- Phase 3 -----------------------------------------------------
        result = execute_mod.execute(
            harvests,
            assessments,
            client,
            config,
            run_url=args.run_url,
            dry_run=args.dry_run,
        )

        # --- Phase 4 -----------------------------------------------------
        by_repo = {h.repo: h for h in harvests}
        for decision in result.decisions:
            if decision.merged or decision.action is Action.MERGE:
                continue
            h = by_repo.get(decision.repo)
            pr = next(
                (p for p in (h.dependabot_prs if h else []) if p.number == decision.number),
                None,
            )
            if pr is None or not pr.failing_required(h.baseline.required_contexts):
                continue
            try:
                decision.diagnosis = diagnose_mod.diagnose(
                    pr, h.baseline.required_contexts, client, config, dry_run=args.dry_run
                )
            except (BudgetExceeded, ModelResponseInvalid) as exc:
                log.warning("diagnosis skipped for %s: %s", pr.slug, exc)

        # --- Tripwires ---------------------------------------------------
        if not args.dry_run:
            problems = check_tripwires(harvests, owner)
            if problems:
                aborted = "tamper tripwire fired — " + "; ".join(problems)
                log.error(aborted)

    except BudgetExceeded as exc:
        aborted = str(exc)
        log.error("budget ceiling hit: %s", exc)
    except ModelResponseInvalid as exc:
        aborted = f"model response was unusable: {exc}"
        log.error(aborted)
    except gh.GhError as exc:
        aborted = f"GitHub API failure: {exc}"
        log.error(aborted)

    # --- Phase 6 ---------------------------------------------------------
    stats = metrics.summarise(result.decisions)
    metrics.record_run(
        result.decisions,
        budget.summary(),
        run_id=args.run_id,
        now=now,
        dry_run=args.dry_run,
    )

    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    args.ledger.write_text(
        json.dumps(
            {
                "run_id": args.run_id,
                "at": now.isoformat(),
                "dry_run": args.dry_run,
                "aborted": aborted,
                "stats": stats,
                "budget": budget.summary(),
                "decisions": [
                    {
                        "repo": d.repo,
                        "number": d.number,
                        "title": d.title,
                        "tier": d.tier.value,
                        "action": d.action.value,
                        "reason": d.reason,
                        "merged": d.merged,
                        "rebased": d.rebased,
                        "deferred": d.deferred,
                        "deciding_question": d.deciding_question,
                        "diagnosis": d.diagnosis,
                        "guards": [
                            {"id": g.guard_id, "passed": g.passed, "detail": g.detail}
                            for g in d.guard_results
                        ],
                    }
                    for d in result.decisions
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    log.info("ledger written to %s", args.ledger)

    # --- Phase 5 ---------------------------------------------------------
    summary = report_mod.compose_summary(stats, client, config, aborted=aborted)
    body = report_mod.render_html(
        summary=summary,
        decisions=result.decisions,
        stats=stats,
        rolling=metrics.rolling(now=now),
        precision=metrics.precision(now=now),
        budget=budget.summary(),
        run_url=args.run_url,
        when=now,
        dry_run=args.dry_run,
        aborted=aborted,
    )
    report_mod.send(
        body,
        report_mod.subject_line(stats, config, now, aborted=aborted, dry_run=args.dry_run),
        config,
        suppress=args.no_email,
    )

    log.info(
        "done: %d merged, %d decisions, $%.4f spent",
        stats["merged"],
        stats["seen"],
        budget.run_usd,
    )
    return 1 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
