---
name: software-factory-recovery
description: "Use when a software factory stalls; repair and resume work."
version: 1.0.0
author: HEX
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [software-factory, kanban, recovery, continuation, orchestration]
    related_skills:
      - kanban-factory-operations
      - kanban-progress-evidence
---

# Software Factory Recovery

Recover an autonomous software factory when work stops behind worker failures, stale diagnostics, oversized cards, incomplete handoffs, or dependency gates. This skill is the adaptive recovery layer around a Kanban dispatcher. It does not authorize weakening tests, bypassing review, or changing deployment policy.

## Trigger

Use when a factory digest reports stalled work, zero workers, repeated worker timeouts, circuit-breaker blocks, stale capability claims, missing reviewer handoffs, or a ready queue that is not advancing.

## User expectation

Treat internal execution failures as factory-owned. Do not leave work human-blocked because of a worker iteration ceiling, stale capability note, missing handoff, dirty but useful partial worktree, dispatcher observability gap, test-environment mismatch, or a task that needs decomposition. Leave `blocked` only for a genuine human decision, external authorization/capability, or explicit operator disposition. A project policy may make a `parked` backlog item human-gated; verify that policy instead of inferring it.

## Recovery invariants

The factory is an add-on around Hermes, not a replacement for Hermes runtime behavior. Keep deterministic repairs in `~/.hermes/scripts/kanban_factory_recovery.py` and user-owned cron/board operations. Do not modify Hermes source for per-job pinning or routine worktree recovery.

Before escalating, run the recovery layer. It promotes legacy cron snapshots with `hermes cron edit`, repairs only duplicate clean managed worktrees, preserves dirty work, unblocks only after readback, and leaves product/review/auth/capability/deployment decisions for explicit handling.

1. The live board, process state, runs, events, workspace, and tests outrank a digest or worker summary.
2. One supervised dispatcher owns a board. Never start a second long-lived dispatcher to compensate for uncertainty.
3. Preserve partial work before requeueing. Read the worktree status and prior run handoff; do not discard dirty implementation state.
4. A new worker PID is not recovery. Require a run, heartbeat or useful progress, and a valid terminal transition/review handoff.
5. A failed review or timed-out reviewer is incomplete evidence, not human approval or a product blocker.
6. Requeue internal failures only after changing the cause or task shape. Do not reset counters merely to improve dashboard numbers.

## Live recovery procedure

### 1. Establish a baseline

Collect in one bounded pass:

```bash
hermes gateway status
hermes kanban --board <board> stats --json
hermes kanban --board <board> list --status ready --json
hermes kanban --board <board> list --status running --json
hermes kanban --board <board> list --status review --json
hermes kanban --board <board> list --status blocked --json
hermes kanban --board <board> diagnostics --json
hermes kanban --board <board> notify-list --json
```

Reconcile counts programmatically. Inspect every root blocker and every task that appears ready. Classify each as internal execution, dependency, review, external capability/authorization, or explicit operator disposition.

### 2. Repair the worker execution contract

Interactive profile limits must not silently cap durable Kanban jobs. Add a recognized `kanban.worker_max_turns` config key with a bounded default and pass it after the `chat` subcommand:

```text
hermes ... chat --max-turns <kanban.worker_max_turns> -q "work kanban task <id>"
```

Register the key in the canonical config defaults/schema, not only in a local YAML file. Keep the interactive profile's `agent.max_turns` unchanged.

Use the project-managed test environment, not a mixed system interpreter:

```bash
uv sync --extra dev --locked
uv run pytest -q <focused-kanban-tests>
```

The bounded-budget change is a reusable first layer. It is not proof that the entire factory is recovered.

### 3. Preserve and resume internal cards

For each internal breaker-blocked card:

1. read the full task, comments, runs, events, and workspace status;
2. add a durable recovery comment naming the changed execution contract;
3. explicitly unblock/reclaim the card, preserving prior run history and the workspace;
4. verify the card's new status and current run;
5. verify the actual spawned command contains the worker budget;
6. verify PID, heartbeat/progress, and eventual terminal handoff.

Respect global and per-profile WIP caps. Cards that cannot run immediately should remain `ready`, not be mislabeled human-blocked.

### 4. Use semantic continuation, not infinite retry

A full factory should distinguish these outcomes:

- `completed`: worker called the terminal completion protocol and evidence is present;
- `blocked_human`: genuine human decision, external authorization, or unavailable capability;
- `continuation_pending`: worker reached a bounded segment boundary with a valid checkpoint and no failure;
- `failed`: crash, provider failure, real timeout, protocol violation, or no-progress segment.

`continuation_pending` must preserve workspace, session/run context, summary, completed criteria, remaining criteria, and next action. The dispatcher may start a bounded continuation segment without incrementing the failure breaker. Set a maximum segment count and wall-clock budget; repeated no-progress segments become `failed` and escalate. Do not implement continuation by silently resetting counters or endlessly respawning the same prompt.

### 5. Escalate failures durably

On a real failure episode, emit one actionable event containing task id/title, profile, run sequence, budget, failure threshold, last error, dependencies, workspace, and next action. Deduplicate by task plus failure episode. Deliver through existing `notify+wake` subscriptions to the main agent/human. Existing subscriptions created after an old event do not replay that event; add a current durable comment when applying recovery.

### 6. Verify the recovered factory

Do not report `ACTIVE` or `RECOVERED` until all are true:

- at least one intended task was claimed after the repair;
- its command used the worker-specific budget;
- a live PID and run record exist;
- a heartbeat or useful progress event exists;
- a terminal Kanban transition or independent review handoff exists;
- the board counts and remaining blockers were read back;
- no claim relies only on worker prose.

## Reporting

Use these sections:

- **Live state:** exact board counts and gateway owner;
- **Internal repairs:** code/config/task transitions and readbacks;
- **Verified progress:** task/run/PID/heartbeat/terminal evidence;
- **Human blockers only:** explicit decisions or external authorizations;
- **Remaining work:** queued, dependency-gated, review-gated, or failed;
- **Decision:** continue automatically, queue behind capacity, or request a human decision.

Never call a card handled when it is only mentioned, running, or queued. Use `fixed`, `verified`, and `closed` only with evidence.

## References

For the validated bounded-worker-budget implementation and test-environment procedure, see `references/kanban-worker-budget-recovery.md`. The continuation outcome remains an architectural requirement until covered by implementation and integration tests.

## Pitfalls

- Treating the absence of an action-only dispatcher log as proof that the dispatcher is dead;
- blindly unblocking the same oversized task without changing its execution contract;
- raising the global interactive budget instead of setting a worker-specific bound;
- mixing the system pytest interpreter with the Hermes environment;
- treating stale CuaDriver or capability comments as current live state;
- treating a worker summary, PID, or green focused test as final completion;
- converting every failure into a human blocker instead of repairing internal logic;
- starting a duplicate dispatcher or writer against the same SQLite board.
