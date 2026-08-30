---
name: tracker-kanban-reconciliation
description: Reconcile issue trackers into Kanban execution tasks.
version: 0.1.0
author: Karsten Samaschke, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [trackers, kanban, backlog, reconciliation, factory]
    related_skills: [kanban-reviewer-contract, kanban-factory-operations, kanban-progress-evidence]
---

# Tracker-to-Kanban Reconciliation Skill

Use this skill to connect a project-specific code and issue tracker to a Hermes
Kanban execution board without coupling the pattern to a vendor, repository,
model, or profile roster. The source tracker remains the backlog source of
truth; Kanban owns execution state, worktrees, claims, retries, review gates,
and evidence; the supervised Hermes gateway remains the only dispatcher.

This is an external factory add-on or project overlay, not a second dispatcher
inside Hermes core. The first run is always a dry run. Do not create tasks until
source identity, target repositories, profiles, dependencies, and counts have
been reconciled.

The orchestrator is the operating and architecture authority for the resulting
task graph and execution decisions within project policy. The reconciler still
owns source normalization and projection; it does not become a dispatcher.

## Factory core versus project add-on

The shared factory provides durable mechanics: source identity, reconciliation,
Kanban task state, isolated worktrees, claims, retries, review gates, evidence,
and supervised dispatch ownership. It does not encode a product's tracker host,
repository layout, labels, profile names, model route, or acceptance policy.

Keep project customization in a project overlay, project-local adapter, or
external add-on. If multiple factories share a tracker host but their profile
tools, permissions, memory, or routing do not match, create custom profiles for
those factories. Preserve the shared lifecycle and safety invariants instead of
modifying the reusable skill or Hermes core for one project.

## When to Use

Use when:

- actionable work items must become durable Kanban tasks;
- a project has manually imported or decomposed backlog work;
- labels, workflow states, or dependency fields determine what may execute;
- a recurring poller or webhook must keep a source backlog and execution board
  aligned;
- several repositories or projects share one product factory.

Do not use this skill to dispatch workers, replace project acceptance criteria,
or make a tracker issue itself the approval gate. Do not blindly re-import a
board that already contains manually decomposed work.

## Source adapter contract

The reconciler is tracker-neutral. A project adapter must declare how its source
provides these capabilities:

- list work items with complete pagination and state/type filters;
- fetch one work item with title, body, labels, state, timestamps, URL, and
  comments or equivalent activity;
- identify work-item kind, such as issue, pull request, ticket, task, or epic;
- read declared dependencies and grouping metadata;
- authenticate through a configured credential helper, CLI login, service
  account, or secret reference without printing credentials;
- detect source updates, closure, parking, blocking, reopening, and deletion;
- expose a stable source key even when the visible title changes.

Supported adapters may include Forgejo/Gitea, GitHub, GitLab, Bitbucket, and
custom REST, GraphQL, CLI, or webhook integrations. The shared skill does not
assume that labels, milestones, epics, sub-issues, pull requests, or dependency
APIs mean the same thing across providers. The project overlay declares the
mapping.

## Project overlay

Keep project values outside this skill. A minimal overlay declares:

```yaml
version: 1
source:
  kind: github # forgejo, gitlab, bitbucket, or custom are also valid
  base_url: https://tracker.example.test
  project: owner/repository
  adapter: declared-cli-or-api-client
  auth: declared-credential-helper-or-login
  poll_interval: 5m
  page_size: 100
board:
  slug: project-board
  project: project-name
  worktree_roots:
    core: project-checkout
profiles:
  implementer: existing-implementer-profile
  code_reviewer: existing-reviewer-profile
actionability:
  exclude_states: [closed]
  exclude_labels: [epic, parked, blocked, needs-human, wontfix]
  dependency_fields: [Depends on, Gated by]
  grouping_fields: [Parent, Epic, slice]
  review_labels: [review-gap]
```

The overlay must use exact existing profile names and repository mappings. Verify
profile existence, checkout roots, remote identity, and project policy before
creating work. Never put tokens, passwords, or API keys in the overlay.

## Canonical source identity

Use a stable key such as:

```text
tracker:<kind>/<normalized-host>/<project>#<provider-item-key>
```

The provider item key may be a numeric issue number or a provider-native stable
identifier. Store the key, exact source URL, work-item kind, source update
timestamp, and a hash of the generated source section in task metadata or a
clearly delimited task body.

Read all non-archived and archived Kanban tasks when building the identity index.
Use the canonical key first; fall back to legacy title/body parsing only for a
one-time migration. A title prefix is a compatibility hint, not the long-term
identity.

If one source item intentionally fans out into several execution tasks, keep
one canonical intake task and mark children as orchestrator-created descendants.
The reconciler indexes those descendants but never creates them. If a project
adapter intentionally creates descendants, that behavior belongs in an external
add-on with a deterministic child key such as
`<parent-source-key>#child:<project-child-key>` and its own idempotency tests.
Do not create duplicates during reconciliation or merge existing work without a
read-back plan.

## Actionability and dependencies

Apply source policy before materialization:

1. Fetch every requested page of work items and, when reconciling existing tasks,
   fetch source states needed to detect closure, parking, or blocking.
2. Exclude configured states, labels, and work-item kinds. Excluded items remain
   visible in the reconciliation report; they are not silently deleted.
3. Treat `Parent`, `Epic`, milestones, components, and slice labels as grouping
   metadata unless the project explicitly defines them as execution gates.
4. Parse only declared blocker fields or verified native dependency relations
   into real Kanban parent links. Never leave a dependency as prose only.
5. Resolve links in a second pass after all source identities are indexed. Detect
   missing references and cycles. Do not create a ready task with an unresolved
   declared blocker.

Projects may use parked epics as navigation anchors and `Depends on:` as an
execution-order field. Other projects may use native sub-issues, linked work
items, or custom fields. Preserve the project distinction without making one
provider's convention universal.

## Materialization rules

For each new actionable item:

- preserve the exact source URL, provider key, title, body, labels, kind, and
  relevant comments in a source section;
- choose the target checkout from the overlay or a validated source field;
- assign an existing implementer or code-reviewer profile from the mapping;
- create an isolated `worktree` task with a stable branch convention;
- set priority, runtime, retry, and project fields explicitly;
- set the canonical source idempotency key;
- add real parent links only after dependency resolution;
- include TDD, validation, review, and deployment boundaries from project policy.

Do not put implementation instructions on a reviewer task. Review handoffs use
`kanban-reviewer-contract` and receive a fresh review packet with a change
manifest, the review kind (`pre_commit` or `pre_merge`), candidate commit,
read-only boundary, the declared two-tier runtime budget, and
`max_retries: 1`.

## Reconciliation rules

A full synchronizer is more than an importer:

- **New actionable item:** create one canonical intake task.
- **Existing actionable item:** update only generated source metadata, priority,
  assignment policy, and dependency edges; preserve worker comments, claims,
  worktrees, review evidence, and terminal history.
- **Newly parked or blocked item:** prevent new dispatch. For unclaimed work,
  schedule or block according to the overlay; for active/review work, record
  source drift and do not silently destroy execution evidence.
- **Closed source item:** preserve the Kanban audit trail. Archive or close only
  through an explicit policy that distinguishes never-started work from completed
  or reviewed work.
- **Reopened item:** requeue only when policy permits and after reading back
  assignee, dependencies, retry state, and worktree status.
- **Changed dependency:** update a safe unclaimed task or create a diagnostic
  for active work. Never silently invalidate a running worker's contract.
- **Deleted or inaccessible source:** report an adapter error; do not assume
  closure and do not delete Kanban history.

Never overwrite a task's execution result with a refreshed source body. Generated
source metadata and worker-owned evidence must have separate sections.

## Run procedure

1. **Discover live prerequisites.** Read the overlay, project instructions,
   profile list, board identity, gateway status, worktree roots, and source
   authentication without printing secrets.
2. **Build a bounded source snapshot.** Paginate through the adapter, normalize
   states, labels, kinds, and dependency fields, preserve source URLs, and record
   fetch errors.
3. **Build the Kanban identity index.** Include archived tasks and legacy source
   forms. Detect duplicate source identities before mutation.
4. **Produce a dry-run plan.** Report source seen, actionable, excluded,
   matched, would-create, would-update, would-park, would-block, would-close,
   would-archive, state drift, dependency edges, unresolved references, cycles,
   and errors.
5. **Reconcile only after the plan is coherent.** Create/update/link tasks with
   explicit fields and stable idempotency. Never call the dispatcher.
6. **Read back every changed task.** Verify source key, target repository,
   workspace kind/path, branch, assignee, parents, status, and retry fields.
7. **Emit bounded JSON metrics.** Include counts and named errors, not secrets or
   full work-item bodies. A recurring no-agent job may deliver the summary to
   the central factory digest, but should not notify each worker task directly.
8. **Verify dispatcher separation.** Confirm one supervised gateway owner and
   that new tasks are claimed only by the normal gateway lifecycle.

Run project adapters through `terminal`, for example:

```text
terminal(command="python <project-reconciler> --config <overlay> --dry-run", timeout=120)
terminal(command="hermes kanban --board <board> stats --json", timeout=30)
terminal(command="hermes kanban --board <board> list --json --archived", timeout=60)
terminal(command="hermes gateway status", timeout=30)
```

Use the declared CLI/API adapter for source reads. Credential helpers and login
state are allowed; printing credential contents is not.

## Reporting and failure policy

Report these counters on every run:

- `source_seen`, `actionable`, `excluded`, `matched`, `created`, `updated`;
- `parked`, `blocked`, `reopened`, `closed`, `archived`, `deleted_or_inaccessible`,
  `state_drift`, `dependency_edges`, `unresolved_dependencies`;
- `duplicate_source_ids`, `source_errors`, `board_errors`, and `dry_run`.

A source outage is an adapter failure, not permission to dispatch stale work.
A duplicate or unresolved identity is a reconciliation blocker, not a reason to
create another task. A gateway or worker failure belongs to factory operations,
not to the source adapter. Preserve the last verified board state and report the
next condition for recovery.

## Verification

The adapter is complete only when:

- a second dry run creates zero duplicates and produces a stable plan;
- every actionable source item maps to exactly one canonical intake task or an
  explicitly recorded one-to-many decomposition;
- excluded tracking items cannot reach ready dispatch;
- declared execution dependencies are real Kanban links and cycles are caught;
- target repositories and isolated worktree paths are verified;
- existing execution evidence survives source metadata refresh;
- source state transitions are reconciled conservatively;
- the gateway remains the sole dispatcher;
- reviewer tasks satisfy `kanban-reviewer-contract` before dispatch;
- counts reconcile against the complete paginated source snapshot.

## Pitfalls

- Treating a project-specific adapter as the reusable factory pattern.
- Using title parsing as the only idempotency mechanism.
- Treating epic parentage as a worker dependency without project policy.
- Reusing a broad implementation card as a reviewer card.
- Letting a reviewer file source issues or create Kanban children.
- Creating tasks before a dry-run identity comparison.
- Calling `kanban dispatch` from the synchronizer.
- Marking source-closed work done without preserving execution evidence.
- Assuming one provider's labels, dependency API, or work-item semantics apply to
  another tracker.
