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
from models import CheckRun, PullRequest

log = logging.getLogger(__name__)

DIAGNOSE_PROMPT = (Path(__file__).parent / "prompts" / "diagnose.md").read_text(encoding="utf-8")

CONFIDENCE_RE = re.compile(r"CONFIDENCE:\s*(HIGH|MEDIUM|LOW)", re.IGNORECASE)
RUN_ID_RE = re.compile(r"/actions/runs/(\d+)")
LOG_TAIL_LINES = 120


def _run_id_from_checks(checks: list[CheckRun]) -> str:
    """Pull a workflow-run ID directly out of a failing check's own URL.

    A push commonly triggers several independent workflow files at once (lint,
    security scans, policy checks, ...), each becoming its own run. "The most
    recent run for this branch" is then a guess that lands on whichever of those
    finished last — as likely to be an unrelated green run as the one that
    actually failed, and ``--log-failed`` against a green run silently returns
    nothing. The check's own ``link`` field names its run unambiguously, so it
    is always preferred when present.
    """
    for check in checks:
        match = RUN_ID_RE.search(check.details_url)
        if match:
            return match.group(1)
    return ""


def _latest_run_id_for_branch(repo: str, owner: str, number: int, head_ref: str) -> str:
    """Fallback: the most recent workflow run on the PR's head branch.

    Only reached when no failing check carries a usable ``link`` (an older
    harvest, or a check type `gh` doesn't expose one for). This is a guess, not
    a lookup — see ``_run_id_from_checks`` — which is why it is the fallback and
    not the primary path.
    """
    if not head_ref:
        log.warning(
            "no head ref for %s#%s; skipping log fetch rather than "
            "risk reading an unrelated run",
            repo,
            number,
        )
        return ""
    args = [
        "run",
        "list",
        "--repo",
        f"{owner}/{repo}",
        "--limit",
        "1",
        "--branch",
        head_ref,
        "--json",
        "databaseId",
    ]
    try:
        raw = gh._run(args)
    except gh.GhError as exc:
        log.info("could not list runs for %s#%s: %s", repo, number, exc)
        return ""
    import json

    runs = json.loads(raw) if raw.strip() else []
    return str(runs[0]["databaseId"]) if runs else ""


def fetch_log_tail(
    repo: str,
    owner: str,
    number: int,
    head_ref: str = "",
    failing_checks: list[CheckRun] | None = None,
) -> str:
    """Last lines of the failing job's log for *this* PR.

    Best-effort: the log endpoint is noisy and occasionally unavailable, and a
    missing log is a normal outcome rather than a reason to fail the run.
    """
    run_id = _run_id_from_checks(failing_checks or [])
    if not run_id:
        run_id = _latest_run_id_for_branch(repo, owner, number, head_ref)
    if not run_id:
        return ""
    try:
        logs = gh._run(
            ["run", "view", run_id, "--repo", f"{owner}/{repo}", "--log-failed"],
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
    failing_checks = pr.failing_required(required)
    if not failing_checks:
        return ""
    failing = [c.name for c in failing_checks]

    log_tail = (
        ""
        if dry_run
        else fetch_log_tail(pr.repo, config["owner"], pr.number, pr.head_ref, failing_checks)
    )

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
