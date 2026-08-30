# Change-scoped review

An adversarial review in this factory reviews a **change set**. It never reviews
a repository. This document defines the only two review kinds the factory
dispatches, how their scope is derived, and the runtime budget that makes them
terminate with a verdict instead of being killed by the dispatcher.

It is project-agnostic. Projects supply values through project policy; they do
not redefine the kinds, the scope rule, or the budget model.

## Why this exists

Observed failure mode, recorded from a live factory board: adversarial reviews
were dispatched with "exact file paths" as their scope and a single 600-second
wall clock. They were killed at 602s, 608s, and 610s. One killed review had been
narrowed to a **single file and a single acceptance question** and still died,
because scope narrowing does not bound the two things that actually consumed the
clock:

- whole-project gate commands run inside the review (one review spent 420s on
  `make test` and a further 188s on a second gate, 675s of command time against
  a 600s cap);
- provider backoff and model latency drawn from the same clock as command
  execution.

Narrowing scope cannot fix a budget defect, and a file list cannot bound a
review that is allowed to run the project gate. The two review kinds below fix
the scope rule; the two-tier budget fixes the clock.

## The only two review kinds

Every adversarial code review is exactly one of these. A review that is neither
is an invalid packet.

### `pre_commit`

Reviews the change set that an implementer proposes to commit, before the commit
is made or accepted.

- **Scope basis:** the working-tree delta against the candidate's base commit —
  `git diff <base>` plus `git diff --cached` plus declared untracked files.
- **Question:** is this change set correct, safe, and adequately tested, on its
  own terms?
- **Base:** the commit the implementer started from, named explicitly in the
  packet.

### `pre_merge`

Reviews the change set a pull/merge request would introduce into the target
branch, before merge.

- **Scope basis:** the merge-base delta — `git diff $(git merge-base <target>
  <source>)..<source>`.
- **Question:** is this change set safe to integrate into the target branch?
- **Base:** the merge base with the target branch, named explicitly in the
  packet.

Both kinds are read-only with respect to the candidate and both use the review
packet, mutation boundary, and verdict set defined in
[`reviewer-role-contract.md`](reviewer-role-contract.md).

## Scope rule: the diff is the boundary

The reviewed unit is the set of changed hunks. Not the changed files in full,
not the modules that contain them, not the repository.

A review packet declares scope as a **change manifest**: the base reference, the
candidate reference, and the enumerated changed paths with their hunk ranges. A
packet whose scope is a directory, a glob, a topic label, a module name, or
"the repository" is invalid; return `REVIEW-INCOMPLETE: invalid review packet`.

Reading outside the diff is permitted only as *context for a specific changed
hunk* — the definition a changed line calls, the test that covers it, the
declaration it overrides. That reading is bounded:

- it must be traceable to a named hunk in the manifest;
- it is read, not re-reviewed; unchanged code is not in the finding surface;
- a defect found only in unchanged code is a `gaps` note for the orchestrator,
  not a finding against this change set.

There is no review kind that legitimately grows into a whole-codebase audit. If
a change set genuinely requires whole-codebase judgement, that is an
architecture-review task the orchestrator files separately, with its own budget
and its own card. It is never an escalation of a commit or merge review.

## Runtime budget: two tiers

A single wall clock covering model latency, provider backoff, and command
execution produces kills instead of verdicts. Reviews therefore carry two
distinct limits.

```yaml
review:
  dispatch_hard_cap_seconds: 1800   # dispatcher SIGTERM/SIGKILL boundary
  evidence_budget_seconds: 900      # reviewer MUST return a verdict by this point
  command_timeout_seconds: 120      # hard cap on any single command
```

- **`dispatch_hard_cap_seconds: 1800`** is the dispatcher's enforcement
  boundary, set with `--max-runtime`. It is a backstop against a hung worker. It
  is not a target and not a budget.
- **`evidence_budget_seconds: 900`** is the reviewer's own deadline. The
  reviewer records its start time, tracks elapsed time, and **returns a verdict
  at or before 900 seconds** with whatever evidence it has, listing everything
  unchecked under `gaps`.
- **`command_timeout_seconds: 120`** caps any single command. A command that
  needs longer is out of scope for a change-scoped review.

The 900-second point is a **mandatory return**, not advice. Reaching it and
returning `CHANGES_REQUESTED` or `REVIEW-INCOMPLETE` with recorded gaps is
correct behavior. Being killed at the 1800-second cap is an anomaly that
indicates a broken reviewer, a broken route, or an invalid packet — and it is
always `REVIEW-INCOMPLETE`.

Checkpoints inside the evidence budget:

- **at 50% (450s):** stop opening new context; begin consolidating evidence;
- **at 70% (630s):** stop starting new commands; only finish in-flight work;
- **at 100% (900s):** emit the verdict and stop.

Provider backoff counts against the evidence budget. A review that spends its
budget on `429`/cooldown retries returns `REVIEW-INCOMPLETE` naming the provider
condition — that is an infrastructure fault, never a product verdict and never
approval.

## Execution boundary: what a reviewer may run

A change-scoped reviewer runs **diff-targeted checks only**.

Permitted:

- tests that cover the changed hunks, selected by node id, file, or pattern;
- linters, type checks, or formatters scoped to the changed paths;
- focused probes and scratch harnesses written outside the source worktree;
- read-only inspection commands (`git diff`, `git status`, `git log`).

Forbidden inside a review:

- the full project gate — `make test`, `make validate`, a full suite run, a full
  build, or any project-wide verification command;
- any command expected to exceed `command_timeout_seconds`;
- anything that mutates the candidate, the tracker, or a live system.

**The full gate is the implementer's and CI's evidence, and the reviewer cites
it — it never re-runs it.** The review packet carries the gate result: the
command, exit code, run reference, and commit it was run against. The reviewer's
job is to check that this evidence exists, that it corresponds to the candidate
commit, and that it is not stale or self-reported without a run reference.

If the gate evidence is missing, stale, or unverifiable, that is a
`CHANGES_REQUESTED` finding against the implementation card — "gate evidence
absent for candidate `<commit>`". It is not a reason for the reviewer to run the
gate itself.

## Oversized change sets

A single leaf reviews at most **5 changed files** and answers **one acceptance
question**. When a commit or PR exceeds that, the orchestrator splits it before
dispatch:

1. group changed hunks into slices by coupled concern — transport, persistence,
   API surface, packaging — each within the 5-file leaf limit;
2. give each slice its own packet, one acceptance question, and its own
   change manifest that is a strict subset of the parent manifest;
3. create one bounded fan-in task that reads **only the leaf reports** and the
   coverage matrix. Fan-in never re-reads the repository and never re-runs
   checks.

Every hunk in the parent change set must appear in exactly one leaf manifest.
The fan-in verifies this coverage. An uncovered hunk, an incomplete leaf, or a
changes-requested leaf blocks approval of the change set.

Fail closed only when a **single hunk** cannot fit a leaf budget: report the
change set as unreviewable and require the implementer to split the commit or
PR. Do not approve a change set whose scope was never fully covered, and do not
raise the budget to accommodate an oversized change.

## Recovery

A killed or incomplete review is preserved as `REVIEW-INCOMPLETE`. Recovery
follows the change-set structure, not a blind narrowing loop:

1. read the log and classify the cause — provider/backoff, a forbidden gate
   command, an invalid packet, or genuine change-set size;
2. if the cause was a forbidden full-gate command or provider backoff, the
   scope was never the problem. **Do not narrow the scope.** Fix the packet or
   the route and re-dispatch the same slice;
3. narrow only when the cause is genuine change-set size, and narrow by
   splitting the change manifest into strict-subset slices;
4. never retry an unchanged prompt;
5. raising the budget is not a recovery step.

A continuation chain that keeps producing timeouts at shrinking scope is
evidence of a budget or execution-boundary defect, not of a change set that
needs further splitting. Escalate it as a factory fault.

## Verification

A change-scoped review is correctly configured only when the packet shows:

- `review_kind` of exactly `pre_commit` or `pre_merge`;
- a change manifest with base reference, candidate reference, and hunk ranges;
- `dispatch_hard_cap_seconds`, `evidence_budget_seconds`, and
  `command_timeout_seconds` present and consistent with project policy;
- `--max-runtime` on the created task equal to the hard cap;
- gate evidence cited from the implementation card, with a run reference;
- no full-gate command in `focused_checks`;
- at most 5 changed files and one acceptance question per leaf.

A review that returned a verdict inside its evidence budget is a healthy review.
A review killed at the dispatcher cap is a factory fault to be diagnosed, never
an approval and never a product finding.
