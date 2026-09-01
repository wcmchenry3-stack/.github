"""Shared data structures for the Dependabot triage agent.

Every module in this package speaks in these types. Nothing here performs I/O or
calls a model; that keeps the objects trivially constructible in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RiskTier(StrEnum):
    """Assessment outcome. Only LOW is ever eligible for an automated merge."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    BLOCKED = "BLOCKED"


class Action(StrEnum):
    """The complete action vocabulary available to the executor.

    The assessment model emits one of these and nothing else. Anything outside
    this set is a schema violation that aborts the run — the model can never
    express "run this shell command" or "write this file".
    """

    COMMENT = "COMMENT"
    REBASE = "REBASE"
    MERGE = "MERGE"
    SKIP = "SKIP"


@dataclass(frozen=True)
class ChangedFile:
    """One file in a pull request, with its unified-diff patch when available."""

    path: str
    additions: int = 0
    deletions: int = 0
    patch: str = ""

    def added_lines(self) -> list[str]:
        """Lines introduced by this PR, without the leading ``+``."""
        return [
            line[1:]
            for line in self.patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]

    def changed_lines(self) -> list[str]:
        """Added *and* removed lines, leading marker retained."""
        return [
            line
            for line in self.patch.splitlines()
            if (line.startswith("+") and not line.startswith("+++"))
            or (line.startswith("-") and not line.startswith("---"))
        ]


@dataclass(frozen=True)
class CheckRun:
    """A single status check on the PR head."""

    name: str
    conclusion: str  # SUCCESS | FAILURE | SKIPPED | NEUTRAL | CANCELLED | ""
    status: str = "COMPLETED"  # QUEUED | IN_PROGRESS | COMPLETED
    # `gh pr checks --json link`'s URL for this check, e.g.
    # https://github.com/OWNER/REPO/actions/runs/123/job/456. Names the exact
    # workflow run this check ran in, which diagnose.fetch_log_tail uses to fetch
    # its log directly rather than guessing at "the most recent run for this
    # branch" — a guess that silently misfires whenever a push triggers more than
    # one workflow. Empty for a synthetic MISSING check or an older harvest.
    details_url: str = ""

    @property
    def is_success(self) -> bool:
        return self.conclusion.upper() in ("SUCCESS", "NEUTRAL", "SKIPPED")

    @property
    def is_settled(self) -> bool:
        return self.status.upper() == "COMPLETED"


@dataclass(frozen=True)
class RepoBaseline:
    """Tamper baseline captured for a repo at the start of the run.

    Re-checked at the end of the run; any drift aborts everything (guard L4).
    """

    repo: str
    required_contexts: frozenset[str]
    workflow_file_shas: dict[str, str] = field(default_factory=dict)
    dependabot_config_sha: str = ""
    thresholds: dict[str, float] = field(default_factory=dict)


@dataclass
class PullRequest:
    """A Dependabot pull request plus everything needed to judge it."""

    repo: str
    number: int
    title: str
    author: str
    base_ref: str
    head_sha: str
    head_ref: str = ""
    files: list[ChangedFile] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    checks: list[CheckRun] = field(default_factory=list)
    mergeable: str = "UNKNOWN"  # MERGEABLE | CONFLICTING | UNKNOWN
    merge_state_status: str = "UNKNOWN"  # CLEAN | UNSTABLE | BEHIND | BLOCKED | DIRTY
    body: str = ""
    ecosystem: str = ""  # npm | pip | github-actions | ...
    is_group: bool = False

    @property
    def slug(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]

    def failing_required(self, required: frozenset[str]) -> list[CheckRun]:
        """Required checks that did not succeed.

        A required context with no matching check run counts as failing — a
        missing check is never treated as a passing one.
        """
        by_name = {c.name: c for c in self.checks}
        out: list[CheckRun] = []
        for name in sorted(required):
            run = by_name.get(name)
            if run is None:
                out.append(CheckRun(name=name, conclusion="MISSING", status="COMPLETED"))
            elif not (run.is_settled and run.is_success):
                out.append(run)
        return out


@dataclass(frozen=True)
class GuardResult:
    """Outcome of one deterministic precondition."""

    guard_id: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - display only
        mark = "pass" if self.passed else "FAIL"
        return f"[{self.guard_id}] {mark}: {self.detail}"


@dataclass
class Assessment:
    """The model's judgment for one PR, before any guard has run."""

    repo: str
    number: int
    tier: RiskTier
    merge_order: int
    rationale: str
    deciding_question: str = ""
    evidence: dict[str, str] = field(default_factory=dict)
    packages: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return f"{self.repo}#{self.number}"


@dataclass
class Decision:
    """Final record for one PR — what happened and why. One ledger line."""

    repo: str
    number: int
    title: str
    tier: RiskTier
    action: Action
    reason: str
    guard_results: list[GuardResult] = field(default_factory=list)
    rebased: bool = False
    merged: bool = False
    deferred: bool = False
    deciding_question: str = ""
    diagnosis: str = ""
    # True when a required check was already failing when the run started. Such a
    # PR was never mergeable tonight by anyone, so counting it against the agent
    # would make the autonomy rate measure repo health rather than agent behaviour.
    pre_existing_red: bool = False
    # The commit this decision was made on. Recorded to history so a future run
    # can recognise "this exact PR, unchanged" and skip re-triaging it — see
    # dedupe.py.
    head_sha: str = ""

    @property
    def slug(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def failed_guard(self) -> str:
        for g in self.guard_results:
            if not g.passed:
                return g.guard_id
        return ""


@dataclass(frozen=True)
class Repeat:
    """An open PR carried over from a previous run because nothing changed.

    Built by dedupe.py from the most recent matching history entry when a PR's
    head SHA is unchanged since it last received a final, non-deferred COMMENT
    verdict. Never re-assessed, never re-commented — just surfaced in the
    report so the previous verdict stays visible without being repeated.
    """

    repo: str
    number: int
    title: str
    tier: RiskTier
    action: Action
    reason: str
    deciding_question: str
    head_sha: str
    last_seen_at: str
    last_run_id: str

    @property
    def slug(self) -> str:
        return f"{self.repo}#{self.number}"
