# Dispatcher Runtime Drift and Reviewer Capacity

This reference records a reusable recovery pattern for a Kanban factory that looks idle while its control plane or reviewer backend is unhealthy.

## Symptom cluster

- The board has review/todo work but no useful progress.
- `hermes gateway status` reports a supervised PID, yet dispatcher log ticks are absent or old.
- The config file shows one WIP/concurrency policy, while gateway startup logs show another.
- Multiple reviewer workers start together and fail with `Overloaded`, crash, or exit before a terminal Kanban call.
- A tiny model probe succeeds, but a real worker-sized request fails.

This is not one problem. Separate the layers:

1. **Runtime liveness:** is the dispatcher actually ticking?
2. **Configuration freshness:** did the running gateway load the current policy?
3. **Backend capacity:** can the selected profile/model handle the configured fan-out?
4. **Task attribution:** which failures were caused by infrastructure versus product defects?

## Reproduction/verification recipe

Run the board checks and capture the latest dispatcher evidence:

```bash
hermes kanban --board <board> stats --json
hermes kanban --board <board> list --status running --json
hermes kanban --board <board> list --status review --json
hermes kanban --board <board> diagnostics --json
hermes gateway status
```

Compare the effective file setting with the live startup line:

```bash
hermes config get kanban
# inspect the latest gateway.log lines containing:
#   max_in_progress=
#   max_in_progress_per_profile=
#   kanban dispatcher: embedded in gateway
```

A persisted config edit is not active until the watcher/gateway reloads it. If the gateway-owned shell refuses a restart to avoid killing itself, use a separate OS shell or the platform supervisor's supported control path. Do not start a second dispatcher.

After reload, verify all three:

- new gateway PID and supervised status;
- gateway log reports the intended WIP caps;
- board stats/runs show claims and worker activity without an immediate spawn storm.

## Model-capacity test

Test the exact reviewer profile, not the desktop default:

1. Send a minimal non-secret probe and record model/provider/status.
2. Send or observe one representative worker-sized request.
3. Treat `Overloaded` on the representative request as a capacity failure even if the tiny probe returned `OK`.
4. Reduce `max_in_progress_per_profile` to the demonstrated safe level, reload the gateway, and verify only the intended number of reviewer workers can run.

Do not infer capacity from catalog presence, one fast completion, or a different model/provider route.

## Narrow recovery

For a reviewer card whose failures are clearly model/process failures:

```bash
hermes kanban --board <board> unblock <task-id> \
  --reason 'Reviewer backend/control-plane recovery; product handoff remains unverified.'
hermes kanban --board <board> show <task-id> --json
hermes kanban --board <board> dispatch --json
hermes kanban --board <board> runs <task-id> --json
```

Read back the task status, assignee, retry state, run ID, worker PID, and model log before reporting recovery. Leave product timeouts, missing UI evidence, dependency gates, and human decisions blocked.

## Digest wording

If the board is unchanged, report both the unchanged interval and factory classification:

- `ACTIVE`: running workers and recent dispatcher ticks;
- `IDLE-BY-GATING`: no runnable work because named dependencies or human decisions hold it;
- `STALLED`: actionable review/todo work exists but dispatcher liveness, runtime policy, or backend capacity prevents execution.

Never report a stale supervised PID as proof that the factory is healthy.
