# hermes-skills

Reusable workflow skills for [Hermes Agent](https://hermes-agent.nousresearch.com/), focused on durable multi-agent engineering, review, and operations.

This repository is intentionally **policy-aware rather than policy-hardcoded**. The skills define safe defaults and require each project to declare its tracker (Forgejo or GitHub), test gate, deployment policy, profiles, and operational constraints.

## Included skills

### `scoped-subagent-audits`

A bounded audit/review procedure that requires explicit working scope,
appropriate time budgets, checkpoints, timeout recovery, and parent-side
verification of worker claims.

### `kanban-implementation-workflow`

A Forgejo/GitHub + Hermes Kanban workflow for:

- importing actionable issues while excluding tracking epics;
- turning `Depends on:` into real task dependencies;
- decomposing work into many small cards without unbounded fan-out;
- TDD-first implementation in isolated worktrees;
- independent adversarial review;
- headless-first testing with explicit UI-smoke exceptions;
- project-defined GitOps and rollout controls;
- durable monitoring, notifications, and progress digests.

### `kanban-factory-operations`

Live operation and recovery for stalled factories: dispatcher ownership, gateway/runtime drift, reviewer capacity, narrow requeueing, and ACTIVE/IDLE-BY-GATING/STALLED health classification.

### `kanban-progress-evidence`

Evidence and closure accounting for digests and reviews: complete inventories, durable records, dependency gates, timeout attribution, independent verification, and explicit queued/blocked/fixed/verified/closed language.

### `software-factory-recovery`

Adaptive recovery for autonomous factories. The skill uses the external
`scripts/kanban_factory_recovery.py` add-on to repair legacy cron inference
snapshots and deterministic duplicate clean worktree failures without changing
Hermes core or discarding dirty work.

The core skill does not select a deployment controller. Each project declares its own rollout policy; the repository includes an illustrative GitOps/Argo policy example, but Argo is not a requirement of the workflow.

See [`docs/skill-areas.md`](docs/skill-areas.md) for the area-of-interest map,
the software-factory skill set, supporting files, and the explicit Hermes
synchronization allowlist.

Central Kanban reporting, the versioned runtime configuration contract, and
the dispatcher/digest boundary are documented in
[`docs/central-kanban-reporting.md`](docs/central-kanban-reporting.md).

## Tracker adapters

The workflow supports both self-hosted Forgejo and GitHub. The project policy selects the adapter and source-of-truth conventions:

- Forgejo: use `tea` or the Forgejo REST API;
- GitHub: use `gh` or the GitHub REST/GraphQL API;
- preserve each tracker’s native issue URLs, labels, comments, and dependency conventions;
- do not assume that a GitHub milestone, Forgejo epic label, or project-board field has the same meaning on another tracker.

The Kanban card format is tracker-neutral: retain the source tracker, issue number, URL, labels, body, and parent dependencies in imported metadata.

## Install

Install the Skill directly from GitHub:

```bash
hermes skills install \
  https://raw.githubusercontent.com/ksamaschke/hermes-skills/main/skills/kanban-implementation-workflow/SKILL.md

hermes skills install \
  https://raw.githubusercontent.com/ksamaschke/hermes-skills/main/skills/kanban-factory-operations/SKILL.md

hermes skills install \
  https://raw.githubusercontent.com/ksamaschke/hermes-skills/main/skills/kanban-progress-evidence/SKILL.md

hermes skills install \
  https://raw.githubusercontent.com/ksamaschke/hermes-skills/main/skills/software-factory-recovery/SKILL.md
```

Once installed, load the set explicitly for a session:

```bash
hermes --skills kanban-implementation-workflow,kanban-factory-operations,kanban-progress-evidence,software-factory-recovery
```

The deterministic recovery add-on is installed separately from the skill
documents:

```bash
install -m 755 scripts/kanban_factory_recovery.py ~/.hermes/scripts/kanban_factory_recovery.py
```

Schedule it as a silent `no_agent` cron job every few minutes. It must remain
outside Hermes core and only repair the mechanical cases documented by the
recovery skill.

Existing profiles need to load or install the skill explicitly. Newly cloned profiles inherit the skill set available at clone time.

## Policy resolution

The workflow resolves policy in this order:

1. explicit user instructions;
2. repository `AGENTS.md`, `CLAUDE.md`, or equivalent project rules;
3. board/task fields and the project policy file;
4. the skill's safe defaults;
5. otherwise stop and ask rather than guessing.

Use [`examples/project-policy.yaml`](examples/project-policy.yaml) as a starting point. See [`docs/policy-resolution.md`](docs/policy-resolution.md) for how to adapt it.

## Role model

Profiles represent **roles, permissions, and model routing**, not repositories:

```text
orchestrator  →  implementer  →  reviewer  →  integrator/release
     │              │               │              │
  kanban only   write worktree   read-only      project policy
```

Recommended reusable profile roles:

- `orchestrator` — decomposes, routes, comments, and manages WIP; no code writes;
- `implementer` — TDD-first code changes in isolated worktrees;
- `reviewer` — independent read-only review using a different model/vendor family;
- `qa-ui` — optional native/browser verification lane for UI-only acceptance;
- `release-operator` — optional project-policy-controlled release/GitOps lane.

In `project-policy.yaml`, role keys use snake_case (`qa_ui`, `release_operator`) while profile names may use hyphens. The mapping is intentional: policy keys are stable schema names; profile values are user-selected identities.

The Hermes gateway dispatcher handles mechanical Kanban lifecycle work: promotion, claims, worktrees, heartbeats, retries, and recovery. An orchestrator profile handles reasoning about decomposition, model strength, WIP, and human decisions.

Use task-level model overrides for strength tiers where possible. Create another profile when behavior, tools, credentials, or memory isolation differ, not merely because a task needs a stronger model.

## Testing principle

Headless-first is a default, not a project-specific mandate:

- unit, integration, HTTP, jsdom, and headless-browser tests run without a desktop shell;
- full desktop/WebView smoke tests are reserved for native-window, sidecar, updater, visual-editor, or other acceptance criteria that genuinely require them;
- any process started by a smoke test is tracked and cleaned up before handoff.

## Deployment principle

Deployment is always project policy, never a hidden default. A project policy declares its rollout mode and controller:

- `gitops_only` with Argo CD, Flux, or another named controller;
- a release pipeline or explicitly approved direct mode;
- `unspecified`, which means no production mutation is allowed until clarified.

The example policy demonstrates one GitOps/Argo arrangement without baking any user's infrastructure paths, rooms, tokens, or repository names into the reusable skill.

## Monitoring

Typical board commands:

```bash
hermes kanban --board <board> stats
hermes kanban --board <board> list --status running
hermes kanban --board <board> diagnostics
hermes kanban --board <board> watch \
  --kinds completed,blocked,gave_up,crashed,timed_out
```

Check factory control-plane health separately:

```bash
hermes gateway status
~/.hermes/scripts/kanban_factory_recovery.py --board <board> --dry-run
```

For recurring updates, use a continuity-enabled cron digest and deliver it to a configured gateway home channel. A local CLI/Desktop chat may not have a live cron delivery target.

## Repository layout

```text
skills/scoped-subagent-audits/SKILL.md       bounded audit/review procedure
skills/kanban-implementation-workflow/SKILL.md  reusable agent procedure
skills/kanban-factory-operations/SKILL.md       live factory operation and recovery
skills/kanban-factory-operations/references/    runtime drift and stall recovery
skills/kanban-progress-evidence/SKILL.md        evidence and closure accounting
skills/kanban-progress-evidence/references/     closure matrix template
skills/software-factory-recovery/SKILL.md      autonomous recovery procedure
scripts/kanban_factory_recovery.py              deterministic recovery add-on
examples/project-policy.yaml                    adaptable Forgejo/GitHub policy template
docs/profile-roles.md                            reusable profile role model
docs/policy-resolution.md                        project-specific adaptation guide
docs/reviewer-reliability.md                     bounded review and failure recovery
docs/tracker-adapters.md                         Forgejo/GitHub import guidance
tests/test_skill_frontmatter.py                 lightweight package validation
```

## License

MIT. See [`LICENSE`](LICENSE).
