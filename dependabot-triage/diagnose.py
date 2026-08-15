"""Phase 4 — explain a failing check. Produces a comment, never an action.

Runs on the cheap model by default and escalates once when that model reports low
confidence in its own answer. The output is prose destined for a PR comment; no
part of the system reads it back as an instruction.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import gh
from llm import Client
from models import PullRequest

log = logging.getLogger(__name__)

DIAGNOSE_PROMPT = (Path(__file__).parent / "prompts" / "diagnose.md").read_text(encoding="utf-8")

CONFIDENCE_RE = re.compile(r"CONFIDENCE:\s*(HIGH|MEDIUM|LOW)", re.IGNORECASE)
LOG_TAIL_LINES = 120


def _failed_check_names(pr: PullRequest, required: frozenset[str]) -> list[str]:
    return [c.name for c in pr.failing_required(required)]


def _fetch_log_tail(repo: str, owner: str, number: int) -> str:
    """Last lines of the failing job's log.

    Best-effort: the log endpoint is noisy and occasionally unavailable, and a
    missing log is a normal outcome rather than a reason to fail the run.
    """
    try:
        raw = gh._run(
            ["run", "list", "--repo", f"{owner}/{repo}", "--limit", "1", "--json", "databaseId"]
        )
    except gh.GhError as exc:
        log.info("could not list runs for %s#%s: %s", repo, number, exc)
        return ""
    import json

    runs = json.loads(raw) if raw.strip() else []
    if not runs:
        return ""
    try:
        logs = gh._run(
            [
                "run",
                "view",
                str(runs[0]["databaseId"]),
                "--repo",
                f"{owner}/{repo}",
                "--log-failed",
            ],
            timeout=180,
        )
    except gh.GhError as exc:
        log.info("could not read failed log for %s#%s: %s", repo, number, exc)
        return ""
    return "\n".join(logs.splitlines()[-LOG_TAIL_LINES:])


def diagnose(
    pr: PullRequest,
    required: frozenset[str],
    client: Client,
    config: dict,
    *,
    dry_run: bool = True,
) -> str:
    """Return a short diagnosis of the PR's failing checks."""
    failing = _failed_check_names(pr, required)
    if not failing:
        return ""

    log_tail = "" if dry_run else _fetch_log_tail(pr.repo, config["owner"], pr.number)

    prompt = "\n".join(
        [
            f"Repository: {pr.repo}  PR #{pr.number}",
            f"Title: {pr.title}",
            f"Ecosystem: {pr.ecosystem}",
            f"Changed files: {', '.join(pr.paths)}",
            f"Failing required checks: {', '.join(failing)}",
            "",
            "Failing job log (tail):",
            log_tail or "(no log available)",
        ]
    )

    text, _ = client.complete(
        phase="diagnose",
        model=config["models"]["diagnose"],
        system=DIAGNOSE_PROMPT,
        prompt=prompt,
        effort=config["effort"]["diagnose"],
        max_tokens=1200,
    )

    match = CONFIDENCE_RE.search(str(text))
    confidence = match.group(1).upper() if match else "LOW"

    if confidence == "LOW" and config["models"]["diagnose"] != config["models"]["assess"]:
        log.info("escalating diagnosis of %s to %s", pr.slug, config["models"]["assess"])
        text, _ = client.complete(
            phase="diagnose-escalated",
            model=config["models"]["assess"],
            system=DIAGNOSE_PROMPT,
            prompt=prompt,
            effort="medium",
            max_tokens=1200,
        )

    return CONFIDENCE_RE.sub("", str(text)).strip()
