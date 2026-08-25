# Skill Areas and Synchronization Boundaries

This repository is the maintained source for reusable software-factory skills and
their supporting references. It is not a mirror of every skill installed in a
Hermes profile.

## Area: Software factory

These skills form the software-factory operating set:

### `kanban-implementation-workflow`

**Primary concern:** end-to-end implementation workflow.

Use for:

- Forgejo/GitHub issue import and dependency mapping;
- task decomposition and WIP limits;
- TDD-first implementation in isolated worktrees;
- independent review and verification;
- headless-first testing and project-policy-controlled release work.

### `kanban-factory-operations`

**Primary concern:** live factory operation.

Use for:

- dispatcher and gateway ownership;
- board health classification;
- reviewer capacity and backend routing;
- infrastructure-caused requeue decisions;
- recovery verification and factory status reporting.

### `kanban-progress-evidence`

**Primary concern:** evidence and closure accounting.

Use for:

- reconciling complete board inventories;
- mapping concerns to durable tasks, comments, runs, commits, and artifacts;
- distinguishing queued, dependency-gated, blocked, fixed, verified, and closed;
- preserving timeout and environment gaps;
- producing closure matrices instead of vague “handled” summaries.

### `software-factory-recovery`

**Primary concern:** autonomous recovery around the dispatcher.

Use when:

- a factory stops behind internal worker or dispatcher failures;
- a legacy cron job has creation-time inference snapshots but no explicit pin;
- a blocked task failed only because a clean managed Git worktree already owns
  its branch;
- a bounded recovery and readback is possible without changing product,
  review, deployment, authorization, or human-decision state.

The companion `scripts/kanban_factory_recovery.py` is deterministic and runs as
a silent `no_agent` cron job. It is an add-on around Hermes, not a replacement
for Hermes runtime behavior.

## Area: Audit and review support

### `scoped-subagent-audits`

**Primary concern:** bounded independent audits.

Use for:

- explicit repository/live-state scope;
- time-budgeted audit workers;
- checkpoint and timeout handling;
- parent-side verification of worker claims.

It supports the factory but is not a dispatcher or implementation workflow by
itself.

## Repository-owned supporting files

- `scripts/kanban_factory_recovery.py` — deterministic recovery add-on;
- `scripts/kanban_factory_recovery_cron.py` — regular in-directory cron shim;
- `tests/test_kanban_factory_recovery.py` — regression tests for collision parsing;
- `skills/kanban-factory-operations/references/` — runtime drift and stall recovery;
- `skills/kanban-progress-evidence/references/` — closure-matrix template;
- `skills/software-factory-recovery/references/` — worker-budget and recovery evidence contract;
- `docs/policy-resolution.md` and `examples/project-policy.yaml` — project policy adaptation.

## Explicit Hermes synchronization allowlist

Only these installed skill directories are symlinked to this repository:

- `scoped-subagent-audits`;
- `kanban-implementation-workflow`;
- `kanban-factory-operations`;
- `kanban-progress-evidence`;
- `software-factory-recovery`.

The symlink destinations are category-specific under `~/.hermes/skills/`. The
repository path is authoritative for the linked directories; changes in the
checkout are immediately visible to Hermes after the normal skill reload.

On Karsten's machine the explicit local links are:

- `~/.hermes/skills/autonomous-ai-agents/scoped-subagent-audits` →
  `skills/scoped-subagent-audits`;
- `~/.hermes/skills/software-development/kanban-implementation-workflow` →
  `skills/kanban-implementation-workflow`;
- `~/.hermes/skills/software-development/kanban-factory-operations` →
  `skills/kanban-factory-operations`;
- `~/.hermes/skills/autonomous-ai-agents/kanban-progress-evidence` →
  `skills/kanban-progress-evidence`;
- `~/.hermes/skills/software-development/software-factory-recovery` →
  `skills/software-factory-recovery`;
- `~/.hermes/scripts/kanban_factory_recovery.py` →
  `scripts/kanban_factory_recovery.py`.

The cron job uses `~/.hermes/scripts/kanban_factory_recovery_cron.py`, which is
deliberately a regular file because Hermes rejects a cron script whose resolved
path leaves the scripts directory. The shim delegates to the canonical linked
recovery script above.

Existing directories were preserved under
`~/.hermes/backups/pre-hermes-agent-skills/` before linking. Keeping the backup
outside `~/.hermes/skills/` prevents Hermes from discovering duplicate skills.
No other Hermes skill directory is linked by this repository.

Profile-scoped workers have their own `HERMES_HOME`. The same five allowlisted
skill links are therefore mirrored under the `reviewer`, `minna-implementer`,
and `default` profile skill roots, and those profiles explicitly trust this
checkout through:

```yaml
skills:
  external_dirs:
    - /Users/karsten/Work/Development/Samaschke/hermes-agent-skills/skills
```

The external-dir entry is required by Hermes' skill security check; a profile
symlink alone is not sufficient when its resolved target lies outside the
profile's local skill root. This is still an explicit five-skill allowlist, not
automatic synchronization of the rest of Hermes' internal skills.

The following remain independent and are **not** symlinked by this repository:

- `hermes-agent`;
- `kanban-worker`;
- `kanban-orchestrator`;
- `subscription-agent-steering`;
- other internal, vendor, platform, or user-specific skills.

Adding another symlink requires an explicit allowlist change. A skill being
related to the factory does not implicitly grant synchronization ownership.

## Installation and verification

From this checkout:

```bash
hermes skills install \
  https://raw.githubusercontent.com/ksamaschke/hermes-skills/main/skills/kanban-implementation-workflow/SKILL.md

install -m 755 scripts/kanban_factory_recovery.py \
  ~/.hermes/scripts/kanban_factory_recovery.py
```

For the local development installation, verify the five allowlisted paths with
`readlink` and verify the source checkout with:

```bash
git status --short --branch
git remote -v
```
