"""Phase 1 — collect Dependabot PRs and the tamper baseline. No model involved.

Everything the assessment and the guards need is gathered here, so a dry run can
be replayed offline from the harvested JSON without touching GitHub again.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import gh
import guards
from models import ChangedFile, CheckRun, PullRequest, RepoBaseline

log = logging.getLogger(__name__)

DIFF_HEADER_RE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")

# Dependabot's title format, e.g.
#   chore(deps): bump lodash from 4.17.20 to 4.17.21
#   Bump actions/setup-python from 6 to 7
BUMP_RE = re.compile(
    r"bump\s+(?P<pkg>\S+)\s+from\s+(?P<from>\S+)\s+to\s+(?P<to>\S+)", re.IGNORECASE
)

ECOSYSTEM_LABELS = {
    "javascript": "npm",
    "npm_and_yarn": "npm",
    "python": "pip",
    "github_actions": "github-actions",
    "ruby": "bundler",
    "go": "gomod",
    "swift": "swift",
}


@dataclass
class RepoHarvest:
    """Everything collected for one repo in a single pass."""

    repo: str
    base: str
    merge_method: str
    baseline: RepoBaseline
    prs: list[PullRequest] = field(default_factory=list)

    @property
    def dependabot_prs(self) -> list[PullRequest]:
        return [p for p in self.prs if p.author in guards.DEPENDABOT_AUTHORS]


def split_diff(diff: str) -> dict[str, str]:
    """Split a unified diff into ``{path: patch}``.

    ``gh pr diff`` returns one blob for the whole PR; the guards reason per file,
    so the blob is cut on ``diff --git`` boundaries. The *b*-side path is used, so
    a rename reports its destination.
    """
    patches: dict[str, list[str]] = {}
    current: str | None = None
    for line in diff.splitlines():
        header = DIFF_HEADER_RE.match(line)
        if header:
            current = header.group("b")
            patches.setdefault(current, [])
            continue
        if current is not None:
            patches[current].append(line)
    return {path: "\n".join(lines) for path, lines in patches.items()}


def parse_bump(title: str) -> tuple[str, str, str]:
    """Extract ``(package, from_version, to_version)`` from a Dependabot title."""
    match = BUMP_RE.search(title)
    if not match:
        return ("", "", "")
    return (match.group("pkg"), match.group("from"), match.group("to"))


def detect_ecosystem(labels: list[str], paths: list[str]) -> str:
    """Best-effort ecosystem, from Dependabot's own labels then from paths."""
    for label in labels:
        if label in ECOSYSTEM_LABELS:
            return ECOSYSTEM_LABELS[label]
    if any(p.startswith(".github/workflows/") for p in paths):
        return "github-actions"
    if any("package.json" in p or "lock" in p.lower() for p in paths):
        return "npm"
    if any("requirements" in p or "pyproject" in p or "Pipfile" in p for p in paths):
        return "pip"
    return "unknown"


def _bucket_to_conclusion(check: dict) -> tuple[str, str]:
    """Normalise ``gh pr checks`` output onto the CheckRun vocabulary."""
    bucket = (check.get("bucket") or "").lower()
    state = (check.get("state") or "").upper()
    if bucket == "pass":
        return "SUCCESS", "COMPLETED"
    if bucket == "fail":
        return state or "FAILURE", "COMPLETED"
    if bucket == "skipping":
        return "SKIPPED", "COMPLETED"
    if bucket == "pending":
        return "", "IN_PROGRESS"
    if state in ("SUCCESS", "NEUTRAL", "SKIPPED"):
        return state, "COMPLETED"
    if state in ("QUEUED", "IN_PROGRESS", "PENDING"):
        return "", "IN_PROGRESS"
    return state or "UNKNOWN", "COMPLETED"


def harvest_repo(
    repo: str, owner: str, base: str, merge_method: str, *, fetch_diffs: bool = True
) -> RepoHarvest:
    """Collect open Dependabot PRs plus the repo's tamper baseline."""
    log.info("harvesting %s/%s", owner, repo)

    contexts = gh.required_contexts(repo, owner, base)
    workflow_shas = gh.file_shas(repo, owner, ".github/workflows", base)
    dependabot_sha = ""
    for entry_path in (".github/dependabot.yml", ".github/dependabot.yaml"):
        shas = gh.file_shas(repo, owner, ".github", base)
        if entry_path in shas:
            dependabot_sha = shas[entry_path]
            break

    thresholds: dict[str, float] = {}
    for path in list(workflow_shas) + ["pyproject.toml", "package.json"]:
        text = gh.file_text(repo, owner, path, base)
        for key, value in guards.extract_thresholds(text).items():
            thresholds[key] = max(thresholds.get(key, value), value)

    baseline = RepoBaseline(
        repo=repo,
        required_contexts=contexts,
        workflow_file_shas=workflow_shas,
        dependabot_config_sha=dependabot_sha,
        thresholds=thresholds,
    )

    prs: list[PullRequest] = []
    for raw in gh.list_open_prs(repo, owner):
        author = (raw.get("author") or {}).get("login", "")
        if author in ("dependabot", "dependabot[bot]"):
            author = "app/dependabot"
        if author not in guards.DEPENDABOT_AUTHORS:
            continue  # never touch a PR the agent did not author-check

        number = raw["number"]
        labels = [entry["name"] for entry in raw.get("labels") or []]
        paths = [entry["path"] for entry in raw.get("files") or []]

        patches: dict[str, str] = {}
        if fetch_diffs:
            try:
                patches = split_diff(gh.pr_diff(repo, owner, number))
            except gh.GhError as exc:
                log.warning("no diff for %s#%s: %s", repo, number, exc)

        files = [
            ChangedFile(
                path=entry["path"],
                additions=entry.get("additions", 0),
                deletions=entry.get("deletions", 0),
                patch=patches.get(entry["path"], ""),
            )
            for entry in raw.get("files") or []
        ]

        checks = []
        for entry in gh.pr_checks(repo, owner, number):
            conclusion, status = _bucket_to_conclusion(entry)
            checks.append(CheckRun(name=entry["name"], conclusion=conclusion, status=status))

        prs.append(
            PullRequest(
                repo=repo,
                number=number,
                title=raw.get("title", ""),
                author=author,
                base_ref=raw.get("baseRefName", base),
                head_sha=raw.get("headRefOid", ""),
                files=files,
                labels=labels,
                checks=checks,
                mergeable=raw.get("mergeable", "UNKNOWN"),
                merge_state_status=raw.get("mergeStateStatus", "UNKNOWN"),
                body=raw.get("body", "") or "",
                ecosystem=detect_ecosystem(labels, paths),
                is_group="group" in raw.get("title", "").lower(),
            )
        )

    log.info("%s: %d Dependabot PR(s), %d required check(s)", repo, len(prs), len(contexts))
    return RepoHarvest(repo=repo, base=base, merge_method=merge_method, baseline=baseline, prs=prs)


def harvest_all(config: dict, *, fetch_diffs: bool = True) -> list[RepoHarvest]:
    """Harvest every enabled repo. Disabled repos are skipped before any API call."""
    owner = config["owner"]
    out: list[RepoHarvest] = []
    for entry in config["repos"]:
        if not entry.get("enabled", False):
            log.info("skipping %s (disabled in config)", entry["name"])
            continue
        out.append(
            harvest_repo(
                entry["name"],
                owner,
                entry.get("base", "dev"),
                entry.get("merge_method", "squash"),
                fetch_diffs=fetch_diffs,
            )
        )
    return out
