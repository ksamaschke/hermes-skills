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
