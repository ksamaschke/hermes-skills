# Reviewer reliability

A reviewer is only useful if the review actually reached the intended target and returned a bounded, evidence-based verdict.

## Fresh review contract

A review card is created from a fresh packet. Do not reuse an implementation card
with a new assignee. The packet must contain the candidate commit, exact file
paths, one acceptance question/lens, focused commands, explicit non-goals,
`read_only_source: true`, the declared runtime budgets (dispatch hard cap,
evidence budget, per-command timeout), one retry, and a stop condition.

Reviewers may not implement fixes, edit the candidate, file or edit tracker
issues, create Kanban children, reassign unrelated work, deploy, or mutate live
infrastructure. If the packet asks for any of those actions, return
`REVIEW-INCOMPLETE: invalid review packet` and do not improvise a broader scope.
The orchestrator owns issue filing, continuations, fan-in, and rework routing.

## Profile environment boundary

The controller shell is not the worker shell. A Hermes profile can resolve a
different `HERMES_HOME`, config, skills, toolsets, `cwd`, interpreter, dependency
manager, credentials, and command path. Before dispatch, run the smallest
project-declared environment probe through the exact reviewer profile and
worktree. Record interpreter/version, required commands, dependency activation,
and non-secret capability names.

A controller-side passing test does not satisfy reviewer-side evidence. Missing
pytest, cargo, pnpm, a project virtual environment, a skill, a tool, or a target
path is a capability gap and yields `REVIEW-INCOMPLETE`, not a product finding.
Repair the profile/project environment or create a narrower continuation; do not
retry the same prompt unchanged. See `docs/profile-environment-contract.md`.

## Required preflight

Before reading the diff, the reviewer must:

1. print or otherwise verify the absolute target repository/worktree path;
2. verify it is a Git worktree and record the current commit/branch;
3. verify the named files exist;
4. confirm read-only intent;
5. use a bounded model turn/runtime budget;
6. use a minimal reviewer profile/toolset rather than inheriting unrelated interactive skills;
7. use non-interactive terminal settings (`CI=1`, `TERM=dumb`) where the runner supports them.

A profile or wrapper must not rely on the controller's outer `cwd` being propagated. Put the target path in the reviewer prompt and explicitly `cd` or use absolute paths.

Before fan-out, verify the exact provider/model route used by the reviewer profile and the effective per-profile concurrency cap. A tiny successful probe proves route reachability only; a representative worker-sized request or concurrent run is required to establish usable capacity. Treat provider `Overloaded` responses as `REVIEW-INCOMPLETE`, not as a product verdict.

## Native UI evidence ownership

A loaded skill does not grant a worker a native desktop-control tool. Before
creating a UI review card, inspect the assigned worker's actual tool schema;
`computer_use` must be present, not merely mentioned in the prompt or attached
as a skill.

If the reviewer profile cannot drive the desktop, HEX/the orchestrator owns the
native capture and input. It records the exact app/window, process and build
provenance, fixture hashes, screenshots, and live protocol checks, then attaches
those artifacts to a read-only reviewer card. That reviewer validates the
artifacts and acceptance mapping without attempting native input itself.

macOS TCC permission dialogs are a human-only boundary. Do not make a worker
click them or classify their absence as a product finding. If capture is not
available, record `REVIEW-INCOMPLETE` with the capability gap and keep the
implementation card separate from the UI evidence card.

Before creating a leaf, run `hermes -p <reviewer-profile> skills list` and
verify that every forced skill is resolvable for that profile. Create the leaf
with `max_runtime_seconds` set to the declared dispatch hard cap and
`max_retries=1`. A timeout must not trigger
an unchanged automatic retry; recovery creates a new continuation card after
preserving the partial evidence.

## Review slice budget

An adversarial code review is a **change-set review**, not a full-repository
forensic mission. Every review is exactly one of two kinds — `pre_commit` (the
working-tree change set an implementer proposes to commit) or `pre_merge` (the
merge-base delta a pull request would introduce). Scope is the changed hunks,
never a module, directory, or repository. See
[`change-scoped-review.md`](change-scoped-review.md).

Reviews carry two distinct limits, because a single wall clock covering model
latency, provider backoff, and command execution produces kills instead of
verdicts. A project declares them in policy; the reference values are:

- `dispatch_hard_cap_seconds: 1800` — the dispatcher's `--max-runtime`
  SIGTERM/SIGKILL backstop against a hung worker. Not a target, not a budget.
- `evidence_budget_seconds: 900` — the reviewer's own mandatory return point.
  The reviewer tracks elapsed time and **emits a verdict at or before this
  point** with whatever evidence it holds, listing the rest under `gaps`.
- `command_timeout_seconds: 120` — hard cap on any single command.

Returning a verdict at the evidence budget is correct behavior. Being killed at
the dispatch hard cap is an anomaly indicating a broken reviewer, a broken
route, or an invalid packet; it is always `REVIEW-INCOMPLETE`.

Checkpoints inside the evidence budget: at 50% stop opening new context, at 70%
stop starting new commands, at 100% emit the verdict and stop.

A reviewer runs **diff-targeted checks only**. The full project gate —
`make test`, `make validate`, a full suite or build — is the implementer's and
CI's evidence, cited in the packet with command, exit code, run reference, and
the commit it ran against. The reviewer confirms that evidence corresponds to
the candidate commit; it never re-runs the gate. Missing, stale, or
unverifiable gate evidence is a `CHANGES_REQUESTED` finding, not a licence to
run the gate.

Split broad reviews into independent cards before dispatch. Typical slices are:

- transport/bootstrap/token security;
- frontend transport parity and UI call sites;
- packaging, sidecar lifecycle, and regression tests.

Each slice names its change manifest, question, focused checks, non-goals, and
verdict format. Require a heartbeat at the end of each major phase.

If the dispatch hard cap is reached, the review is incomplete. Diagnose the
cause before narrowing: provider backoff and forbidden full-gate commands are
budget and execution-boundary faults, and narrowing scope will not fix them.

## Hierarchical chunking

Slice by acceptance question and control-flow path, then split again by size.
Do not stop at three broad architecture categories when one category still
crosses frontend, native bootstrap, security, and packaging boundaries.

Before dispatch, build a review manifest. Every leaf chunk contains:

- one acceptance question or one tightly coupled path;
- no more than five primary production files, plus directly referenced tests or
  configuration;
- focused checks only, with an explicit non-goal list;
- the declared runtime budgets and a compact evidence format.

If a proposed chunk crosses two runtime layers, needs more than five primary
files, or contains more than one independent verdict question, split it before
dispatch. A typical #234 review therefore becomes separate chunks for transport
selection, frontend HTTP parity, bootstrap/token handoff, window/origin
capability handling, and packaging/sidecar lifecycle.

After all leaf chunks finish, create one bounded fan-in/synthesis task. It reads
the child reports and acceptance matrix, resolves contradictions, and lists
unverified gaps; it does not repeat a repository-wide scan. A finding reruns
only the affected leaf chunk after the implementation fix.

## Verdict rules

- A final report with evidence may produce `APPROVED` or `CHANGES_REQUESTED`.
- Target-not-found, wrong-cwd, terminal-clear failure, tool timeout, iteration exhaustion, missing credentials, process crash, same-family review, unknown-family review, or partial coverage produces `REVIEW-INCOMPLETE`.
- `REVIEW-INCOMPLETE` is never approval and must not advance a code card.
- `BLOCKED` is an orchestrator/board state for a genuine external or human decision, not a reviewer verdict.

## Recovery

When a review is incomplete:

1. inspect the worker/reviewer process and logs;
2. verify no source files were modified;
3. preserve the timeout as `REVIEW-INCOMPLETE`, never as a finding or approval;
4. do not retry the same broad prompt;
5. classify the cause first: provider backoff or a forbidden full-gate command
   is a budget/execution fault, so re-dispatch the same slice after fixing the
   packet or route instead of narrowing it. Narrow only for genuine change-set
   size, and link the continuation to the incomplete card;
6. if a focused slice still times out, split that slice into leaf chunks at the
   last verified boundary and report the exact missing evidence;
7. do not leave the implementation card blocked merely because an internal review worker needed decomposition.

If the failure is backend overload, reduce the effective reviewer cap through the supervised dispatcher owner, verify the running gateway loaded the new value, and queue excess review cards. Do not requeue a product task merely to lower the blocked count.

When the reviewer returns findings, the orchestrator verifies the cited files/lines, routes concrete changes to an implementer, and reruns the reviewer after the fix.

## Review handoff

The final handoff must include:

- target path and commit reviewed;
- files inspected;
- tests/checks run and exit status;
- findings with severity and file/line evidence;
- verdict;
- limitations or unverified acceptance criteria.

Worker summaries without this evidence remain untrusted handoffs.
