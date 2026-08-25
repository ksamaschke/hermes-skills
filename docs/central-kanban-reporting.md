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
reads the live board and dispatcher evidence, then reports:

- board counts and completed work since the previous digest;
- running, review, ready, and todo work;
- every blocked task ID and title;
- the blocker class: dependency, parked backlog, capability/authorization,
  operator disposition, product failure, or orchestration failure;
- the exact reason and the action or decision required from Karsten;
- gateway/dispatcher health and whether `IDLE-BY-GATING` is intentional.

When no work is spawnable, the digest must not stop at “IDLE-BY-GATING”. It
must enumerate the blockers and say which ones need Karsten and which ones are
intentionally parked. The digest must not create task subscriptions or ask a
worker to message the user.

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
