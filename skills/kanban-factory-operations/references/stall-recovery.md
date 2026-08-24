# Kanban Factory Stall Recovery Reference

Use this reference with `kanban-factory-operations` when a board has stopped producing verified progress.

## Evidence matrix

| Observation | Likely class | Required next check | Safe action |
|---|---|---|---|
| `todo` with unfinished parent | dependency-gated | Read parent IDs/statuses | Leave queued; do not promote manually |
| `review` assigned to a real profile, no running review | review gate | Read effective `review_dispatch` and profile existence | Enable only when policy and backend are real |
| `ready` assigned/unclaimed, no spawn | dispatcher/profile issue | Check gateway owner, profile, PATH/auth, dispatch diagnostics | Repair owner/profile; do not start a duplicate dispatcher |
| worker starts then exits on auth/model error | backend route failure | Query model catalog and run one bounded completion | Change route only after the probe passes |
| one probe passes, concurrent workers fail | capacity overload | Inspect concurrent runs and backend error timing | Lower per-profile cap; queue excess review |
| repeated timeout with no product result | product/environment blocker | Read exact command, timeout ceiling, focused evidence | Preserve blocker; create focused remediation/repro task |
| worker says complete but board remains review/running | protocol/evidence gap | Read task run/events and exact status | Do not close; require terminal Kanban transition |

## Compact command pass

Run through the terminal tool with the real board slug:

```text
hermes kanban --board <board> stats --json
hermes kanban --board <board> list --status running --json
hermes kanban --board <board> list --status review --json
hermes kanban --board <board> list --status todo --json
hermes kanban --board <board> diagnostics --json
hermes gateway status
```

Then inspect individual tasks and runs:

```text
hermes kanban --board <board> show <task-id> --json
hermes kanban --board <board> runs <task-id> --json
hermes kanban --board <board> log <task-id>
```

Use the board database only for read-only reconciliation when the CLI output is insufficient. Do not mutate it with ad-hoc SQL.

## Backend probe contract

A probe must use the same authenticated provider route and exact model ID that the worker profile will use. Record:

- HTTP status or structured success/failure;
- exact model ID, without normalizing it;
- whether the failure is auth, authorization, overload, timeout, or provider-side;
- a tiny response budget and non-streaming request;
- no credential values or full auth-file/provider HTML in logs.

A catalog listing is discovery, not authorization. A single successful probe is route health, not concurrency capacity.

## Requeue contract

Only requeue a task after the infrastructure cause is fixed and attributable:

1. include the cause and replacement route in the durable reason;
2. use the Kanban unblock/requeue operation, not direct SQL;
3. read the task back and confirm status, assignee, current run, and retry counter;
4. let the single dispatcher claim it;
5. verify spawned PID/run and heartbeat;
6. stop retrying if the new attempt reproduces a product timeout or backend overload.

Do not requeue a task merely because the user wants a lower blocked count. A lower number without a changed cause is dashboard cosmetics.

## Reload and concurrency note

Gateway watchers may resolve `max_in_progress_per_profile` once at startup. After changing it, distinguish:

- **persisted:** the config file reads back the new value;
- **effective:** the running watcher reports/behaves with the new value;
- **verified:** active worker count and backend errors show the overload pattern has stopped.

If a supervised reload is required, use the supported gateway lifecycle and re-check the board afterward. Never run a second gateway or standalone dispatcher against the same board.

## Recovery report minimum

End with:

- current counts and exact board slug;
- every active review/running task;
- dispatcher owner and effective gates;
- changes made and read-back evidence;
- verified worker/run/event evidence;
- remaining product, environment, and human blockers;
- whether the factory is running, capacity-limited, gated, or blocked.
