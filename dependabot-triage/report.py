"""Phase 5 — one email covering the whole stack.

Every number and every reason in the email is rendered from the ledger in code.
The model contributes exactly one paragraph of prose, so the report cannot
misstate what happened even if the model is having an off night.
"""

from __future__ import annotations

import html
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from llm import Client
from models import Action, Decision, Repeat, RiskTier

log = logging.getLogger(__name__)

SUMMARY_PROMPT = (Path(__file__).parent / "prompts" / "summary.md").read_text(encoding="utf-8")
RESEND_ENDPOINT = "https://api.resend.com/emails"
USER_AGENT = "wcmchenry3-stack-dependabot-triage/1.0 (+https://github.com/wcmchenry3-stack/.github)"

TIER_BADGE = {
    RiskTier.LOW: ("#1a7f37", "LOW"),
    RiskTier.MEDIUM: ("#9a6700", "MEDIUM"),
    RiskTier.HIGH: ("#bc4c00", "HIGH"),
    RiskTier.BLOCKED: ("#cf222e", "BLOCKED"),
}
ACTION_MARK = {
    Action.MERGE: ("#1a7f37", "merged"),
    Action.COMMENT: ("#9a6700", "left for review"),
    Action.SKIP: ("#cf222e", "refused"),
    Action.REBASE: ("#0969da", "rebasing"),
}


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def compose_summary(stats: dict, client: Client | None, config: dict, aborted: str = "") -> str:
    """One paragraph of prose. Falls back to a deterministic sentence if unavailable."""
    if aborted:
        return f"The run stopped early: {aborted}. Nothing further was merged."
    if client is None:
        return "Nightly Dependabot triage completed."

    prompt = json.dumps(stats, indent=2, sort_keys=True)
    try:
        text, _ = client.complete(
            phase="summary",
            model=config["models"]["summary"],
            system=SUMMARY_PROMPT,
            prompt=prompt,
            effort=config["effort"]["summary"],
            max_tokens=300,
        )
        return str(text).strip()
    except Exception as exc:  # noqa: BLE001 - the email must go out regardless
        log.warning("summary generation failed, using fallback: %s", exc)
        return "Nightly Dependabot triage completed; see the table below."


def _decision_row(decision: Decision, run_url: str) -> str:
    colour, label = ACTION_MARK.get(decision.action, ("#57606a", decision.action.value))
    tier_colour, tier_label = TIER_BADGE[decision.tier]
    ref = f"#{decision.number}"
    reason = _esc(decision.reason)
    if decision.deciding_question and decision.deciding_question != "NONE":
        reason += f' <span style="color:#57606a">({_esc(decision.deciding_question)})</span>'
    if decision.failed_guard:
        reason += f' <span style="color:#cf222e">[{_esc(decision.failed_guard)}]</span>'
    return (
        "<tr>"
        f'<td style="padding:6px 10px;white-space:nowrap"><code>{ref}</code></td>'
        f'<td style="padding:6px 10px">{_esc(decision.title)}</td>'
        f'<td style="padding:6px 10px;white-space:nowrap;color:{tier_colour};font-weight:600">{tier_label}</td>'
        f'<td style="padding:6px 10px;white-space:nowrap;color:{colour}">{label}</td>'
        f'<td style="padding:6px 10px;color:#57606a">{reason}</td>'
        "</tr>"
    )


def _repeat_row(r: Repeat) -> str:
    # One flat table spans every repo (unlike the per-repo decision sections
    # above it), so each row names its own repo rather than relying on a
    # shared heading.
    tier_colour, tier_label = TIER_BADGE[r.tier]
    ref = f"{_esc(r.repo)}#{r.number}"
    reason = _esc(r.reason)
    if r.deciding_question and r.deciding_question != "NONE":
        reason += f' <span style="color:#57606a">({_esc(r.deciding_question)})</span>'
    return (
        "<tr>"
        f'<td style="padding:6px 10px;white-space:nowrap"><code>{ref}</code></td>'
        f'<td style="padding:6px 10px">{_esc(r.title)}</td>'
        f'<td style="padding:6px 10px;white-space:nowrap;color:{tier_colour};font-weight:600">{tier_label}</td>'
        f'<td style="padding:6px 10px;color:#57606a">{reason}</td>'
        f'<td style="padding:6px 10px;white-space:nowrap;color:#57606a">since {_esc(r.last_seen_at)}</td>'
        "</tr>"
    )


def render_html(
    *,
    summary: str,
    decisions: list[Decision],
    stats: dict,
    rolling: dict,
    precision: dict,
    budget: dict,
    run_url: str,
    when: datetime,
    dry_run: bool,
    aborted: str = "",
    repeats: list[Repeat] | None = None,
) -> str:
    """Build the email body. Pure function over the ledger — no I/O, no model."""
    repeats = repeats or []
    by_repo: dict[str, list[Decision]] = {}
    for decision in decisions:
        by_repo.setdefault(decision.repo, []).append(decision)

    banner = ""
    if aborted:
        banner = (
            '<div style="background:#ffebe9;border:1px solid #cf222e;padding:12px;'
            'border-radius:6px;margin-bottom:16px"><strong>Run aborted:</strong> '
            f"{_esc(aborted)}</div>"
        )
    elif dry_run:
        banner = (
            '<div style="background:#ddf4ff;border:1px solid #0969da;padding:12px;'
            'border-radius:6px;margin-bottom:16px"><strong>Dry run</strong> — '
            "decisions below were not carried out.</div>"
        )

    head = (
        f'<span style="color:#1a7f37">{stats["merged"]} merged</span> · '
        f'{stats["seen"] - stats["merged"] - stats["blocked_by_guard"]} left for review · '
        f'<span style="color:#cf222e">{stats["blocked_by_guard"]} refused</span>'
    )
    if repeats:
        head += f' · <span style="color:#57606a">{len(repeats)} unchanged</span>'

    sections: list[str] = []
    for repo in sorted(by_repo):
        items = by_repo[repo]
        merged = sum(1 for d in items if d.merged)
        note = f"{merged} merged" if merged else "nothing merged"
        rows = "".join(_decision_row(d, run_url) for d in items)
        sections.append(
            f'<h3 style="margin:22px 0 6px;font-size:15px">{_esc(repo)} '
            f'<span style="font-weight:400;color:#57606a">— {len(items)} PR(s), {note}</span></h3>'
            '<table style="border-collapse:collapse;width:100%;font-size:13px">'
            f"{rows}</table>"
        )
    if not sections and not repeats:
        sections.append('<p style="color:#57606a">No open Dependabot pull requests.</p>')

    if repeats:
        repeat_rows = "".join(_repeat_row(r) for r in repeats)
        sections.append(
            '<h3 style="margin:22px 0 6px;font-size:15px;color:#57606a">Already triaged '
            f'<span style="font-weight:400">— {len(repeats)} PR(s) unchanged since a previous '
            "run, not re-reviewed tonight</span></h3>"
            '<table style="border-collapse:collapse;width:100%;font-size:13px">'
            f"{repeat_rows}</table>"
        )

    return f"""<!doctype html>
<html><body style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;
color:#1f2328;max-width:760px;margin:0 auto;padding:20px">
<h2 style="margin:0 0 4px;font-size:18px">Dependabot Triage — {when:%a %d %b %Y}</h2>
<p style="margin:0 0 16px;color:#57606a;font-size:13px">{head}</p>
{banner}
<p style="font-size:14px;line-height:1.55">{_esc(summary)}</p>
<table style="border-collapse:collapse;font-size:13px;margin:18px 0;width:100%">
<tr style="background:#f6f8fa">
  <td style="padding:8px 10px">Autonomy rate ({rolling.get('window_days', 30)}d)</td>
  <td style="padding:8px 10px;font-weight:600">{_pct(rolling.get('autonomy_rate'))}</td>
  <td style="padding:8px 10px">Precision</td>
  <td style="padding:8px 10px;font-weight:600">{_pct(precision.get('precision'))}</td>
</tr>
<tr>
  <td style="padding:8px 10px">Spend tonight</td>
  <td style="padding:8px 10px;font-weight:600">${budget.get('run_usd', 0):.2f}</td>
  <td style="padding:8px 10px">Month to date</td>
  <td style="padding:8px 10px;font-weight:600">${budget.get('month_usd', 0):.2f}
      <span style="color:#57606a;font-weight:400">/ ${budget.get('max_per_month', 0):.2f}</span></td>
</tr>
</table>
{''.join(sections)}
<hr style="border:0;border-top:1px solid #d0d7de;margin:24px 0">
<p style="font-size:12px;color:#57606a">
  <a href="{_esc(run_url)}">Run log</a> · ledger attached as a workflow artifact ·
  the agent may only comment, request a Dependabot rebase, and merge — it never edits a file.
</p>
</body></html>"""


def send(html_body: str, subject: str, config: dict, *, suppress: bool = False) -> bool:
    """Deliver via Resend. Returns True when the email was accepted.

    Deliberately *not* gated on dry-run. A dry run suppresses GitHub mutations —
    comments, rebases, merges — because those change the world. The report
    changes nothing, and it is the thing a dry run exists to let you evaluate;
    withholding it would make the staged rollout period produce no signal at all.
    Dry-run reports are marked in both the subject and the body, so the two can
    never be confused.
    """
    api_key = os.environ.get("RESEND_API_KEY", "")
    if suppress or not api_key:
        reason = "--no-email" if suppress else "RESEND_API_KEY not set"
        log.info("not sending email (%s); subject was %r", reason, subject)
        return False

    payload = json.dumps(
        {
            "from": config["report"]["from_address"],
            "to": [config["report"]["to_address"]],
            "subject": subject,
            "html": html_body,
        }
    ).encode("utf-8")

    request = urllib.request.Request(  # noqa: S310 - fixed HTTPS endpoint
        RESEND_ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Resend sits behind Cloudflare, which rejects urllib's default
            # "Python-urllib/3.x" signature with a 403 and edge error 1010 —
            # before the API key is ever looked at. A real User-Agent avoids it.
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            log.info("email accepted by Resend (%s)", response.status)
            return True
    except urllib.error.HTTPError as exc:
        log.error("Resend rejected the email (%s): %s", exc.code, exc.read().decode())
    except urllib.error.URLError as exc:
        log.error("could not reach Resend: %s", exc)
    return False


def subject_line(
    stats: dict,
    config: dict,
    when: datetime,
    aborted: str = "",
    dry_run: bool = False,
    repeats: int = 0,
) -> str:
    prefix = config["report"]["subject_prefix"]
    if dry_run:
        prefix = f"[DRY RUN] {prefix}"
    if aborted:
        return f"{prefix} — ABORTED — {when:%a %d %b}"
    verb = "would merge" if dry_run else "merged"
    subject = f"{prefix} — {stats['merged']} {verb}, {stats['seen'] - stats['merged']} pending"
    if repeats:
        subject += f", {repeats} unchanged"
    return f"{subject} — {when:%a %d %b}"
