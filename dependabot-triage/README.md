# Dependabot triage agent

A nightly job that empties the low-risk Dependabot queue across the org, refuses
anything it cannot justify, and sends one email covering every repo.

Runs at 06:00 UTC via [`scheduled-dependabot-triage.yml`](../.github/workflows/scheduled-dependabot-triage.yml).

## What it may and may not do

The agent has exactly three side effects: **comment on a PR**, **ask Dependabot to
rebase**, and **merge**. It never edits a file, never pushes a commit, and never
touches branch protection. Rebases are performed by Dependabot in response to a
comment, which is why the agent's token needs no write access to any target repo's
git tree.

That restriction is the point. The failure mode this design exists to prevent is
an agent that, faced with a red CVE check, decides the check is the problem and
turns it off. It cannot: there is no code path from here to a file edit, and the
token has no `Administration` or `Actions` permission to reach for either.

## Phases

| Phase | Module | Model |
|---|---|---|
| 1. Harvest PRs + tamper baseline | `harvest.py` | none |
| 2. Collective risk assessment | `assess.py` | Sonnet 5, one call for the whole stack |
| 3. Decide, rebase, merge | `execute.py` | none |
| 4. Diagnose failing checks | `diagnose.py` | Haiku 4.5 |
| 5. Email the stack-wide report | `report.py` | Haiku 4.5, one paragraph only |
| 6. Record history | `metrics.py` | none |

The model classifies. Deterministic code decides. Phase 2's output is
schema-validated data, and phase 3 maps it onto a four-value action vocabulary —
so a model response can never be read as a command.

## Guards

`guards.py` holds eleven preconditions, re-run against freshly fetched API state
immediately before every merge. Any failure means comment-and-skip.

| ID | Refuses when |
|---|---|
| G01 | The author is not Dependabot |
| G02 | A changed path is outside the manifest/lockfile/workflow allowlist |
| G03 | A CI-defining file is touched (`pytest.ini`, `codecov.yml`, `.eslintrc*`, …) |
| G04 | A `.github/workflows/` diff changes anything but a `uses:` version pin |
| G05 | An added line matches a check-weakening pattern |
| G06 | A required check is red, pending, or missing — or the repo requires none |
| G07 | A required context disappeared from branch protection mid-run |
| G08 | The head SHA moved since assessment |
| G09 | A numeric quality gate was lowered |
| G10 | GitHub does not consider the PR cleanly mergeable |
| G11 | The `dependabot-triage:hold` label is present |

Beyond the guards: an adversarial second opinion on every `LOW` (a different,
weaker model arguing the merge is unsafe), per-repo and total merge caps, spend
ceilings, and a tamper tripwire that aborts the run if any baseline drifts.

`guards.py` is held at **100% coverage**, verified in CI before the agent is
allowed to run. A broken boundary is worse than no automation.

## Running locally

```bash
pip install -r requirements.txt
python __main__.py --dry-run --verbose            # assess and report, change nothing
python __main__.py --dry-run --repos RulersAI     # one repo
python __main__.py --no-dry-run                   # live
```

`--dry-run` is the default everywhere, including the workflow. Going live is an
explicit `workflow_dispatch` choice.

## Tests

```bash
python -m pytest                                   # 80% floor, enforced
python -m pytest --cov=guards --cov-fail-under=100 # the boundary
```

`gh.py` and `llm.py` are excluded from coverage: they are pure adapters over the
`gh` CLI and the Anthropic SDK, and unit tests over them would assert the
behaviour of a mock. They are exercised by the dry run against live repos.

## Configuration

Everything tunable lives in [`config.yml`](config.yml). Repos are opted in
individually and ship disabled apart from `powerplayshistory_site`, which is the
staged-rollout starting point.

## Secrets

| Secret | Used for |
|---|---|
| `ANTHROPIC_API_KEY` | Model calls. Pay-as-you-go API billing, deliberately separate from any Claude Code subscription — this job cannot consume interactive usage. |
| `RESEND_API_KEY` | The nightly email. One message a day sits far inside Resend's free tier. |
| `DEPENDABOT_TRIAGE_TOKEN` | Cross-repo PR access. Fine-grained, `Pull requests: RW` + `Contents: RW` + `Metadata: R`, and deliberately **no** `Administration` or `Actions`. |

The workflow's own `GITHUB_TOKEN` commits the metrics history and is scoped to
this repository only, so the two credentials stay separate.

## Kill switches

- `dependabot-triage:hold` label on a PR — skips that PR.
- `HOLD` file at the root of this repo — halts the whole run.
- `enabled: false` in `config.yml` — skips a repo before any API call.
