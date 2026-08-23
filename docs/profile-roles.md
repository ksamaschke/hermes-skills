# Profile roles

Hermes profiles are reusable role identities. A Kanban board chooses a profile through the task's `assignee`; the profile is not manually pointed at a repository.

## Orchestrator

An orchestrator profile owns decomposition and routing:

- reads project policy and board state;
- creates small child cards with real dependencies;
- chooses implementer/reviewer profiles and model tiers;
- manages WIP and human decisions;
- never edits source code.

The Hermes gateway dispatcher still owns mechanical promotion, claims, worktrees, heartbeats, retries, and crash recovery.

## Implementer

The implementer profile is write-capable and works in an isolated worktree:

- writes a failing test first;
- implements the smallest scoped change;
- runs the project gate;
- records exact evidence;
- hands the result to review.

One generic implementer profile can serve multiple boards. Create separate implementer profiles only when tools, credentials, memory, safety policy, or write permissions differ. Model strength can usually be selected with a task-level override.

## Reviewer

The reviewer is separate from the implementer and should use an independent model/vendor family. It is read-only by default:

- reads the original issue and implementation diff;
- verifies tests and edge cases;
- checks security, scope, and deployment policy;
- returns approval, concrete changes, or a genuine blocker.

A same-family or unknown-family review is `PRELIMINARY`, not final sign-off.

## QA/UI

A `qa-ui` profile is optional. Use it for native/browser acceptance that cannot be proven by headless tests. It should not rebuild or launch a desktop shell for every unit test. Keep full-app smoke cards separate and time-boxed.

## Release/GitOps

A `release-operator` profile is optional and project-policy-controlled. It may prepare release artifacts or GitOps changes. It must not bypass the project's declared deployment controller or mutate production directly when policy forbids that.
