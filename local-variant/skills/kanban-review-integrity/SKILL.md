---
name: kanban-review-integrity
description: "Use for Kanban review integrity and honest closure."
version: 0.1.0
author: HEX
license: MIT
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [kanban, review, integrity, orchestration, evidence, recovery]
    related_skills:
      - kanban-review-orchestration
      - kanban-reviewer-contract
      - kanban-factory-operations
      - kanban-progress-evidence
---

# Kanban Review Integrity

Use this umbrella when an implementation handoff must pass an independent
Kanban review, when review work has timed out or been misrouted, or when a
release snapshot may be counting historical review state as current work.
This skill is the integrity layer around the project's review and factory
skills. It is not a replacement for the configured reviewer profile or the
project's acceptance criteria.

## Core role boundary

HEX/the orchestrator routes, decomposes, and reconciles lifecycle evidence. HEX
must not perform the adversarial review, replay the candidate to supply an
independent finding, or turn its own inspection into a reviewer verdict.

Only the configured reviewer profile may emit `APPROVED`,
`CHANGES_REQUESTED`, or `REVIEW-INCOMPLETE`. A completion verifier may check
that the reviewer reached the intended worktree, stayed within scope, kept the
source read-only, and emitted one terminal outcome. Those are lifecycle facts,
not substitute review evidence.

If an implementation worker moves its own card to the review lane, that is not
a fresh independent review. Reclaim or reclassify the implementation card and
create a separate reviewer card. Preserve the run as history and keep its
result out of fan-in and release closure.

## Required graph

Build and read back this graph:

```text
implementation handoff
        |
        v
fresh exact-scope reviewer leaf(s)
        |
        v
bounded fan-in / coverage reconciliation
```

Create true parent links in the original create call. Do not create apparently
ready children and repair dependencies after dispatch has had a chance to race
the mutation. Fan-in depends on every required leaf and runs only after every
leaf is terminal.

## Change-scoped review kinds

Adversarial review reviews a **change set**, never a code base. There are exactly
two kinds, and a packet that is neither is invalid:

- `pre_commit` — the working-tree change set an implementer proposes to commit,
  scoped to the delta against the named base commit;
- `pre_merge` — the change set a pull/merge request would introduce, scoped to
  the merge-base delta against the target branch.

Scope is the changed hunks, declared as a **change manifest** (base reference,
candidate reference, changed paths with hunk ranges). A whole file is not a
change set: naming file paths alone lets the review drift into surrounding code
and consumes the budget. Reading outside the diff is allowed only as context for
a named changed hunk; a defect visible only in unchanged code is a `gaps` note
for the orchestrator, not a finding against this change set.

Neither kind may grow into a repository-wide audit. Whole-codebase judgement is a
separate orchestrator-filed task with its own card and budget, never an
escalation of a commit or merge review.

## Packet gate

Before dispatch, require all of these literal packet fields:

- `implementation_task` and source issue/PR;
- `review_kind`: `pre_commit` or `pre_merge`;
- target repository, absolute worktree, branch, and candidate revision;
- implementer and reviewer profiles, with vendor-family independence;
- a change manifest: base reference, candidate reference, and changed paths with
  hunk ranges. Never a whole file, directory, glob, module, or topic label;
- one review lens and one bounded acceptance question;
- original acceptance criteria, diff-targeted commands, non-goals, and
  live-system boundary;
- cited implementer/CI gate evidence: command, exit code, run reference, and the
  commit it ran against. The reviewer never re-runs the full project gate;
- `read_only_source: true`;
- the project-declared two-tier runtime budget for each adversarial leaf (see
  below). Runtime values come from explicit project/backend policy and are never
  invented by a skill or agent;
- `max_retries: 1` for each adversarial leaf;
- explicit stop condition, evidence format, and allowed verdicts:
  `APPROVED`, `CHANGES_REQUESTED`, and `REVIEW-INCOMPLETE`.

## Two-tier runtime budget

A single wall clock covering model latency, provider backoff, and command
execution produces kills instead of verdicts. Reviews therefore carry two
distinct limits plus a per-command cap. Reference values for this factory:

- `dispatch_hard_cap_seconds: 1800` — the dispatcher's `--max-runtime`
  SIGTERM/SIGKILL backstop against a hung worker. Not a target, not a budget.
- `evidence_budget_seconds: 900` — the reviewer's own mandatory return point. It
  tracks elapsed time and emits a verdict at or before this point with whatever
  evidence it holds, listing the rest under `gaps`.
- `command_timeout_seconds: 120` — hard cap on any single command.

Checkpoints inside the evidence budget: at 50% stop opening new context, at 70%
stop starting new commands, at 100% emit the verdict.

Returning a verdict at the evidence budget is correct behavior. Being killed at
the dispatch hard cap is an anomaly — a broken reviewer, a broken route, or an
invalid packet — and is always `REVIEW-INCOMPLETE`.

Reviewers run **diff-targeted checks only**. The full project gate (`make test`,
`make validate`, a full suite or build) is the implementer's and CI's evidence,
cited in the packet and never re-run inside a review. Missing or stale gate
evidence is a `CHANGES_REQUESTED` finding, not a licence to run the gate.

Reject packets containing implementation instructions such as “TDD first”,
“write the fix”, or “make the production change”. A reviewer may write scratch
artifacts only outside the candidate worktree. A prose claim does not override
a missing or contradictory durable board field.

## Durable board gate

Read back the task row after creation and before dispatch. Require:

- the configured reviewer profile is the assignee;
- the board row has the project-declared dispatch hard cap as `max_runtime_seconds` and `max_retries=1`; runtime values come from explicit project/backend policy, never invented by a skill;
- the candidate path and exact scope match the handoff;
- parent and fan-in links are correct;
- the task is not an implementation card in `review`;
- the idempotency key includes the implementation task and review round, not only a reusable title.

Run a profile preflight before dispatch:

```bash
hermes -p <reviewer-profile> skills list
```

A missing profile/skill/tool, wrong target, invalid packet, or source mutation is
`REVIEW-INCOMPLETE`, never approval.

## Timeout and rework handling

A review timeout is incomplete evidence, not a finding, approval, or human
blocker. Preserve the run and its evidence.

**Classify the cause before choosing a remedy.** Narrowing scope is the wrong
reflex for most review kills:

- **Provider backoff / `429` cooldown** — the route failed, not the scope. Fix or
  re-probe the route and re-dispatch the same slice. Provider backoff counts
  against the evidence budget; a review consumed by it is `REVIEW-INCOMPLETE`
  naming the provider condition.
- **Forbidden full-gate command in the packet** — the execution boundary was
  violated. Remove the gate command, cite the implementer/CI evidence instead,
  and re-dispatch the same slice.
- **Invalid packet** (non-diff scope, missing budget fields, multiple questions)
  — fix the packet and re-dispatch the same slice.
- **Genuine change-set size** — only now create a strict-subset continuation
  whose change manifest is a subset of the previous one, with a new idempotency
  key and correct parent links.

Raising the runtime budget is never a recovery step. Never retry the unchanged
packet, route a timed-out review to an implementer, or let a review timeout
consume an implementation retry.

A continuation chain that keeps timing out at shrinking scope — for example a
depth-3 continuation reviewing one file and one question that is still killed —
is proof of a budget or execution-boundary defect, not of a change set needing
more splitting. Escalate it as a factory fault instead of creating another
continuation.

If a reviewer finds a real defect, create a new focused implementer task from
the finding. Do not mutate the review card into implementation work. If an
implementation task times out, preserve its worktree and create a genuinely
narrower implementation continuation with the failure evidence and remaining
criteria; do not merely copy the full original prompt under a new title.

## Snapshot and release projection

A release snapshot is a projection, not a source of truth. It must read the
live board and distinguish:

- active implementation rework (`running`, `ready`, or genuinely blocked implementation cards);
- active review work (`running`, `ready`, or `review` reviewer cards);
- fan-in waiting on named review parents (`todo`);
- historical timeout/review-incomplete leaves retained for audit.

Do not report historical blocked review leaves as current implementation work
when a successor or current rework card owns the next action. Keep the
`REVIEW-INCOMPLETE` evidence and latest `CHANGES_REQUESTED` verdict visible,
but render the actionable rework owner and its current status. Do not close a
source release blocker merely because a local task or one review leaf is done.

## Orchestrator verification checklist

Before reporting progress:

1. Read current board JSON/stats, task details, runs, events, dependencies, and diagnostics.
2. Reconcile every requested issue against its current implementation owner and review graph.
3. Validate packet prose and durable row fields for every fresh review card.
4. Verify worker PID/heartbeat/terminal transition without inspecting the candidate as a reviewer.
5. Treat reviewer verdicts exactly as emitted; never infer approval from green tests, a PID, or a handoff summary.
6. Keep archived history, incomplete reviews, real findings, and current work separate in the report.
7. Say `fixed`, `verified`, or `closed` only when the corresponding evidence and board state agree.

Use direct report sections: **Live state**, **Review graph**, **Reviewer verdicts**,
**Rework routing**, **Historical evidence**, **Genuine blockers**, and
**Decision**. If no genuine human decision or external authorization is needed,
say `No human action required` rather than asking the operator to restart,
reclaim, or repair routine factory state.

## References

See `references/incident-lessons.md` for the reusable failure-pattern matrix
and the compact recovery/readback sequence derived from a real factory stall.
See `references/reviewer-timeout-policy.md` for the durable lesson on gated
review scope and the approved two-tier budget.
See `references/diagnosing-review-kills.md` for the log-forensics method that
separates a scope problem from a budget or execution-boundary problem before
any continuation is created — including the worker log path, the command-time
arithmetic, and the cause/remedy decision table.
The related project skills remain authoritative for project-specific tracker,
model, deployment, and acceptance policy.

## Pitfalls

- Loading a review skill and then performing the review in HEX anyway;
- reassigning an implementation card to a reviewer instead of creating a fresh card;
- declaring scope as file paths, a module, or a directory instead of a change
  manifest — a whole file is not a change set;
- letting a reviewer run the full project gate: it is the single largest consumer
  of the budget and the evidence already exists, so cite it;
- collapsing model latency, provider backoff, and command execution into one wall
  clock, so the worker is killed instead of returning a verdict;
- treating a dispatch-cap kill as the reviewer's normal stop condition rather
  than as a factory fault to diagnose;
- narrowing scope in response to a timeout actually caused by provider backoff or
  a forbidden gate command;
- enforcing a runtime cap in code (a validator that rejects any other value) so
  the policy cannot be corrected by configuration alone;
- accepting a body-level retry cap when the board row has `max_retries=NULL`;
- reusing review leaves by title across implementation rounds;
- treating a timed-out reviewer as a product finding or approval;
- sending a timed-out review through implementation continuation recovery;
- creating fan-in before all leaf dependencies exist or repairing links after dispatch;
- reporting historical timeout cards as current blockers without naming the active successor;
- closing a Forgejo issue from a local completion or a single leaf verdict.

## Completion criteria

This skill's procedure is satisfied only when every requested review scope has a
fresh contract-complete reviewer card or an explicit preserved gap, every leaf
has a terminal reviewer outcome or honest `REVIEW-INCOMPLETE`, fan-in coverage
is reconciled, implementation rework is separately routed, and no HEX-authored
inspection is counted as independent review evidence.
