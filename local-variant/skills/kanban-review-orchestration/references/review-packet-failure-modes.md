# Review packet failure modes

Use this reference when a Kanban review lane appears active but no release
progress occurs.

## Proven failure chain

A fresh review handoff can be semantically correct in prose and still be
operationally invalid. Check the durable task row, not only the body:

1. The implementation worker finishes and creates a fresh reviewer card.
2. The card has a runtime cap but a missing/null `max_retries` field.
3. The broad reviewer prompt times out twice and trips the dispatcher breaker.
4. Successor recovery reports a successful/no-op run because its recognizer only
   understands its own machine-shaped packet format.
5. A progress renderer counts the blocked review leaves as blocked implementation
   work, so repeated cron reports show no useful state transition.

A cron `ok` status proves only that the script exited successfully. It does not
prove that a successor, fan-in, or rework task was created.

## Required recovery sequence

- Read the failed leaf, all runs/events, and its parent/child links.
- Preserve the failed leaf as `REVIEW-INCOMPLETE`; never retry the same prompt.
- Accept both canonical fields and implementer prose labels when parsing a
  handoff, but emit successors in one canonical packet format.
- Create strict-subset reviewer successors with board-enforced
  `max_runtime_seconds` equal to the declared dispatch hard cap and
  `max_retries=1`, plus the evidence budget and per-command timeout in the body.
- Preflight the assigned reviewer profile before creation.
- Create the bounded fan-in with every successor parent in the original create
  call, then read back the exact dependency set.
- Route `CHANGES_REQUESTED` from the latest fan-in even when the implementation
  predecessor is already `done`; do not require the implementation card itself
  to be `blocked`.
- Generate the current projection from the latest implementation generation.
  Superseded timeout leaves remain history and must not block a later approved
  fan-in; unresolved current-generation review gaps still block closure.
- Run the closure bridge in a read-only mode first and verify it imports the
  tracked helper module rather than executing a same-named runtime entrypoint.
  Apply a closure only after the bridge identifies the issue as eligible, then
  read the source tracker back.

## Orchestrator boundary

HEX owns packet construction, dependency wiring, profile preflight, recovery,
reporting, and lifecycle verification. HEX is not the adversarial reviewer.
Reviewer leaves alone emit `APPROVED`, `CHANGES_REQUESTED`, or
`REVIEW-INCOMPLETE`. A heartbeat, PID, green focused test, or worker summary is
not a review verdict.

## Verification checklist

- The failed run and timeout remain visible in history.
- Every replacement leaf has explicit scope, one lens, read-only boundary,
  reviewer/implementer vendor-family comparison, the declared two-tier budget, one retry,
  and a terminal-verdict contract.
- Replacement fan-in parents match the replacement leaf set exactly.
- Rework has a fresh implementer card with the finding as its parent.
- The progress snapshot distinguishes implementation, active review,
  dependency-gated fan-in, and retained historical incomplete leaves.
- Closure eligibility and source-tracker state are read back independently.
