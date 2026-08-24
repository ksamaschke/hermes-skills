# Reviewer reliability

A reviewer is only useful if the review actually reached the intended target and returned a bounded, evidence-based verdict.

## Required preflight

Before reading the diff, the reviewer must:

1. print or otherwise verify the absolute target repository/worktree path;
2. verify it is a Git worktree and record the current commit/branch;
3. verify the named files exist;
4. confirm read-only intent;
5. use a bounded model turn/runtime budget;
6. use a minimal reviewer profile/toolset rather than inheriting unrelated interactive skills;
7. use non-interactive terminal settings (`CI=1`, `TERM=dumb`) where the runner supports them.

A profile or wrapper must not rely on the controller's outer `cwd` being propagated. Put the target path in the reviewer prompt and explicitly `cd` or use absolute paths.

Before fan-out, verify the exact provider/model route used by the reviewer profile and the effective per-profile concurrency cap. A tiny successful probe proves route reachability only; a representative worker-sized request or concurrent run is required to establish usable capacity. Treat provider `Overloaded` responses as `REVIEW-INCOMPLETE`, not as a product verdict.

## Verdict rules

- A final report with evidence may produce `APPROVE`, `CHANGES_REQUESTED`, or `BLOCKED`.
- Target-not-found, wrong-cwd, terminal-clear failure, tool timeout, iteration exhaustion, missing credentials, or process crash produces `REVIEW-INCOMPLETE`.
- `REVIEW-INCOMPLETE` is never approval and must not advance a code card.
- A same-family, unknown-family, or partial review is `PRELIMINARY`, not independent sign-off.

## Recovery

When a review is incomplete:

1. inspect the worker/reviewer process and logs;
2. verify no source files were modified;
3. retry once with a smaller explicit scope, fewer tools, a clean profile, or a corrected absolute path;
4. if the second attempt fails, block the card with the exact infrastructure limitation;
5. do not repeat the same failing launch command indefinitely.

If the failure is backend overload, reduce the effective reviewer cap through the supervised dispatcher owner, verify the running gateway loaded the new value, and queue excess review cards. Do not requeue a product task merely to lower the blocked count.

When the reviewer returns findings, the orchestrator verifies the cited files/lines, routes concrete changes to an implementer, and reruns the reviewer after the fix.

## Review handoff

The final handoff must include:

- target path and commit reviewed;
- files inspected;
- tests/checks run and exit status;
- findings with severity and file/line evidence;
- verdict;
- limitations or unverified acceptance criteria.

Worker summaries without this evidence remain untrusted handoffs.
