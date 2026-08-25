# Kanban Worker Budget Recovery Reference

## Validated first layer

A recurring factory failure was caused by durable Kanban workers inheriting an interactive profile budget of 90 tool-calling turns. Large implementation and verification cards reached `90/90` before their lifecycle handoff and were then recorded as timeout/breaker failures.

The reusable first-layer fix was:

1. Add `kanban.worker_max_turns` to the canonical config defaults/schema with a bounded default.
2. Resolve the setting from the root Kanban config at worker spawn time.
3. Pass it after the `chat` subcommand:

```text
chat --max-turns <kanban.worker_max_turns> -q "work kanban task <id>"
```

4. Keep profile `agent.max_turns` unchanged for interactive sessions.
5. Add worker-argv and config-recognition regression tests.
6. Verify using the repository-managed environment:

```bash
uv sync --extra dev --locked
uv run pytest -q <focused-kanban-tests>
```

## Recovery evidence contract

After applying a fix, do not call the factory recovered merely because the process started. Read back:

- board status and counts;
- task run sequence;
- worker PID and command line;
- heartbeat or useful progress in the worker log;
- terminal Kanban completion/block or independent review handoff.

Preserve old failed runs and useful workspaces. Add a durable recovery comment before unblocking internal cards. Respect per-profile WIP caps; a card that cannot start immediately should remain `ready`.

## Remaining continuation requirement

A larger bounded segment prevents a turn-budget mismatch but is not a full continuation protocol. If a worker reaches the ceiling, the factory should persist a checkpoint and classify the run as `continuation_pending` rather than incrementing the failure breaker. The checkpoint must carry the workspace, run/session context, completed criteria, remaining criteria, and next action. Bound the number of continuation segments and total wall-clock time; repeated no-progress segments are real failures and must escalate.

Do not silently reset counters, endlessly respawn the same prompt, or turn internal execution problems into human blockers.
