# Profile roles

Hermes profiles are reusable role identities. A Kanban board chooses a profile through the task's `assignee`; the profile is not manually pointed at a repository.

## Orchestrator

An orchestrator profile owns decomposition and routing:

- reads project policy and board state;
- creates small child cards with real dependencies;
- chooses implementer/code-reviewer profiles and model tiers;
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

## Code reviewer

The code reviewer is separate from the implementer and should use an independent model/vendor family. It receives a fresh typed review packet, not an implementation card with a new assignee.

The packet names the candidate commit, exact file paths, one acceptance question/lens, focused checks, explicit non-goals, `read_only_source: true`, `max_runtime_seconds: 600`, `max_retries: 1`, and a stop condition. The reviewer may read and test the candidate and write scratch evidence outside the source worktree.

The reviewer does not implement fixes, edit source, file tracker issues, create Kanban children, reassign unrelated work, merge, push, deploy, or mutate live infrastructure. A timeout, crash, wrong target, scope violation, mutation, or missing evidence is `REVIEW-INCOMPLETE`, never approval.

A same-family or unknown-family review is `REVIEW-INCOMPLETE` until an independent
final review exists. `PRELIMINARY` may describe the evidence qualifier but is
not a terminal verdict. See [`docs/reviewer-role-contract.md`](reviewer-role-contract.md).

## Completion verifier and tracker operator

A completion verifier checks review coverage, acceptance evidence, and board transitions; it does not replace the code reviewer. An orchestrator/tracker operator creates continuations, adjudicates findings, files source-tracker issues, and routes rework. These actions are separate from the review leaf.

## QA/UI

A `qa-ui` profile is optional. Use it for native/browser acceptance that cannot be proven by headless tests. It should not rebuild or launch a desktop shell for every unit test. Keep full-app smoke cards separate and time-boxed.

## Release/GitOps

A `release-operator` profile is optional and project-policy-controlled. It may prepare release artifacts or GitOps changes. It must not bypass the project's declared deployment controller or mutate production directly when policy forbids that.
