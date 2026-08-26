# Reusable tracker-to-Kanban reconciliation

This document defines the source-to-execution pattern for code and
issue-tracking systems. It is not a provider-specific or product-specific
implementation.

## Boundaries

- **Source tracker:** owns item identity, title/body, labels or fields, source
  state, grouping, and declared dependency metadata.
- **Adapter/reconciler add-on:** normalizes source data, filters actionability,
  maps stable identities to Kanban tasks, resolves dependencies, and reports
  metrics.
- **Hermes Kanban:** owns claims, worktrees, branches, retries, review handoffs,
  execution evidence, and terminal state.
- **Supervised Hermes gateway:** is the sole dispatcher.
- **Orchestrator:** is the operating and architecture authority within project
  policy. It owns decomposition, tracker writes through the declared adapter,
  adjudication, continuations, recovery, and human decisions.

The reconciler never calls `kanban dispatch`, launches a second gateway, or
claims worker capacity.

## Adapter contract

The shared pattern supports Forgejo/Gitea, GitHub, GitLab, Bitbucket, and custom
REST, GraphQL, CLI, or webhook integrations. Each project adapter declares how
its source provides:

- complete paginated listing of work items and source states;
- detail, comments/activity, labels/fields, timestamps, URLs, and item kind;
- stable provider identity even when a title changes;
- explicit dependencies or verified native relationships;
- authentication through a configured helper, login, service account, or secret
  reference without printing credentials;
- closure, parking, blocking, reopening, deletion, and update detection.

No provider's epics, milestones, labels, pull requests, sub-issues, or dependency
API are assumed to mean the same thing elsewhere.

## Project overlay

Shared skills define mechanics and invariants. A project overlay supplies the
source kind, host/project, adapter, board, worktree mappings, profile mappings,
actionability rules, dependency fields, polling/webhook policy, and reporting
destination:

```yaml
version: 1
source:
  kind: github # forgejo, gitlab, bitbucket, or custom
  base_url: https://tracker.example.test
  project: owner/repository
  adapter: declared-cli-or-api-client
  auth: declared-login-or-credential-helper
board:
  slug: project-board
  worktree_roots:
    core: project-checkout
profiles:
  implementer: existing-implementer
  code_reviewer: existing-reviewer
actionability:
  exclude_states: [closed]
  exclude_labels: [epic, parked, blocked, needs-human, wontfix]
  dependency_fields: [Depends on, Gated by]
  grouping_fields: [Parent, Epic, slice]
```

Do not put secrets, absolute machine paths, model IDs, or product assumptions in
the shared skill. Do not invent profiles; resolve exact names from live Hermes
state. If multiple factories need different tools or permissions, create custom
profiles rather than changing the shared role contract.

The Hermes runtime that owns the board and dispatcher is responsible for
provisioning the selected adapter, credentials, and network access. The shared
skills do not provision tracker connections or assume that one runtime's tools
exist in another profile.

## Identity and idempotency

Use a canonical source key:

```text
tracker:<kind>/<normalized-host>/<project>#<provider-item-key>
```

Store the key, exact source URL, item kind, source timestamp, and generated
source-section hash in task metadata or a clearly delimited body section. Read
archived tasks during migration. Title parsing is only a legacy fallback.

One source item may intentionally fan out into an intake task and execution
children. The reconciler indexes existing descendants but does not create them.
If a project add-on creates descendants, it owns a deterministic child key such
as `<parent-source-key>#child:<project-child-key>` and its own idempotency tests.
Preserve that one-to-many decomposition rather than creating a second intake
task on every poll.

## Actionability and dependencies

Filter source items deterministically. Tracking epics, parked work, blocked
work, human-only work, and wontfix items must not reach ready dispatch. Keep
excluded items visible in the reconciliation report.

Treat `Parent`, `Epic`, milestones, components, and slice labels as grouping
metadata unless project policy makes them execution gates. Parse only declared
blocker fields such as `Depends on` or verified native dependency relations into
real Kanban parent links. Resolve identities in a second pass, detect missing
references and cycles, and never silently drop a blocker.

This supports projects with parked epics and body markers as well as projects
using native sub-issues or linked work items, without forcing one convention on
the factory.

## Reconciliation

A full synchronizer is more than an additive importer:

- create one canonical intake task for a new actionable item;
- update generated source metadata, priority, assignment policy, and dependency
  edges for an existing item;
- preserve worker comments, claims, worktrees, review evidence, and terminal
  history;
- prevent new dispatch when a source item becomes parked or blocked;
- handle closure and reopening through an explicit policy;
- report inaccessible or deleted source data instead of assuming closure;
- never overwrite execution results with a refreshed source body.

The first two runs are dry runs. A stable second run must produce zero duplicate
creates and a coherent identity/dependency/state-transition report before
recurring writes are enabled.

Every run reports named counters for source seen/actionable/excluded/matched,
created/updated, parked/blocked/reopened/closed/archived,
deleted_or_inaccessible/state_drift, dependency edges/unresolved dependencies,
duplicate source IDs, source errors, board errors, and dry-run mode.

## Reviewer handoff

A source item becoming review work receives a fresh review card governed by
`kanban-reviewer-contract`. The packet has exact scope, candidate commit,
read-only source boundary, one lens, a 600-second adversarial leaf cap, one
retry, and a structured verdict. Reviewer tasks never contain implementation or
tracker-issue filing instructions.

## Add-ons, not shared-skill edits

Project-specific behavior belongs in:

1. a project policy/overlay;
2. a project-local adapter or fixture;
3. an external factory add-on repository or script;
4. a project-specific profile `SOUL.md` that remains generic in its role rules
   and receives project values through task packets.

Do not modify the shared reusable skills or Hermes core to add a product's host,
board, repository layout, profile names, model route, dependency convention, or
review protocol. Extend the adapter contract only when multiple independent
projects demonstrate a reusable gap.

## Verification

Before enabling a project adapter, verify:

- complete paginated source inventory and exclusion counts;
- canonical source identity and idempotent rerun behavior;
- target checkout and profile mappings;
- real Kanban dependency links and cycle detection;
- conservative source-state transitions;
- read-back of every task mutation;
- one supervised gateway as dispatcher;
- fresh reviewer packets for all review work;
- structured metrics for source, board, dependency, state-transition, and adapter errors.
