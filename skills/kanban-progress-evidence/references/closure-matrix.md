# Kanban Closure Matrix

Use this template before writing a progress or release-readiness report. Keep one entry per finding or acceptance gap, even when several entries share a remediation task.

## Inventory

- ID / concern:
- Source:
- Severity or release impact:
- Exact evidence target:
- Durable record: task, issue, comment, artifact, or file
- Current state: mentioned / recorded / queued / dependency-gated / blocked / fixed / verified / closed
- Parent or dependency:
- Owner:
- Acceptance evidence still required:
- Residual-risk decision or reference:

## Reconciliation checks

1. Compare the inventory count with the enumerated entries in the source review or digest.
2. Resolve every task ID against live Kanban JSON or task details.
3. Read back comments, status changes, and created tasks after writes.
4. Check diagnostics and run outcomes for blocked or timed-out cards.
5. Confirm every “verified” entry has an independent command, test, log, screenshot, or read-back artifact.
6. Confirm every “closed” entry satisfies its parent acceptance criteria, not merely a child summary.
7. List any concern that remains only in prose or a worker handoff before finalizing.

## Timeout record

- Command or worker action:
- Time limit:
- Result: failure / timeout with no result / process still running
- Focused checks that passed:
- Replacement owner/task:
- Closure condition:
