A Dependabot pull request has a failing CI check. Explain why, in a comment that will be posted on the PR.

The single most useful thing you can determine is whether **this bump caused the failure, or whether the check was already failing before it**. A pre-existing failure means the PR is fine and the repo has an unrelated problem; a caused failure means the bump needs work. Getting this backwards sends someone down the wrong path, so say which one it is and what evidence told you.

Write three short paragraphs at most:

1. What failed, concretely — the job, and the actual error, not a restatement of "the build failed".
2. Whether the bump caused it. If the error has no connection to the packages this PR moves, say so plainly.
3. What would fix it, and whether that fix would be confined to dependency manifests or would need a source change. This matters because the agent may only rebase; anything requiring a source edit is a human's job.

If the logs do not contain enough to tell, say that rather than guessing. A confident wrong diagnosis is worse than an admission that the logs were unhelpful.

Do not suggest disabling, skipping, or relaxing the failing check. If the check is genuinely wrong, say that the check looks wrong and why — but the fix is never to turn it off.

End your response with a line reading exactly `CONFIDENCE: HIGH`, `CONFIDENCE: MEDIUM`, or `CONFIDENCE: LOW`.
