"""Phase 6 — persistent history, so "is this working?" has an answer.

Two numbers matter, and only one of them is the one people ask for first.

*Autonomy rate* — merged over eligible — answers "how much is the agent actually
doing". *Precision* — merges that did not later have to be undone — answers
whether the autonomy rate was earned. Optimising the first without watching the
second is how an automation like this quietly becomes something you stop trusting.

Attribution matters more than either total: recording which rubric question caused
each non-merge tells you where the ceiling is, and therefore whether the fix is a
better prompt or a configuration change.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from models import Action, Decision

log = logging.getLogger(__name__)

HISTORY_DIR = Path(__file__).parent / "history"


def _month_file(when: datetime) -> Path:
    return HISTORY_DIR / f"{when:%Y-%m}.jsonl"


def _guard_summary(decision: Decision) -> list[dict]:
    return [
        {"guard_id": g.guard_id, "passed": g.passed, "detail": g.detail}
        for g in decision.guard_results
    ]


def record_run(
    decisions: list[Decision],
    budget_summary: dict,
    *,
    run_id: str,
    now: datetime,
    dry_run: bool,
    ci_seconds: dict[str, float] | None = None,
) -> Path:
    """Append one line per decision plus a run summary. Returns the file written."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = _month_file(now)
    stamp = now.isoformat()

    lines: list[str] = []
    for decision in decisions:
        lines.append(
            json.dumps(
                {
                    "type": "decision",
                    "run_id": run_id,
                    "at": stamp,
                    "dry_run": dry_run,
                    "repo": decision.repo,
                    "number": decision.number,
                    "title": decision.title,
                    "tier": decision.tier.value,
                    "action": decision.action.value,
                    "merged": decision.merged,
                    "rebased": decision.rebased,
                    "deferred": decision.deferred,
                    "deciding_question": decision.deciding_question,
                    "failed_guard": decision.failed_guard,
                    "reason": decision.reason,
                    "guards": _guard_summary(decision),
                },
                sort_keys=True,
            )
        )

    stats = summarise(decisions)
    lines.append(
        json.dumps(
            {
                "type": "run",
                "run_id": run_id,
                "at": stamp,
                "dry_run": dry_run,
                "budget": budget_summary,
                "ci_seconds": ci_seconds or {},
                **stats,
            },
            sort_keys=True,
        )
    )

    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    log.info("wrote %d history line(s) to %s", len(lines), path.name)
    return path


def summarise(decisions: list[Decision]) -> dict:
    """Per-run counts, including the autonomy rate and its attribution."""
    merged = [d for d in decisions if d.merged]
    deferred = [d for d in decisions if d.deferred]
    blocked = [d for d in decisions if d.action is Action.SKIP]

    # A PR that was already red before the run started, through no fault of the
    # bump, was never mergeable tonight — counting it against the agent would
    # make the autonomy rate measure repo health rather than agent behaviour.
    eligible = [d for d in decisions if not d.deferred]

    return {
        "seen": len(decisions),
        "merged": len(merged),
        "rebased_then_merged": len([d for d in merged if d.rebased]),
        "deferred": len(deferred),
        "blocked_by_guard": len(blocked),
        "eligible": len(eligible),
        "autonomy_rate": round(len(merged) / len(eligible), 4) if eligible else None,
        "by_tier": dict(Counter(d.tier.value for d in decisions)),
        "by_deciding_question": dict(
            Counter(
                d.deciding_question for d in decisions if d.deciding_question not in ("", "NONE")
            )
        ),
        "by_failed_guard": dict(Counter(d.failed_guard for d in blocked if d.failed_guard)),
        "by_repo": dict(Counter(d.repo for d in decisions)),
    }


def load_history(since: datetime | None = None) -> list[dict]:
    """Read every history line, optionally filtered to entries after ``since``."""
    if not HISTORY_DIR.exists():
        return []
    entries: list[dict] = []
    for path in sorted(HISTORY_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                log.warning("skipping malformed history line in %s", path.name)
                continue
            if since is not None:
                try:
                    when = datetime.fromisoformat(entry["at"])
                except (KeyError, ValueError):
                    continue
                if when < since:
                    continue
            entries.append(entry)
    return entries


def rolling(days: int = 30, now: datetime | None = None) -> dict:
    """Rolling window used in the email header.

    Dry-run entries are excluded: they describe what the agent *would* have done,
    and mixing them into the live rate would overstate it.
    """
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    decisions = [
        e for e in load_history(since) if e.get("type") == "decision" and not e.get("dry_run")
    ]
    if not decisions:
        return {"window_days": days, "decisions": 0, "autonomy_rate": None, "merged": 0}

    merged = [d for d in decisions if d.get("merged")]
    eligible = [d for d in decisions if not d.get("deferred")]
    return {
        "window_days": days,
        "decisions": len(decisions),
        "merged": len(merged),
        "eligible": len(eligible),
        "autonomy_rate": round(len(merged) / len(eligible), 4) if eligible else None,
        "by_deciding_question": dict(
            Counter(
                d.get("deciding_question")
                for d in decisions
                if d.get("deciding_question") not in (None, "", "NONE")
            )
        ),
        "by_failed_guard": dict(
            Counter(d.get("failed_guard") for d in decisions if d.get("failed_guard"))
        ),
    }


def month_to_date_spend(now: datetime | None = None) -> float:
    """Spend already booked this calendar month, for the monthly ceiling."""
    now = now or datetime.now(UTC)
    total = 0.0
    for entry in load_history():
        if entry.get("type") != "run" or entry.get("dry_run"):
            continue
        try:
            when = datetime.fromisoformat(entry["at"])
        except (KeyError, ValueError):
            continue
        if (when.year, when.month) != (now.year, now.month):
            continue
        total += float((entry.get("budget") or {}).get("run_usd", 0.0))
    return round(total, 4)


def record_regression(repo: str, number: int, run_id: str, note: str, now: datetime) -> None:
    """Mark a previously merged PR as having caused a problem.

    Written by the follow-up check that looks for a red base branch or a revert in
    the seven days after a merge. This is what turns the precision number from an
    assumption into a measurement.
    """
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with _month_file(now).open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "regression",
                    "run_id": run_id,
                    "at": now.isoformat(),
                    "repo": repo,
                    "number": number,
                    "note": note,
                },
                sort_keys=True,
            )
            + "\n"
        )


def precision(days: int = 30, now: datetime | None = None) -> dict:
    """Share of merges that did not have to be undone."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    entries = load_history(since)
    merged = [
        e
        for e in entries
        if e.get("type") == "decision" and e.get("merged") and not e.get("dry_run")
    ]
    regressions = [e for e in entries if e.get("type") == "regression"]
    if not merged:
        return {"window_days": days, "merged": 0, "regressions": 0, "precision": None}
    return {
        "window_days": days,
        "merged": len(merged),
        "regressions": len(regressions),
        "precision": round(1 - len(regressions) / len(merged), 4),
    }
