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

## Review slice budget

An adversarial code review is a focused slice, not a full-repository forensic
mission. A single review card must use a `max_runtime_seconds` cap of **600
seconds** and an explicit stop condition. Do not create a 1,200-second
"inspect everything, run all baselines, and prove E2E" card.

Split broad reviews into independent cards before dispatch. Typical slices are:

- transport/bootstrap/token security;
- frontend transport parity and UI call sites;
- packaging, sidecar lifecycle, and regression tests.

Each slice names its exact files, questions, focused checks, non-goals, and
verdict format. Require a heartbeat at the end of each major phase. At roughly
70% of the budget, the reviewer stops discovery and returns the evidence it has
instead of starting another test or repository scan.

The 600-second cap is a safety boundary, not an expected duration. A correctly
scoped slice should finish well before it. If it does not, the review is
incomplete and must be narrowed again; increasing the timeout is not the
default recovery.

## Verdict rules

- A final report with evidence may produce `APPROVE`, `CHANGES_REQUESTED`, or `BLOCKED`.
- Target-not-found, wrong-cwd, terminal-clear failure, tool timeout, iteration exhaustion, missing credentials, or process crash produces `REVIEW-INCOMPLETE`.
- `REVIEW-INCOMPLETE` is never approval and must not advance a code card.
- A same-family, unknown-family, or partial review is `PRELIMINARY`, not independent sign-off.

## Recovery

When a review is incomplete:

1. inspect the worker/reviewer process and logs;
2. verify no source files were modified;
3. preserve the timeout as `REVIEW-INCOMPLETE`, never as a finding or approval;
4. do not retry the same broad prompt;
5. create a new, narrower review slice with a 600-second cap and link it to the incomplete card;
6. if a focused slice still times out, split that slice again and report the exact missing evidence;
7. do not leave the implementation card blocked merely because an internal review worker needed decomposition.

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
