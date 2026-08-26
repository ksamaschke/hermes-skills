# Reusable reviewer role contract

This document defines the reviewer role for the shared Software Factory. It is
project-agnostic. A project may provide an adapter or overlay, but it must not
rewrite this role contract to smuggle implementation, tracker, or deployment
work into a review card.

## Role boundaries

The factory has separate responsibilities:

- **Implementer:** changes code in an isolated worktree and requests review.
- **Code reviewer:** independently checks a candidate and returns one evidence-
  backed verdict. It is read-only with respect to the candidate.
- **Completion verifier:** checks that review coverage, gates, and board evidence
  exist. It does not replace code review.
- **Orchestrator/tracker operator:** is the operating and architecture authority
  within project policy; decomposes work, creates continuations, adjudicates
  findings, files tracker issues, chooses recovery, and routes rework. It does
  not implement source changes or bypass the independent review contract.
- **Release operator:** performs only project-policy-approved release actions.

A profile name is not a permission boundary. A profile called `reviewer` still
needs a typed review packet and parent-side verification.

## Review packet

A review card is created fresh from a packet. It is not an implementation card
with a new assignee. The packet contains:

- implementation task and source issue/PR identity;
- target repository, worktree, branch, and candidate commit;
- implementer and reviewer profiles plus vendor-family comparison;
- exact file paths, with no directory or glob scopes;
- one acceptance question and one review lens;
- original acceptance criteria and focused commands;
- explicit non-goals and live-system boundaries;
- `read_only_source: true`;
- `max_runtime_seconds: 600` for each adversarial code-review leaf;
- `max_retries: 1`;
- a stop condition and structured report format;
- environment provenance: profile-scoped runtime, effective `cwd`, interpreter,
  command paths/versions, dependency activation, and preflight result.

The controller's passing test is supplementary; the reviewer must prove the
same check in its own profile/worktree environment. Missing commands, packages,
interpreters, skills, tools, or target paths are `REVIEW-INCOMPLETE` capability
gaps owned by the factory, not product findings.

Reject a packet containing `TDD first`, implementation instructions, tracker
issue creation, child-task creation, deployment, or an unrelated repository.
Route those actions to their owning role.

## Allowed reviewer work

A reviewer may read the named candidate, inspect the diff, run focused checks,
exercise fake or read-only services, and write scratch harnesses outside the
source worktree. It may emit heartbeats and use the worker protocol's single
terminal transition for its own review run.

The reviewer may not edit source, tests, project configuration, or documentation
in the candidate; commit, push, merge, or rebase; create or edit tracker issues;
create Kanban children; reassign unrelated work; deploy; rotate credentials; or
mutate live infrastructure.

If the runtime cannot enforce read-only terminal/file tools, the packet, profile
prompt, source-status readback, and parent verification are mandatory. A skill
name or profile description alone is not a security boundary.

## Verdicts

Use exactly one terminal outcome:

- `APPROVED`: every criterion and exact scope is covered by evidence;
- `CHANGES_REQUESTED`: a reproducible defect or acceptance gap is recorded with
  file/line evidence and impact;
- `REVIEW-INCOMPLETE`: timeout, crash, wrong target, missing capability, scope
  violation, mutation, or missing evidence.

A reviewer does not emit `BLOCKED`; the orchestrator may place a parent card in
blocked state for a genuine external or human decision. `PRELIMINARY` is a
non-terminal evidence qualifier and maps to `REVIEW-INCOMPLETE` when an
independent final verdict is required.

An incomplete review is never approval. It does not become a product finding
merely because the worker produced a plausible partial report.

A reviewer uses the lifecycle transition matching its verdict exactly. The
orchestrator creates narrower continuations, files issues, and routes fixes.
A reviewer must not call a competing terminal transition after its run is
already terminal.

## Scope and recovery

A leaf reviews no more than five primary production files plus directly
referenced tests/configuration, and answers one acceptance question. At roughly
70% of the budget it stops opening new files. Broad work is split before
execution. A timed-out leaf is preserved as `REVIEW-INCOMPLETE` and replaced by
a narrower continuation with a strict subset of the previous scope. An unchanged
retry is not recovery.

Several leaves are reconciled by a bounded fan-in task that reads their reports
and coverage matrix. Fan-in does not rescan the repository and does not grant
approval to an incomplete or uncovered leaf.

## Profile guidance

A project may include this policy in a reviewer profile's `SOUL.md`, but that
profile text must remain project-agnostic. It should describe the role boundary,
packet requirements, allowed terminal transition, and incomplete semantics. It
must not contain repository paths, tracker hosts, project board slugs, model
IDs, credentials, or project-specific commands.

Project-specific behavior belongs in a project policy file, review packet, or
external factory add-on. Shared skills and Hermes core are not extension points
for one product's review protocol.
