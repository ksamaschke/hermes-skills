# Review integrity incident patterns

This reference records a reusable failure class from a real Kanban factory stall.
It is not a current task list and its example identifiers must not be reused.

## Failure pattern matrix

- **Same-card review:** an implementation worker moves its own card to `review`,
  and the dispatcher starts a reviewer run on that implementation card. This is
  not a fresh independent review. Reclaim/reclassify the implementation card
  and create a separate reviewer card.
- **Prose-only contract:** the task body says `max_retries=1` or
  `read_only_source=true`, but the durable board row has a null retry/runtime
  field. The board row wins; quarantine the result and recreate the card.
- **Title-only reuse:** a later implementation round reuses the previous review
  leaf because the title matches. Scope idempotency by implementation task and
  review round, and require the source marker to match before reuse.
- **Review-to-implementation misrouting:** generic timeout recovery sees a
  review timeout as an implementation failure and creates an implementer
  continuation. Review timeout recovery must stay on the reviewer path.
- **Historical projection noise:** completed successors coexist with blocked
  timeout predecessors. A progress projection may retain the historical
  `REVIEW-INCOMPLETE` evidence, but must report the active successor and not
  count the predecessor as current implementation work.
- **Fan-in race:** a fan-in card is created before all leaf parents exist or its
  links are repaired after dispatch. Create every parent link in the fan-in
  create call and read the graph back before dispatch.

## Safe repair sequence

1. Read current board stats, task details, runs, events, parents, children, and
   diagnostics. Treat digests and worker prose as hypotheses.
2. Classify the card as implementation, reviewer leaf, fan-in, historical
   timeout, real product finding, or genuine external/human blocker.
3. For a review packet, validate literal fields and the durable row before any
   result can feed fan-in: exact implementation task, exact file paths, one
   lens/question, read-only boundary, the declared two-tier review budget, one retry, profiles,
   stop condition, verdict set, and correct parent links.
4. Preserve timed-out runs. Create a strict-subset successor with a new
   idempotency key; never retry the unchanged packet.
5. Route real findings to a new implementation task. Do not edit a review card
   into implementation work.
6. Reconcile projections so active implementation/review state is distinct from
   retained historical evidence.
7. Read back every board mutation and require a reviewer terminal verdict plus
   fan-in coverage before calling the issue verified or closed.

## Evidence language

Use `queued`, `dependency-gated`, `running`, `REVIEW-INCOMPLETE`,
`CHANGES_REQUESTED`, `APPROVED`, `fixed`, `verified`, and `closed` precisely.
A reviewer PID, green focused test, implementation handoff, or HEX inspection
is not an independent approval. A Forgejo release-blocker label remains open
until the closure bridge verifies all projections and the latest independent
verdict.
