# Factory delivery lifecycle

A worktree is not delivery; it is an implementation workspace, not a delivered
change. The factory
must preserve these states as separate durable transitions:

1. **Implementation complete** — the implementer produced a scoped candidate in
   an isolated worktree and recorded its tests and candidate revision.
2. **Review complete** — the independent reviewer answered the exact packet and
   returned `APPROVED`. `REVIEW-INCOMPLETE` and `CHANGES_REQUESTED` do not pass
   this gate.
3. **Integration complete** — an integration owner created or verified the
   pull request in the declared source repository, checked base/head and the
   changed-file set, obtained the required host review and CI results, merged
   according to repository policy, and read back the merged commit.
4. **Deployment complete** — a release/deployment owner consumed the merged
   commit, published the approved artifact or GitOps change through the declared
   controller, and performs post-action verification of the controller/data-plane
   state. If deployment policy is
   unspecified, this transition is held; it is not inferred from a green test.

No state implies a later state. Completion of one state does not imply the next.
In particular:

- a successful worker handoff is not implementation integration;
- an `APPROVED` Kanban review verdict is not a pull-request review or merge;
- a pushed branch is not a merged pull request;
- a built artifact or desired-state commit is not deployment;
- a local or worker summary is not read-back evidence.

## Required handoff graph

```text
implementation
    -> independent review leaf/fan-in
    -> completion verification
    -> integration operator
         -> pull request exists in the declared repository
         -> required host review and CI are complete
         -> merge is performed and the merged commit is read back
    -> release operator
         -> artifact/GitOps publication is verified
         -> controller/data-plane post-action state is verified
```

Each arrow is a real Kanban dependency or an explicitly recorded external
handle. All worker stages are Kanban tasks assigned to Hermes profiles; this
factory contract does not route implementation, review, integration, release,
or deployment through external coding CLIs. Prose such as “ready for merge” is
not a dependency gate.

## Integration operator

The integration operator owns the source-control boundary after completion
verification. The task packet names the source repository, base branch, head
branch or candidate commit, expected changed files, required host reviewers and
checks, merge policy, and rollback reference. The operator:

- creates or locates the pull request in the declared repository;
- reads back its base, head, revision, title, body, and changed files;
- waits for required host review and CI rather than treating Kanban review as a
  substitute;
- merges only when the project policy permits it;
- reads back the merged commit and records the exact external handle.

If the repository or credentials are unavailable, integration is incomplete.
Do not mark the implementation or source issue closed to hide that gap.

## Release/deployment operator

The release operator is separate from integration. Its task names the merged
revision, artifact or GitOps repository, deployment controller, rollout gate,
rollback path, and post-action evidence. A project may intentionally stop after
merge, but that decision must be explicit in project policy; it must not be
silently represented as deployment complete.

The release operator must not mutate production when deployment mode is
`unspecified` or forbidden. It records the policy hold and returns an honest
incomplete/blocked state with the next gate. When deployment is authorized, it
verifies both the publication handle and the real controller/data-plane state.

## Deterministic Forgejo delivery observation

`scripts/forgejo_delivery_controller.py` is the portable, read-only observation
layer between source delivery and the one project supervisor. Copy
`examples/forgejo-delivery-overlay.json`, replace its placeholders with
project-owned non-secret values, and invoke the controller through a thin
project wrapper:

```text
python3 scripts/forgejo_delivery_controller.py --config <project-overlay.json>
```

The overlay owns the Forgejo API base and repository, Git credential-helper
input and private cache path, source/target branch filters, exact branch
exclusions, exact runner visibility endpoints, required runner labels and CI
contexts, stale thresholds, board/project identity, inventory/output bounds,
and integration, infrastructure-recovery, and release operator profiles. It
does not carry a manually maintained candidate list.

Every HTTP request is a `GET` with explicit non-empty `User-Agent` and `Accept`
headers. Authentication is resolved through `git credential fill`; successful
headers are cached atomically at mode `0600` for non-interactive cron use and
credential values are never logged. Open and closed pull requests, live
repository branches, and every configured runner endpoint are fully paginated
inside explicit inventory bounds. A bound overrun fails closed rather than
silently treating a partial page as complete. Closed pull requests are always
inventoried to suppress branches that already have PR history;
`pull_requests.include_closed` controls only whether their lifecycle records are
included in output.

On every tick the live branch inventory is filtered by `source_prefixes`,
`target_branches`, `excluded_branches`, and the head branch/SHA of every observed
open or closed pull request. A newly pushed matching branch therefore appears as
`pushed/no-PR` on the next tick without an overlay edit. Forgejo's branch payload
normally supplies the head SHA and commit timestamp. When either is unusable,
the controller performs at most `max_branch_commit_lookups` one-item GET queries
to `/repos/{owner}/{repo}/commits`; exceeding that bound fails closed before the
fallback queries begin. The emitted `pushed_at` value is explicitly marked with
`timestamp_semantics: head-commit`: it is bounded branch-age evidence, not a
claim that Forgejo exposed the wall-clock push event.

Runner readiness is calculated only from an exact `repository-visible` scope
whose endpoint resolves to `/repos/{owner}/{repo}/actions/runners` and is queried
with `visible=true`. Organization or instance scopes may be included as
diagnostic observations, but an identically named runner there does not satisfy
repository readiness. Required labels are classified as `active`, `busy`,
`offline`, or `missing`.

For every open pull request the controller reads the detail resource and the
head commit's combined status. Its delivery stages include
`waiting-for-runner`, `waiting-for-CI`, `CI-failed`, and
`ready-for-integration`. Optional closed-history observation preserves `merged`
and `closed` without inferring release or source closure. A missing/offline
required repository-visible runner, failed required CI, or an open pull request
past the configured no-completed-CI threshold produces `STALLED`. Recent
pending work remains `ACTIVE`; a repository with no delivery work is
`IDLE-BY-GATING`.

Review routing is installation policy, not a global framework assumption. The
overlay records the actual implementer/reviewer profile and vendor-family route
and sets `vendor_family_separation` to `required`, `preferred`, or `not_required`.
Only `required` gates delivery when the configured families are equal or
unknown. `preferred` reports an advisory and `not_required` reports the route
without treating same-family review as a defect. The controller never creates
or modifies reviewer profiles and never claims that review itself completed.

The cron job uses `no_agent: true` and `deliver: local`; its job ID is one of the
supervisor's `context_from` inputs. Controller JSON is still only an
observation. After verifying live Forgejo and board state, a supervisor that
confirms `STALLED` creates one exact-scope integration or infrastructure-recovery task,
uses the owner profile named by the overlay, and reads back the task ID, scope,
status, and assignee. It creates no broad duplicate and stops that tick. Merge,
release, issue closure, and deployment remain separate policy-gated operator
actions with their own readback.

## Recovery and reporting

The orchestrator creates and dispatches the integration and release handoffs
when their prerequisites are met. The review recovery add-on only repairs review
packet and successor/fan-in state; it does not create a pull request, merge code,
or deploy infrastructure.

Progress reports name the latest completed state, the current state, the owner
of the next transition, and its evidence. A source issue is closure-ready only
when the required integration and release policy gates are satisfied and read
back. If a downstream owner or task is missing, report a `factory-owned
lifecycle gap`, not “handled” or “idle-by-gating”.
