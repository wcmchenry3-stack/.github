"""Thin, auditable wrapper around the ``gh`` CLI.

Every GitHub interaction the agent performs goes through one of these functions,
which keeps the complete list of side effects short enough to read in one sitting:
:func:`comment`, :func:`request_rebase`, and :func:`merge`. There is deliberately
no generic "run this command" escape hatch.

Subprocess calls always pass an argument list — never ``shell=True`` — so nothing
in a PR title or package name can reach a shell.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

log = logging.getLogger(__name__)


class GhError(RuntimeError):
    """A ``gh`` invocation failed."""


class ProtectionUnreadable(GhError):
    """Branch protection exists but this token is not allowed to read it.

    Reading branch protection requires ``Administration: Read``. Without it the
    API answers with a permission error that is easy to mistake for "this branch
    has no protection" — and that mistake is dangerous in both directions: it
    makes a protected repo look unprotected, and it makes a token-permission
    problem look like a repo-configuration problem. The run aborts instead.
    """


# Substrings GitHub uses when a token lacks the permission, as opposed to when
# the resource genuinely does not exist.
_PERMISSION_MARKERS = (
    "resource not accessible",
    "must have admin rights",
    "not accessible by integration",
    "requires authentication",
    "403",
)


def _is_permission_error(exc: GhError) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _PERMISSION_MARKERS)


def _run(args: list[str], *, timeout: int = 120) -> str:
    """Execute ``gh`` with an argument list and return stdout."""
    log.debug("gh %s", " ".join(args))
    try:
        proc = subprocess.run(  # noqa: S603 - argument list, never shell=True
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment issue
        raise GhError("the `gh` CLI is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GhError(f"gh {args[0]} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise GhError(f"gh {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def api(path: str, *, method: str = "GET", fields: dict[str, str] | None = None) -> Any:
    """Call the REST API and parse the JSON response."""
    args = ["api", "--method", method, path]
    for key, value in (fields or {}).items():
        args += ["-f", f"{key}={value}"]
    raw = _run(args)
    return json.loads(raw) if raw.strip() else None


def graphql(query: str, **variables: str) -> Any:
    """Call the GraphQL API. Used where REST would need several round trips."""
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        args += ["-f", f"{key}={value}"]
    return json.loads(_run(args))


def list_open_prs(repo: str, owner: str) -> list[dict[str, Any]]:
    """Open PRs with the fields the triage needs, in one call per repo."""
    raw = _run(
        [
            "pr",
            "list",
            "--repo",
            f"{owner}/{repo}",
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,title,author,baseRefName,headRefOid,labels,files,"
            "mergeable,mergeStateStatus,body,createdAt",
        ]
    )
    return json.loads(raw) if raw.strip() else []


def pr_diff(repo: str, owner: str, number: int) -> str:
    """Unified diff for a PR. Needed because `pr list --json files` omits patches."""
    return _run(["pr", "diff", str(number), "--repo", f"{owner}/{repo}"], timeout=180)


def pr_checks(repo: str, owner: str, number: int) -> list[dict[str, Any]]:
    """Check-run states for the PR head.

    ``gh pr checks`` exits non-zero when any check is failing, which is a normal
    outcome here rather than an error, so the raw result is tolerated.
    """
    try:
        raw = _run(
            [
                "pr",
                "checks",
                str(number),
                "--repo",
                f"{owner}/{repo}",
                "--json",
                "name,state,bucket",
            ]
        )
    except GhError as exc:
        # Non-zero exit with a parseable body means "some check is red".
        text = str(exc)
        start = text.find("[")
        if start == -1:
            log.warning("no check data for %s/%s#%s: %s", owner, repo, number, exc)
            return []
        raw = text[start : text.rfind("]") + 1]
    return json.loads(raw) if raw.strip() else []


def required_contexts(repo: str, owner: str, branch: str) -> frozenset[str]:
    """Required status checks for a branch, from classic protection or rulesets.

    Repos in this org use both mechanisms, and a repo may have neither — in which
    case the empty set is returned and guard G06 refuses to auto-merge there.

    Raises :class:`ProtectionUnreadable` when the token lacks permission to read
    protection. An empty result must only ever mean "nothing is required here",
    never "I was not allowed to look".
    """
    contexts: set[str] = set()
    try:
        protection = api(f"repos/{owner}/{repo}/branches/{branch}/protection")
        checks = (protection or {}).get("required_status_checks") or {}
        contexts.update(checks.get("contexts") or [])
        for entry in checks.get("checks") or []:
            if entry.get("context"):
                contexts.add(entry["context"])
    except GhError as exc:
        if _is_permission_error(exc):
            raise ProtectionUnreadable(
                f"cannot read branch protection for {owner}/{repo}@{branch}. "
                "The token needs 'Administration: Read'. Read-only is sufficient — "
                "it does not allow changing protection."
            ) from exc
        log.info("%s/%s has no classic branch protection on %s", owner, repo, branch)

    try:
        for ruleset in api(f"repos/{owner}/{repo}/rulesets") or []:
            detail = api(f"repos/{owner}/{repo}/rulesets/{ruleset['id']}")
            include = ((detail.get("conditions") or {}).get("ref_name") or {}).get("include", [])
            if f"refs/heads/{branch}" not in include and "~ALL" not in include:
                continue
            for rule in detail.get("rules") or []:
                if rule.get("type") != "required_status_checks":
                    continue
                params = rule.get("parameters") or {}
                for entry in params.get("required_status_checks") or []:
                    if entry.get("context"):
                        contexts.add(entry["context"])
    except GhError as exc:
        if _is_permission_error(exc):
            raise ProtectionUnreadable(
                f"cannot read rulesets for {owner}/{repo}. "
                "The token needs 'Administration: Read'."
            ) from exc
        log.info("%s/%s exposes no rulesets", owner, repo)

    return frozenset(contexts)


def file_shas(repo: str, owner: str, directory: str, ref: str) -> dict[str, str]:
    """Blob SHA per file in a directory — the tamper baseline for workflows."""
    try:
        entries = api(f"repos/{owner}/{repo}/contents/{directory}?ref={ref}") or []
    except GhError:
        return {}
    return {e["path"]: e["sha"] for e in entries if e.get("type") == "file"}


def file_text(repo: str, owner: str, path: str, ref: str) -> str:
    """Decoded file contents, or an empty string when the file is absent."""
    import base64

    try:
        entry = api(f"repos/{owner}/{repo}/contents/{path}?ref={ref}")
    except GhError:
        return ""
    if not entry or entry.get("encoding") != "base64":
        return ""
    return base64.b64decode(entry["content"]).decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# The three mutating operations. Nothing else in this package writes to GitHub.
# --------------------------------------------------------------------------


def comment(repo: str, owner: str, number: int, body: str, *, dry_run: bool = False) -> None:
    """Post a PR comment."""
    if dry_run:
        log.info("[dry-run] would comment on %s/%s#%s", owner, repo, number)
        return
    _run(["pr", "comment", str(number), "--repo", f"{owner}/{repo}", "--body", body])


def request_rebase(
    repo: str, owner: str, number: int, *, recreate: bool = False, dry_run: bool = False
) -> None:
    """Ask Dependabot to rebase or recreate the branch.

    The agent never rebases a branch itself; it asks Dependabot to, and Dependabot
    force-pushes under its own identity. That is why the agent's token needs no
    ability to write to a target repo's git tree at all.
    """
    body = "@dependabot recreate" if recreate else "@dependabot rebase"
    if dry_run:
        log.info("[dry-run] would post %r on %s/%s#%s", body, owner, repo, number)
        return
    comment(repo, owner, number, body)


def merge(
    repo: str, owner: str, number: int, method: str = "squash", *, dry_run: bool = False
) -> None:
    """Merge the PR.

    ``--admin`` is never passed: administrator bypass of branch protection is
    exactly the shortcut this agent must not be able to take.
    """
    if method not in ("squash", "merge", "rebase"):
        raise ValueError(f"unsupported merge method {method!r}")
    if dry_run:
        log.info("[dry-run] would %s-merge %s/%s#%s", method, owner, repo, number)
        return
    _run(
        [
            "pr",
            "merge",
            str(number),
            "--repo",
            f"{owner}/{repo}",
            f"--{method}",
            "--delete-branch",
        ],
        timeout=180,
    )
