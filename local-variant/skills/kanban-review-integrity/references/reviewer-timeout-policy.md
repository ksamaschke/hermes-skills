# Reviewer timeout policy

## Durable lesson

Reviewer work is not repository-wide work. The review gate dispatches a fresh,
read-only packet whose scope is a **change manifest** — base reference,
candidate reference, and changed paths with hunk ranges — for exactly one review
kind: `pre_commit` (working-tree delta against the base commit) or `pre_merge`
(merge-base delta against the target branch). It carries one acceptance
question/lens, diff-targeted checks, cited implementer/CI gate evidence,
explicit non-goals, and a stop condition. The reviewer must not implement, edit
tracker state, create child tasks, merge, or deploy.

Runtime limits are **operator-approved project policy**, not values a skill or
agent invents. The approved model for this factory is two-tier:

- `dispatch_hard_cap_seconds: 1800` — the dispatcher's `--max-runtime`
  SIGTERM/SIGKILL backstop against a hung worker. Not a target, not a budget.
- `evidence_budget_seconds: 900` — the reviewer's own mandatory return point. It
  tracks elapsed time and emits a verdict at or before this point with whatever
  evidence it holds, listing the rest under `gaps`.
- `command_timeout_seconds: 120` — hard cap on any single command.

The dispatcher backstop and the reviewer's evidence budget are separate
concepts. Collapsing them into one wall clock is the defect this policy exists
to prevent: a single clock covering model latency, provider backoff, and command
execution produces kills instead of verdicts.

Returning a verdict at the evidence budget is correct behavior. Being killed at
the dispatch hard cap is an anomaly — a broken reviewer, a broken route, or an
invalid packet — and is always `REVIEW-INCOMPLETE`.

## Execution boundary

A change-scoped reviewer runs diff-targeted checks only. It must never run the
full project gate (`make test`, `make validate`, a full suite or build). That
evidence belongs to the implementer and CI and is cited in the packet with
command, exit code, run reference, and commit. Missing or stale gate evidence is
a `CHANGES_REQUESTED` finding, not a licence to run the gate.

## Failure patterns this prevents

**Single-tier cap.** A reviewer took roughly ten minutes while a single
600-second cap expired just after its focused test command completed. Raising
the cap to 1200s and shrinking scope were both wrong answers; the fix was to
separate the dispatcher backstop from a reviewer-owned evidence budget so the
reviewer returns a verdict instead of being killed.

**Gate inside the review.** One leaf spent 420s on `make test` plus 188s on a
second gate — 675s of command time against a 600s cap. The gate evidence already
existed and should have been cited.

**Narrowing a budget fault.** A depth-3 continuation scoped to a single file and
a single acceptance question was still killed at 602s, after opening with an
HTTP 429 `model_cooldown`. Scope was never the problem. Classify the cause
before choosing a remedy: provider backoff, a forbidden gate command, or an
invalid packet means fix the packet or route and re-dispatch the same slice.
Narrow only for genuine change-set size.

## Verification checklist

- Review packet declares `review_kind` and a change manifest, never a whole
  file, directory, module, or repository scope.
- Packet carries the dispatch hard cap, evidence budget, and per-command
  timeout; `--max-runtime` on the board row equals the dispatch hard cap.
- No full-gate command appears in the checks; gate evidence is cited with a run
  reference matching the candidate commit.
- Any runtime limit is traceable to explicit project/backend configuration, and
  no skill or script invents one.
- Timeout remains `REVIEW-INCOMPLETE`, not approval or a product finding.
- A continuation chain still timing out at shrinking scope is escalated as a
  factory fault rather than narrowed again.
- Successors are strict subsets with new idempotency keys and preserved evidence.
- Whole-repository review is rejected rather than made longer.
