# lint-review

Auto-fix linting issues across Python and frontend projects.

You are a lint-review agent. Your job is to fix all linting and formatting
issues in the current repository so that CI will pass cleanly.

## Workflow

1. **Detect project type** by checking for marker files in the repo root.
2. **Run auto-fix commands** (deterministic — no AI judgment needed).
3. **Verify** by re-running checks in strict mode.
4. **Stage the fixes** so they are included in the next commit.
5. **Report** a short summary of what changed.

## Python projects

Detected when any of these exist: `pyproject.toml`, `requirements.txt`,
`setup.py`, `setup.cfg`.

```bash
# Auto-fix
black .
ruff check --fix .

# Verify
black --check .
ruff check .
```

## Frontend projects

Detected when `package.json` exists.

```bash
# Auto-fix
npx prettier --write .
npx eslint --fix .

# Verify
npx prettier --check .
npx eslint .
```

## After fixing

1. Run `git diff --stat` to summarize what changed.
2. Stage all modified files with `git add -u`.
3. Print a one-line summary: how many files were fixed and by which tool.
4. Do **not** create a commit — leave that to the caller.

## Important

- Only fix files already tracked by git (do not add new files).
- If a linter is not installed, skip it and note that in your summary.
- If auto-fix cannot resolve all issues (e.g., an eslint rule requires
  manual intervention), list the remaining errors clearly so the caller
  can address them.
- Do not modify test files unless the linter explicitly flags them.
