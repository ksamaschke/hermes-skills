# hermes-skills

Reusable workflow skills for [Hermes Agent](https://hermes-agent.nousresearch.com/), focused on durable multi-agent engineering, review, and operations.

This repository is intentionally **policy-aware rather than policy-hardcoded**. The skills define safe defaults and require each project to declare its tracker, test gate, deployment policy, profiles, and operational constraints.

## Included skills

### `kanban-implementation-workflow`

A Forgejo + Hermes Kanban workflow for:

- importing actionable issues while excluding tracking epics;
- turning `Depends on:` into real task dependencies;
- decomposing work into many small cards without unbounded fan-out;
- TDD-first implementation in isolated worktrees;
- independent adversarial review;
- headless-first testing with explicit UI-smoke exceptions;
- project-defined GitOps and rollout controls;
- durable monitoring, notifications, and progress digests.

The skill does **not** assume Minna, Argo CD, a specific model vendor, a particular branch name, or a particular Hermes profile roster.

## Install the skill

Install directly from GitHub:

```bash
hermes skills install \
  https://raw.githubusercontent.com/ksamaschke/hermes-skills/main/skills/kanban-implementation-workflow/SKILL.md
```

Or load it explicitly for a session:

```bash
hermes --skills kanban-implementation-workflow
```

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

Profiles represent **roles and permissions**, not repositories:

```text
orchestrator  →  implementer  →  reviewer  →  integrator/release
                    │               │
                    └── worktree    └── read-only review
```

The Hermes gateway dispatcher handles mechanical Kanban lifecycle work: promotion, claims, worktrees, heartbeats, retries, and recovery. An orchestrator profile or controller handles decomposition, model routing, WIP policy, and human decisions.

Use task-level model overrides for strength tiers where possible. Create another profile only when behavior, tools, credentials, or memory isolation differ.

## Testing principle

Headless-first is a default, not a project-specific mandate:

- unit, integration, HTTP, jsdom, and headless-browser tests run without a desktop shell;
- full desktop/WebView smoke tests are reserved for native-window, sidecar, updater, visual-editor, or other acceptance criteria that genuinely require them;
- any process started by a smoke test is tracked and cleaned up before handoff.

## Deployment principle

The skill never assumes Argo CD. A project policy declares its deployment mode:

- `gitops_only` / `argocd` for repositories where desired state must flow through Git and Argo;
- another explicitly documented mode when a project has a different release contract;
- `unspecified` means no production mutation is allowed until the policy is clarified.

The example policy demonstrates Argo CD without baking Karsten's infrastructure paths, rooms, tokens, or repository names into the reusable skill.

## Monitoring

Typical board commands:

```bash
hermes kanban --board <board> stats
hermes kanban --board <board> list --status running
hermes kanban --board <board> diagnostics
hermes kanban --board <board> watch \
  --kinds completed,blocked,gave_up,crashed,timed_out
```

For recurring updates, use a continuity-enabled cron digest and deliver it to a configured gateway home channel. A local CLI/Desktop chat may not have a live cron delivery target.

## Repository layout

```text
skills/kanban-implementation-workflow/SKILL.md  reusable agent procedure
examples/project-policy.yaml                    adaptable policy template
docs/policy-resolution.md                       project-specific adaptation guide
tests/test_skill_frontmatter.py                 lightweight package validation
```

## License

MIT. See [`LICENSE`](LICENSE).
