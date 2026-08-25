---
name: kanban-reviewer-contract
description: Define bounded, read-only Kanban review work.
version: 0.1.0
author: Karsten Samaschke, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [kanban, reviewer, review, evidence, read-only]
    related_skills: [kanban-factory-operations, kanban-progress-evidence, scoped-subagent-audits]
---

# Kanban Reviewer Contract Skill

Use this skill to create or operate an independent Kanban review. A reviewer
checks an implementation or a declared artifact; it does not implement fixes,
operate the source tracker, deploy infrastructure, or decide that missing
evidence is approval. The review packet is a separate contract from the
implementation card.

## When to Use

Use when:

- an implementation task moves to review;
- a security, reliability, test-adequacy, or release-readiness review is needed;
- a previous review timed out, crashed, or returned an unverifiable claim;
- a factory needs a completion verdict backed by exact evidence.

Do not use this skill to implement a review protocol, file tracker issues, merge
code, or perform a deployment. Those are separate implementation, orchestrator,
or release tasks.

## Role split

Keep these responsibilities distinct:

- **Implementer:** changes code in an isolated worktree and requests review.
- **Code reviewer:** independently inspects and exercises the candidate, read-only.
- **Completion verifier:** checks that required review coverage and board evidence
  exist; it does not replace the code review.
- **Orchestrator/tracker operator:** decomposes work, creates continuations,
  adjudicates findings, files tracker issues, and routes rework.
- **Release operator:** performs only the project-policy-approved release or
  deployment action.

A profile name does not establish a role. The task packet must state the role.
If a configured profile named `reviewer` is used for adversarial code review,
apply the code-reviewer contract below rather than relying on the profile name.

## Review packet

Do not dispatch a review until the packet contains every field below:

- implementation task ID and source issue/PR, if any;
- target repository, worktree path, branch, and candidate commit;
- implementer profile and reviewer profile, with vendor-family comparison;
- exact file paths to inspect; no directories, globs, or topic labels;
- one review lens and the acceptance questions it must answer;
- original acceptance criteria and focused commands to run;
- explicit non-goals and live-system boundaries;
- `read_only_source: true`;
- `max_runtime_seconds: 600` for every adversarial code-review leaf;
- `max_retries: 1`;
- stop condition and required evidence format;
- environment provenance: profile-scoped runtime, effective `cwd`, interpreter,
  required command paths/versions, dependency activation, and preflight result.

A review card must not inherit an implementation prompt unchanged. Reject the
packet if it contains implementation language such as "TDD first", "write the
fix", or "make the production change", or if it asks the reviewer to create or
edit tracker issues. The orchestrator creates a fresh review task with the
review packet.

## Profile environment preflight

Hermes profile isolation is a runtime boundary. The controller's interpreter,
`HERMES_HOME`, skills, toolsets, `cwd`, credentials, and dependency cache do not
necessarily propagate to the worker profile. Run the smallest project-declared
probe through the same profile/runtime that will execute the review.

Verify the profile identity, worktree/commit, effective `cwd`, interpreter and
version, required command paths/versions, dependency activation, and external
capability names without secrets. A controller-side passing test is supplementary
only; it cannot satisfy reviewer-side preflight.

Missing commands, packages, interpreters, skills, tools, or target paths are a
capability gap and produce `REVIEW-INCOMPLETE`, not a product finding. The
orchestrator repairs the profile/project environment or creates a narrower
continuation. A timeout, crash, or provider failure follows the same incomplete
path. After changing the profile environment, run preflight again instead of
retrying the unchanged prompt. See `docs/profile-environment-contract.md`.

## Mutation boundary

The reviewer may:

- read the named worktree, diff, task context, and project instructions;
- run focused tests, linters, renderers, fake services, or read-only probes;
- write scratch harnesses outside the source worktree when needed;
- emit heartbeats and one structured review report;
- use the worker protocol's single terminal transition for its own review run.

Scratch files and test caches are not source changes. Verify the worktree status
before the verdict and report any unexpected change.

The reviewer must not:

- edit source, tests, project configuration, or documentation in the candidate;
- commit, push, merge, rebase, or change branches;
- create, edit, label, close, or comment on tracker issues or pull requests;
- create child Kanban tasks, reassign work, unblock unrelated cards, or dispatch workers;
- deploy, mutate a cluster, rotate credentials, or change live infrastructure;
- expand the review to another repository or runtime layer without a new packet.

A reviewer may append its report to its own review record when the worker
protocol requires it. Tracker mutations and child-task creation belong to the
orchestrator/tracker lane, never inside the review.

## Procedure

1. **Validate the packet.** Check exact paths, candidate commit, profiles,
   read-only boundary, lens, commands, cap, retry limit, and stop condition.
   Completion criterion: every required field is present and no prohibited
   mutation is requested.
2. **Preflight the target.** Confirm the worktree, branch, candidate commit,
   project instructions, and named files. If the target is missing or the
   profile cannot resolve its required review capability, return
   `REVIEW-INCOMPLETE`; do not improvise a repository-wide search.
3. **Read the artifact cold.** Inspect the diff and acceptance criteria before
   exploring adjacent code. Keep the working set inside the packet.
4. **Run the focused checks.** Exercise the declared behavior and error paths.
   Prefer real, unmocked or state-asserting probes when the acceptance claim is
   about resulting state. Record command, exit code, fixture, and result.
5. **Stop at the boundary.** At roughly 70% of the time budget, stop opening
   new files and return the evidence collected. A broader question becomes a
   continuation or a new review slice, not an excuse to overrun the cap.
6. **Check mutation and provenance.** Verify the candidate commit, changed-file
   set, worktree status, and that scratch artifacts stayed outside the source.
7. **Emit one verdict.** Use exactly one terminal outcome for the review run.
   Do not call multiple competing terminal actions after the run is already
   terminal.

## Verdicts

Use only these outcomes:

- `APPROVED` — every required question is answered with sufficient evidence,
  the exact scope is covered, and no contract violation occurred;
- `CHANGES_REQUESTED` — a reproducible defect or acceptance gap exists; record
  file/line, evidence, impact, and the smallest required correction;
- `REVIEW-INCOMPLETE` — timeout, crash, spawn failure, wrong target, missing
  capability, provider failure, scope violation, or missing evidence prevented
  a valid verdict.

A timeout or crash is never a finding, approval, or clean result. A reviewer
that writes source or tracker state has violated the contract; its result is
`REVIEW-INCOMPLETE` even if it also reports a plausible finding.

For Kanban lifecycle calls, use the worker's review transition exactly once:
`kanban_complete` only for `APPROVED`, `kanban_request_changes` only for
`CHANGES_REQUESTED`, and `kanban_block` only for a genuine external or human
blocker. A review defect owned by the implementer is not a human blocker.

## Lifecycle qualifiers

The three leaf verdicts are `APPROVED`, `CHANGES_REQUESTED`, and
`REVIEW-INCOMPLETE`. `PRELIMINARY` is only a non-terminal evidence qualifier
for same-family, unknown-family, or partial coverage; it maps to
`REVIEW-INCOMPLETE` when an independent final review is required. `BLOCKED` is
an orchestrator/board state for a genuine external or human decision, not a
reviewer verdict. A reviewer records incomplete evidence and the orchestrator
owns any board block or continuation.
Return a compact structured report containing:

- `implementation_task` and `candidate_commit`;
- `reviewer_profile` and `implementer_profile`;
- `scope_checked` with exact paths;
- `acceptance_coverage` for every criterion;
- `checks` with commands, exit codes, and evidence;
- `findings` with severity, file/line, reproduction, and impact;
- `gaps` and `mutations`;
- `environment_provenance` separating worker-side checks from parent-side supplementary checks;
- `verdict` exactly as defined above;
- `next_action`, limited to rework, continuation, fan-in, or approval.

The parent/orchestrator independently reads the diff, task run, and decisive
evidence before treating the report as true.

## Continuations and fan-in

A timed-out leaf is preserved as `REVIEW-INCOMPLETE`. Do not retry the same
prompt unchanged. The orchestrator creates a narrower continuation with a
strict subset of the previous scope and carries prior evidence forward.

When several leaves are required, a bounded fan-in task checks coverage of the
declared review manifest. It must not rescan the repository. Any uncovered
file, incomplete leaf, changes-requested leaf, or contract violation blocks
final approval.

Do not combine code review with source-tracker filing. If a finding should become
an issue, the orchestrator creates a separate tracker task after adjudicating
the finding. If a review protocol itself needs implementation, route that work
to an implementer and review the resulting code separately.

## Profile and capacity rules

Before dispatch:

- verify the exact reviewer profile and its resolved skills/tools;
- verify the reviewer is independent from the implementer vendor family;
- use the observed per-profile concurrency cap, not an optimistic default;
- keep the leaf at 600 seconds and `max_retries=1`;
- do not infer read-only capability from a skill name; skills do not provision
  tools or revoke terminal/file write capability.

If the runtime cannot enforce read-only tools, enforce the boundary through the
packet, source-status readback, worker lifecycle, and parent verification. A
profile description alone is not a security boundary.

The shared contract defines reviewer mechanics and safety invariants. If
factories on the same host need different tools, permissions, memory, or
routing, create custom profiles and map them in project policy. Do not weaken or
fork the shared reviewer lifecycle to fit one product.

Project-specific behavior belongs in a project policy file, review packet, or
external factory add-on. Shared skills and Hermes core are not extension points
for one product's review protocol.

## Pitfalls

- Reassigning an implementation card to a reviewer without replacing its body.
- Asking a reviewer to file tracker findings while it is reviewing.
- Calling a 1,800-second broad review "bounded" because it eventually finished.
- Treating a reviewer summary, heartbeat, or successful test command as approval.
- Letting a reviewer expand from one repository to a private overlay or live
  cluster without a new packet.
- Retrying a crashed or timed-out review with the same scope and prompt.
- Treating a same-family reviewer as independent without recording the exception.

## Verification

The review contract is satisfied only when:

- the packet is complete and read back from the Kanban task;
- the reviewer reached a terminal outcome exactly once;
- exact scope and candidate commit are recorded;
- source and live-system mutation checks are clean;
- every acceptance criterion has evidence or an explicit gap;
- incomplete slices remain gated and no downstream work was released;
- any tracker or implementation mutation is owned by a separate task.
