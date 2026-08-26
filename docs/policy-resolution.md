# Policy resolution

The workflow skill is reusable only when project-specific decisions stay outside the skill body.

## Resolution order

1. Explicit user instruction or standing delegation.
2. Repository rules (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, or equivalent).
3. Project policy, such as `examples/project-policy.yaml` copied into the
   target repository.
4. Kanban/task-specific constraints and comments.
5. Safe defaults.
6. Operator clarification only for a genuinely non-delegable decision.

The order supplies intent; it does not remove a declared safety boundary.
Explicit user intent and standing delegation are interpreted within repository
rules, project policy, protected paths, and forbidden mutations.

Within explicit standing delegation and project policy, the orchestrator
chooses and executes the simplest adequate, policy-compliant option. It prefers
reversible actions where practical but does not choose a smaller change at the
expense of a correct result. A missing routine preference is not a reason to
stop or ask.

If a safety-critical value is unspecified, hold only the affected action and
send one central clarification. Continue independent work. This narrow safety
hold is not a routine preference escalation.

`decision_authority.mode: delegated` lets the orchestrator execute decisions in
`delegated_scope`; `approval_required` makes that scope planning-only until the
operator approves the proposed action. The orchestrator still owns the plan,
recommendation, and next gate. Non-delegable decisions and unspecified
safety-critical values require clarification in either mode.

## What belongs in project policy

The Kanban section should also declare whether review is dispatched automatically:

- `review_dispatch: false` when the orchestrator explicitly creates/assigns reviewer cards;
- `review_dispatch: true` only when every review request carries a valid independent reviewer profile.

The same section should declare conservative execution limits and the dispatcher owner (for example, `dispatcher_owner: supervised_gateway`). Set `max_in_progress_per_profile` from observed backend capacity, not from the number of queued cards. A successful single-request model probe does not justify concurrent fan-out.


Project policy should declare:

- tracker project and base branch;
- labels that mean epic, parked, ready, or human decision;
- implementer, code-reviewer, completion-verifier, and orchestrator profile names;
- model tiers and provider routing;
- focused headless test/build/lint commands;
- whether a UI smoke test exists and how it is run;
- deployment mode and the system that owns rollout;
- notification channel and progress-digest schedule;
- protected paths, secrets boundaries, and forbidden mutations.

## Persisted versus effective runtime policy

The project policy file and Hermes config are persisted intent. A supervised gateway may capture dispatcher and review settings at process startup. After changing `max_in_progress`, `max_in_progress_per_profile`, `review_dispatch`, or a profile/model route, the operator must use the supported supervised lifecycle and verify the effective startup values, current gateway PID, worker claims, and heartbeats. A stale supervised PID is not factory health evidence.

## Headless-first is a default

The skill assumes that unit, integration, HTTP, jsdom, and headless-browser tests are cheaper and more reproducible than a full desktop shell. A project may require a real desktop/WebView smoke test for native behavior, but that requirement should be explicit in `verification.ui_smoke` or repository instructions.

Do not launch a desktop shell merely because a task mentions the UI. First identify which acceptance criteria cannot be proven headlessly.

## Deployment is not universal

`deployment.mode: gitops_only` and `deployment.controller: argocd` are valid examples for projects whose live state is owned by Git and Argo CD. Another project may use Flux, a release pipeline, or a human-controlled deployment. The generic skill does not choose.

`release_only` is a policy value for projects where the repository produces artifacts but a separate release system performs rollout. `unspecified` is the safe default: prepare/review only, no production mutation.

## Profile reuse

Use role profiles across boards. A repository name in a profile name is a signal that migration may be needed, not a requirement for isolation. Create a project-specific profile only when credentials, tools, memory, or safety policy must be isolated.

Model strength normally belongs in a task-level override. Separate profiles are justified by behavior or permissions, not just by “Sonnet versus Opus.”
