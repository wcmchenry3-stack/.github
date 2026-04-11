# plan-issues

Break down a problem statement into well-scoped GitHub issues. No code
is written. The output is GitHub issues created in the target repository.

## When to invoke

The user describes a feature, bug, initiative, or area of work. Your job
is to investigate, structure, and file — not implement.

---

## Phase 1 — Investigate before writing

Before drafting any issue, do the following:

1. **Clarify scope** — if the request is ambiguous, ask:
   - Is this a bug, feature, refactor, or spike?
   - Which repo does this belong to? (check `gh repo view` if unclear)
   - Is there a deadline or external dependency?
   - Are there existing issues covering any part of this? (`gh issue list --search "..."`)

2. **Read the code** — find the relevant files. Read them. Don't file issues
   about code you haven't looked at. Reference actual file paths and line numbers.

3. **Check existing issues** — search for duplicates or related work before
   creating anything. If a related issue exists, link to it; don't duplicate.

4. **Determine scope** — decide: is this one issue or an epic with children?
   - **Single issue**: one story that one PR can close
   - **Epic**: multiple interdependent stories; create the epic first, then children
   - Rule of thumb: if it would take more than ~3 days to implement end-to-end, it's an epic

---

## Phase 2 — Draft and confirm

Before creating any issues, output your proposed breakdown as a numbered
list with titles and one-line summaries. Wait for the user to confirm or
adjust before calling `gh issue create`.

---

## Phase 3 — Create issues

### Epic issue template

Use when the work spans multiple stories. Create this first, get its number,
then reference it in every child story.

```
## In plain English
[2–3 sentences. What problem does this initiative solve? What will be
noticeably different for a user or developer when it's done?]

## Problem statement
[Current state — what's broken, missing, or painful.]
[Desired state — what done looks like.]

## Child stories
<!-- Filled in as stories are created -->
- [ ] #N — story title
- [ ] #N — story title

## Out of scope
[Explicit exclusions. What will NOT be addressed in this epic.]

## Success metrics
[How we know the epic is complete. Measurable where possible.]

## Dependencies
- Blocked by: [external thing, other issue, or "none"]
```

---

### Feature story template

```
## In plain English
[1–2 sentences a non-engineer could read. What's missing or broken today?
What will work after this ships?]

## User story
As a [role], I want [action] so that [benefit].

## Context
[Technical background. Why does this exist? What related issues, PRs, or
decisions led here? Link them.]

## Acceptance criteria
- [ ] [Specific, testable condition]
- [ ] [Specific, testable condition]

## Technical notes
[File paths, API contracts, component names, data shapes. Be specific.
Reference actual code locations from your investigation.]

## Test coverage
<!-- Check all that apply. Leave unchecked ones with a reason if skipping. -->
### Unit tests
- [ ] [What to test and where — e.g. `src/services/__tests__/foo.test.ts`]
### E2E tests
- [ ] [User flow to cover in Playwright/Detox — or "Not required: no user-facing interaction"]
### Performance
- [ ] [Threshold to assert — or "Not required: no hot path changes"]
### Regression
- [ ] [Scenario that must not break — or "Not required: no changes to existing behaviour"]

## Documentation to update
- [ ] [Doc file or section] — [what changes]
- (or "None")

## Unknowns / needs investigation
- [Anything that must be answered before implementation can start]
- (or "None")

## Out of scope
- [Explicit exclusions — prevents scope creep]

## Dependencies
- Part of: #[epic number] (if applicable)
- Blocked by: #[issue] or "none"
- Blocks: #[issue] or "none"

## Definition of done
- [ ] All acceptance criteria met
- [ ] Test coverage added per above
- [ ] Documentation updated
- [ ] CI green (lint, type check, tests)
- [ ] PR reviewed and merged to dev
```

---

### Bug report template

```
## In plain English
[1–2 sentences. What is the user experiencing? What should happen instead?]

## Reproduction steps
1. [Step]
2. [Step]
3. Expected: [what should happen]
4. Actual: [what happens]

## Supporting evidence
[Paste logs, error messages, stack traces, or screenshots. If none, write "None observed."]

## Root cause (if known)
[File, line, and explanation. If unknown, write "Unknown — needs investigation."]

## Proposed fix
[Specific change: file path, line number, what to change. Or "TBD" if investigation is needed first.]

## Impact
- Who is affected: [all users / specific flow / specific environment]
- Severity: [blocking / degraded / cosmetic]

## Test coverage
### Regression test
- [ ] Test that reproduces the bug scenario so it can never silently regress

## Dependencies
- Part of: #[epic] (if applicable)
- Blocked by: "none" (bugs are usually not blocked)

## Definition of done
- [ ] Bug no longer reproducible via reproduction steps
- [ ] Regression test added and passing
- [ ] CI green
- [ ] Root cause documented in PR description
```

---

### Test story template

Use when test coverage is its own deliverable (not bundled with a feature).

```
## In plain English
[What behaviour is currently untested? Why does it matter to cover it?]

## Context
[What shipped without tests? Reference the PR/issue that created the gap.]

## Missing coverage

### Unit tests (`path/to/__tests__/file.test.ts`)
- [ ] [Scenario: input → expected output]

### E2E tests (`e2e/tests/file.spec.ts`)
- [ ] [User flow]

### Performance assertions
- [ ] [Threshold] — or "Not applicable"

### Regression guards
- [ ] [Scenario that must not break]

## Acceptance criteria
- [ ] All listed test cases pass in CI
- [ ] No existing tests broken

## Dependencies
- Part of: #[epic] (if applicable)
- Blocked by: #[issue that must ship first] or "none"

## Definition of done
- [ ] All listed tests written and green
- [ ] CI passes
```

---

### Documentation story template

```
## In plain English
[What is currently undocumented or wrong? Who is confused by the gap?]

## What to document

### Location
[File path or external system — e.g. `docs/operational-settings.md`, Confluence, README]

### Content required
- [Section heading] — [what to explain]
- [Section heading] — [what to explain]

## Context
[What changed that requires documentation? Link the PR or issue.]

## Acceptance criteria
- [ ] Docs updated at the specified location
- [ ] Accurate as of the linked code change
- [ ] Reviewed by someone outside the implementing team

## Definition of done
- [ ] Documentation merged
- [ ] Link to docs included in PR description
```

---

## Labelling

Apply labels when creating issues. Use `gh label list` to see what's available.
Common patterns across the org:

| Condition            | Labels                                               |
| -------------------- | ---------------------------------------------------- |
| Epic                 | `epic`                                               |
| Bug                  | `bug`                                                |
| Feature              | `enhancement`                                        |
| Test coverage        | `testing`                                            |
| Docs                 | `documentation`                                      |
| Applies to all repos | `repo:all`                                           |
| Repo-specific        | `repo:gaming_app` etc.                               |
| Priority             | `priority:high` / `priority:medium` / `priority:low` |
| Blocked              | `blocked`                                            |

Create missing labels with `gh label create` before filing issues.

---

## Epic / child linking conventions

- Child stories reference the epic: "Part of #N" in the first line of the body
- Epic's child list is updated after each child is created
- Use `gh issue edit #EPIC --body "..."` to append to the epic's child list
- Closing a child: "Closes part of #N" in the PR description (not "Closes #N" — that would close the epic)

---

## What NOT to do

- Do not write code or implementation detail beyond what's needed for the issue to be unambiguous
- Do not create issues for work that's already covered by an open issue — link instead
- Do not create an epic for a single story
- Do not skip the investigation phase — issues filed without reading the code are wrong
- Do not mark test types as "not required" without a written reason
