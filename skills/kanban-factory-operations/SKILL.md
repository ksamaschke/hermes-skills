---
name: kanban-factory-operations
description: "Use when Kanban work stalls; recover dispatch and review."
version: 0.1.0
author: Karsten Samaschke, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [kanban, software-factory, orchestration, dispatch, review, evidence]
    related_skills: [kanban-implementation-workflow, kanban-progress-evidence, software-factory-recovery]
---

# Kanban Factory Operations

Operate a durable Kanban board as a software factory: tasks are decomposed, implemented, independently reviewed, verified, and released through evidence-backed state transitions. This skill owns live factory operations and recovery. It does not replace project acceptance criteria, deployment policy, or independent code review.

## When to Use

Use when:

- a Kanban digest reports no progress for hours;
- the board has todo/review work but zero or unexpectedly few running workers;
- review handoffs accumulate without independent verdicts;
- workers time out, crash, exit without a terminal Kanban call, or are reported done without evidence;
- the dispatcher, gateway, model route, or worker capacity is suspected;
- a user asks whether the software factory is actually operating.

Do not use a digest alone as the source of truth. Do not use this skill to bypass acceptance gates, silence diagnostics, weaken tests, or mark work done because a worker produced a plausible summary.

## Operating Invariants

1. **The live board is authoritative.** A digest is a historical observation. Current JSON, task details, runs, events, diagnostics, and process state decide what is happening now.
2. **The factory is more than a queue.** A task is not handled until its implementation, independent review, verification evidence, and board transition are all accounted for.
3. **One dispatcher owner.** If dispatch is embedded in a supervised gateway, that gateway is the owner. Never launch a second long-lived gateway or standalone dispatcher against the same board database.
4. **Review is a first-class lane.** A review handoff is not completion. A review task assigned to a real reviewer can be automatically dispatched only when the review-dispatch gate and reviewer profile are enabled.
5. **Infrastructure recovery is narrow.** Requeue a card after fixing a dispatcher/backend problem only when its failure is attributable to that problem. Preserve genuine product timeouts, missing evidence, dependency gates, and human decisions.
6. **Worker claims are untrusted.** Verify from task runs, task events, PIDs, heartbeats, diffs, tests, and the exact board state.
7. **Capacity is part of correctness.** A backend that accepts one probe but overloads under fan-out is not a healthy factory route. Bound per-profile concurrency to observed capacity.

## Add-on recovery layer

Keep factory recovery outside Hermes core. The repository's
`scripts/kanban_factory_recovery.py` is installed as a silent `no_agent` cron
job and repairs only:

- legacy LLM cron jobs whose creation-time snapshots are not durable
  `provider`/`model` fields, using `hermes cron edit`;
- blocked tasks whose latest spawn failure is a duplicate clean managed Git
  worktree, preserving the branch, removing only the clean worktree, unblocking
  the task, and reading the status back.

Dirty or non-managed worktrees, product failures, provider authorization,
review findings, and deployment decisions remain explicit blockers. Do not
modify Hermes source for these repairs.

## Review budget protocol

Adversarial code reviews are dispatched as focused read-only slices, not as
one broad repository scan. Each slice uses `max_runtime_seconds=600`, names
the exact files and questions, runs only focused checks, heartbeats at phase
boundaries, and stops discovery at roughly 70% of its budget.

Create each leaf with `max_retries=1` and preflight the assigned reviewer with
`hermes -p <reviewer-profile> skills list`. A timed-out leaf must not be
automatically respawned with the same prompt; preserve it as
`REVIEW-INCOMPLETE`, then create a narrower continuation after confirming the
profile can resolve the required skills.

If a review times out, classify it as `REVIEW-INCOMPLETE`. Preserve the run and
its evidence, do not retry the same prompt, and create a narrower continuation
slice. A review timeout is an internal orchestration problem, not a product
blocker and not approval. The implementation card remains gated only on a
completed review verdict, not on the failed worker attempt.

Chunking is hierarchical: split by acceptance question/control-flow path, then
split again whenever a chunk crosses two runtime layers, contains more than
five primary production files, or asks for multiple independent verdicts. Leaf
chunks run independently; a bounded fan-in task reconciles their reports and
acceptance coverage without rescanning the repository. Findings rerun only the
affected leaf after a fix.

Native UI evidence is a separate lane. A skill reference does not provision
`computer_use` to a reviewer worker. Preflight the actual worker schema; if the
tool is absent, HEX/the orchestrator performs the approved capture/input and
attaches the screenshot, process/build provenance, fixture hash, and protocol
checks. The reviewer then validates those artifacts read-only. TCC permission
dialogs remain a human-only boundary and are never delegated to a worker.

## Prerequisites

Resolve before mutating the board:

- board slug and repository identity;
- the dispatcher owner and supervised service lifecycle;
- project-local instructions and canonical verification commands;
- implementer and independent reviewer profiles;
- review-dispatch policy and concurrency caps;
- backend/model route and its credential ownership without printing secrets;
- deployment policy and release owner.

If any prerequisite is missing, inspect the live state and record the gap. Do not invent a worker, model, repository, or deployment contract.

## Live Stall Procedure

### 1. Establish a current baseline

Use the terminal tool to collect, in one bounded pass:

```text
hermes kanban --board <board> stats --json
hermes kanban --board <board> list --status running --json
hermes kanban --board <board> list --status review --json
hermes kanban --board <board> list --status todo --json
hermes kanban --board <board> diagnostics --json
hermes gateway status
```

Reconcile counts programmatically. Enumerate every active review, blocker, and runnable-looking task. A count that does not reconcile is a failed inspection, not a minor formatting issue.

**Completion criterion:** the report names the exact current counts, every running/review/blocked task, and whether the gateway is supervised by the expected owner.

For every blocked task that needs human input, include its current reason and
required decision in the central dispatcher/digest report. Do **not** create a
Matrix subscription for the individual task. Under the central-reporting
policy, task-level `notify-list` entries should remain empty: workers write
board state and events, while the dispatcher or HEX digest informs the human.
If the central reporting path is unavailable, escalate that observability
failure to the orchestrator rather than routing a worker directly to Matrix.

### 2. Classify why work is not moving

For each task, distinguish:

- **dependency-gated:** todo with an unfinished parent;
- **review-gated:** review handoff waiting for an enabled reviewer lane;
- **dispatch-stalled:** ready/runnable work with no claim or spawn despite a healthy assignee;
- **backend-failed:** worker starts but model/auth/provider requests fail;
- **capacity-limited:** workers are healthy individually but concurrent fan-out overloads the backend;
- **product-blocked:** repeated implementation timeout, reproducible defect, missing environment capability, or human decision;
- **verified/closed:** independent evidence and board state agree.

Inspect parent links and latest events rather than inferring readiness from the title or priority. A todo card behind a blocked parent is expected gating, not proof that the dispatcher is broken.

**Completion criterion:** every apparent stall has one named cause with task/run/event evidence.

### 3. Check dispatcher ownership and service state

Inspect the supported gateway/dispatcher status. If the service definition is stale, use the documented supervised lifecycle. Do not run a second gateway from a shell to "unstick" the board; concurrent writers can race on the Kanban database and create misleading state.

After any supervised lifecycle action, do not stop at the command's success message. Continue with the live board and worker checks below.

**Completion criterion:** exactly one dispatcher owner is identified, and its service definition/process state is known.

### 4. Inspect gates and profiles

Read the effective configuration without printing secrets. Pay particular attention to:

- `kanban.dispatch_in_gateway`;
- `kanban.review_dispatch`;
- `kanban.failure_limit`;
- `kanban.max_in_progress`;
- `kanban.max_in_progress_per_profile`;
- the assigned profile's existence and model/provider route.

A review profile can exist while review dispatch is explicitly disabled. Conversely, enabling review dispatch without a working reviewer route merely converts a quiet gate into a retry storm.

**Completion criterion:** every review handoff has an explicit decision: dispatchable now, intentionally human-only, or blocked by a named backend/profile issue.

### 5. Verify model routes before rerouting work

Use a bounded non-secret probe:

1. query the authenticated model catalog if supported;
2. select an exact model ID returned by that catalog;
3. send one minimal non-streaming completion with a tiny output budget;
4. record only status, model ID, latency/error class, and whether authentication succeeded;
5. never print API keys, auth-file contents, or full provider error pages.

A model appearing in configuration is not proof that it authenticates, is authorized for the selected provider, or tolerates concurrent workers. Test the actual route the worker profile will use, not a different shell default.

**Completion criterion:** the replacement route has a successful bounded probe, or the task remains explicitly blocked on external provider recovery.

### 6. Recover only infrastructure-caused failures

When a reviewer/backend outage is fixed:

1. update the owning profile or task route to the verified model/provider;
2. read the exact setting back;
3. use the Kanban unblock/requeue operation for only the affected cards;
4. include the reason in the durable comment/event;
5. read every target task back and confirm its resumed status, assignee, and reset retry state;
6. leave genuine product timeouts and human blockers untouched.

Do not blindly retry a task with the same failed backend. Do not reset a failure counter merely to make the dashboard look healthy.

**Completion criterion:** each requeued card has a recorded infrastructure cause and a verified replacement route; unrelated blockers remain intact.

### 7. Bound concurrency and account for reload semantics

Set a per-profile cap based on observed backend capacity. A single successful probe does not validate four simultaneous agents. Prefer a stable review lane with fewer workers over repeated overload/crash cycles.

Some gateway watchers capture concurrency settings at startup. A persisted config edit may therefore be durable but not active in the current process. If reload is needed, use only the supervised lifecycle, and expect to re-check running workers and claims afterward. Never start an unmanaged duplicate to apply a setting.

**Completion criterion:** the effective running cap is known, and the active worker count cannot continue an observed overload pattern.

### 8. Verify real recovery

Require all of the following before calling the factory recovered:

- a task was claimed in the intended source lane;
- a worker PID/run was created;
- the worker is alive or produced a terminal run outcome;
- a heartbeat or equivalent liveness event exists for long work;
- the board recorded a status/event delta;
- independent review or implementation completion is not confused with a self-report;
- remaining blockers are explicitly named.

For completed work, read the task/run back and verify the summary, tests, diff/review evidence, and final board status. A service-start response, a worker process alone, or a digest with new counts is insufficient.

**Completion criterion:** live task/run/event evidence supports the exact recovery claim.

## References

When working from a repository checkout, use the companion references for the detailed runtime-drift symptom matrix and stall-recovery contract:

- `references/dispatcher-runtime-drift.md`
- `references/stall-recovery.md`

The core procedure above remains self-contained for direct `SKILL.md` installation.

## Reporting Shape

Use direct sections, not a vague success paragraph:

- **Live state:** exact counts and board slug;
- **Factory health:** dispatcher owner, review gate, active worker evidence;
- **Changes made:** exact config/task transitions, each read back;
- **Verified progress:** task IDs, run/events, independent verdicts;
- **Unresolved blockers:** task IDs, failure class, next condition for retry;
- **Decision:** continue automatically, queue behind capacity, recover a worker, or stop for human input.

Never say "handled" when the state is only mentioned, queued, or running. Use `fixed`, `verified`, and `closed` only when their evidence criteria are satisfied.

## Pitfalls

- Replying to a no-change digest with a restatement instead of checking the live factory.
- Treating a digest baseline as the start of the unchanged interval.
- Assuming todo means ready without reading parent links.
- Assuming review tasks run automatically when `review_dispatch` is false.
- Treating a model catalog entry or one successful request as proof of auth and fan-out capacity.
- Requeueing genuine product timeouts under the label of infrastructure recovery.
- Launching a second gateway or dispatcher to compensate for a suspected stall.
- Assuming a config write changed a watcher that captured settings at startup.
- Claiming recovery from a worker self-report or a successful service command without board events and heartbeats.
- Reporting counts from memory or prose when current JSON disagrees.

## Verification Checklist

- [ ] Current board JSON/stats reconciled.
- [ ] Every review, running, todo, and diagnostic task enumerated.
- [ ] Parent dependencies inspected for apparent todo stalls.
- [ ] Single dispatcher owner and supervised service state confirmed.
- [ ] Review-dispatch policy and profile existence checked.
- [ ] Replacement backend route probed without exposing secrets.
- [ ] Requeues limited to infrastructure-caused failures and read back.
- [ ] Per-profile concurrency cap selected for actual backend capacity.
- [ ] Worker PID/run and heartbeat or terminal event observed.
- [ ] Independent review and deployment evidence kept separate from implementation claims.
- [ ] Final report distinguishes queued, gated, blocked, fixed, verified, and closed.
