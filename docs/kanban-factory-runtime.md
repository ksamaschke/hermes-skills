# Kanban Factory Runtime and Routing Contract

This document describes the Hermes Kanban runtime contract used by the Minna
factory. It is a versioned policy/example, not a copy of a user's complete
`~/.hermes/config.yaml`. Secrets, credentials, machine paths, and unrelated
personal settings remain local.

## Required runtime settings

The current Minna baseline uses these `kanban` values:

```yaml
kanban:
  # One supervised gateway owns the dispatcher.
  dispatch_in_gateway: true

  # Review cards are dispatched to the reviewer profile automatically.
  review_dispatch: true

  # Bounded dispatcher cadence and worker recovery.
  dispatch_interval_seconds: 60
  failure_limit: 2
  dispatch_stale_timeout_seconds: 14400
  worker_max_turns: 250

  # Capacity limits. Tune from observed backend and host capacity.
  max_in_progress: 8
  max_in_progress_per_profile: 2

  # Triage decomposition is bounded per tick.
  auto_decompose: true
  auto_decompose_per_tick: 3

  # Human reporting is centralised in the dispatcher/HEX digest.
  auto_subscribe_on_create: false
```

`failure_limit: 2` is a global implementation/recovery breaker. Review leaf
cards still set `max_retries: 1` individually so one incomplete review prompt is
not retried unchanged. Lowering the global breaker to `1` would unnecessarily
make transient implementation failures human blockers.

`dispatch_stale_timeout_seconds` requires long-running workers to emit
heartbeats. A stale reclaim returns the task to `ready`; it is not proof that
the product work failed.

The authoritative Hermes defaults and lifecycle semantics are documented in the
[Kanban feature guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban)
and [worker-lane guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban-worker-lanes).

## Human-facing default profile

`orchestrator_profile` and `default_assignee` are separate settings:

```yaml
kanban:
  orchestrator_profile: ""
  default_assignee: ""
```

An empty `orchestrator_profile` falls back to the active default profile for
the root/orchestration task after decomposition. That is intentional when the
human-facing `default` profile is HEX's orchestration and reporting profile.
There is no need to invent a separate orchestrator profile just to name the
same role twice.

An empty `default_assignee` also falls back to the active default profile when
the decomposer selects an unknown profile. That is safe only when:

- normal implementation and review cards carry explicit valid assignees; and
- the decomposer's profile roster and descriptions are maintained so unknown
  routes are exceptional.

Setting `default_assignee: minna-implementer` is an alternative policy for a
factory that wants unknown implementation children to fall into the worker
lane. It is **not** automatically correct for a human-facing default profile,
because it changes where malformed or ambiguous decomposition results go.
Choose it deliberately rather than treating it as a missing required value.

## Decomposer model

`auto_decompose: true` invokes Hermes' built-in decomposer through the auxiliary
LLM slot:

```yaml
auxiliary:
  kanban_decomposer:
    provider: <explicit-provider>
    model: <explicit-model>
```

The decomposer is not a separate Hermes profile and does not load the
orchestrator profile's prompt or skills. It produces a task graph; the
`orchestrator_profile` only controls ownership of the resulting root task.
The dispatcher then spawns explicit profile workers for the child tasks.

The current Minna setting is explicitly pinned:

```yaml
auxiliary:
  kanban_decomposer:
    provider: openai-codex
    model: gpt-5.6-luna
```

This reuses the verified main/implementer route and makes decomposition
reproducible for this factory. It remains a Minna-specific choice: another
factory may select a different provider/model after its own bounded quality,
latency, and cost probe. The reusable skills do not impose this model.

## Explicit profiles versus implicit subagents

The current architecture uses explicit Hermes profiles:

- `default`: human-facing HEX/orchestration/reporting session;
- `minna-implementer`: implementation worker process;
- `reviewer`: independent read-only review worker process.

The gateway dispatcher still spawns worker **processes** for assigned tasks. It
does not use `delegate_task`-style implicit subagents for the Kanban lifecycle.
The auxiliary decomposer is an LLM call inside the dispatcher, not an implicit
worker profile.

A project `implementation-skills.yaml` is therefore not required by Hermes
Kanban when explicit profiles and task routing are the source of truth. Such a
file only makes sense if the project also uses the separate
implementation-skills/`isc` pipeline for model lanes, gate commands, commits,
PRs, or delegation. If that pipeline is not used, references requiring that
file should be removed rather than adding a redundant policy layer.

## Verification

From the active Hermes profile:

```bash
hermes config get kanban.dispatch_in_gateway
hermes config get kanban.review_dispatch
hermes config get kanban.worker_max_turns
hermes config get kanban.max_in_progress
hermes config get kanban.max_in_progress_per_profile
hermes config get kanban.auto_decompose
hermes config get kanban.auto_subscribe_on_create
hermes kanban --board minna dispatch --dry-run --json
hermes kanban assignees --json
hermes gateway status
```

For a central-reporting deployment, also verify:

```bash
hermes kanban --board minna notify-list --json
hermes cron list --all
```

The task-level notification list should be empty. Human updates come from the
central digest, not individual workers.
