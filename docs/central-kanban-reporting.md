# Central Kanban Reporting

## Policy

Kanban workers and individual task cards do not contact Matrix or any other
human channel directly. They write task state, runs, comments, and events to
the board. One central dispatcher/orchestrator path reads that state and HEX
reports the aggregate result through the configured progress digest.

Task-level `kanban_notify_subs` rows are not part of this policy and should stay
empty. Do not use `notify-subscribe` as a substitute for dispatcher
observability.

## Runtime configuration

The user-local Hermes configuration must disable automatic per-task
subscriptions:

```yaml
kanban:
  auto_subscribe_on_create: false
```

Apply it with the Hermes CLI rather than editing secrets or copying the entire
user config into Git:

```bash
hermes config set kanban.auto_subscribe_on_create false
hermes config get kanban.auto_subscribe_on_create
```

The gateway must be restarted from a shell outside the running gateway process
so the watcher reads the new value:

```bash
hermes gateway restart
hermes gateway status
```

The exact `~/.hermes/config.yaml` remains user-local runtime state. This
repository stores the reproducible configuration contract, not credentials or
machine-specific full config files.

## Central digest contract

The central Minna progress digest is the human-facing reporting path. Each run
reads the live board and dispatcher evidence, applies the human-impact filter
below, and then reports:

- board counts and completed work since the previous digest;
- running, review, ready, and todo work;
- verified progress and active work;
- genuine human decisions only: product/design/priority, explicit external
  authorization that the factory cannot obtain, security/payment/credential
  approval, deployment/release approval, or deliberate parked-task steering;
- gateway/dispatcher health and whether `IDLE-BY-GATING` is intentional.

Internal execution failures are not human blockers. Gateway/dispatcher
recovery, worker timeouts, reviewer/tool capability gaps, CuaDriver/TCC state,
provider failures, stale diagnostics, and routine board repair are owned by the
factory. The digest may state that factory recovery is handling internal state,
but must not ask Karsten to restart, respawn, unblock, grant permissions, or
inspect logs.

Parked backlog is not user-blocked work. Never enumerate parked task IDs, ages,
or `stuck_in_blocked` diagnostics in the human digest. At most report
`Parked backlog unchanged; no action required.` Never unblock or dispatch a
parked task.

When no genuine human decision exists, the digest must say `No human action
required` and report concise verified progress. The digest must not create task
subscriptions or ask a worker to message the user.

## Verification

From the active Hermes profile, verify:

```bash
hermes config get kanban.auto_subscribe_on_create
hermes kanban --board minna stats --json
hermes kanban --board minna list --status blocked --json
hermes kanban --board minna notify-list --json
hermes gateway status
```

Expected state for this policy:

- `auto_subscribe_on_create` is `false`;
- the central digest job is enabled and delivers to its configured origin;
- blocked tasks are described in the digest;
- task-level notification subscriptions are empty;
- one supervised gateway owns dispatcher and digest delivery.
