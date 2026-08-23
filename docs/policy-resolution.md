# Policy resolution

The workflow skill is reusable only when project-specific decisions stay outside the skill body.

## Resolution order

1. Direct user instruction.
2. Repository rules (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules`, or equivalent).
3. A project policy file such as `examples/project-policy.yaml` copied into the target repository.
4. Kanban task fields and comments.
5. Skill defaults.
6. Ask or block if a safety-critical value is still unspecified.

## What belongs in project policy

Project policy should declare:

- Forgejo/GitHub repository and base branch;
- labels that mean epic, parked, ready, or human decision;
- implementer/reviewer/orchestrator profile names;
- model tiers and provider routing;
- focused headless test/build/lint commands;
- whether a UI smoke test exists and how it is run;
- deployment mode and the system that owns rollout;
- notification channel and progress-digest schedule;
- protected paths, secrets boundaries, and forbidden mutations.

## Headless-first is a default

The skill assumes that unit, integration, HTTP, jsdom, and headless-browser tests are cheaper and more reproducible than a full desktop shell. A project may require a real desktop/WebView smoke test for native behavior, but that requirement should be explicit in `verification.ui_smoke` or repository instructions.

Do not launch a desktop shell merely because a task mentions the UI. First identify which acceptance criteria cannot be proven headlessly.

## Deployment is not universal

`deployment.mode: gitops_only` and `deployment.controller: argocd` are valid examples for projects whose live state is owned by Git and Argo CD. Another project may use Flux, a release pipeline, or a human-controlled deployment. The generic skill does not choose.

If the deployment mode is `unspecified`, the agent may prepare or review artifacts but must not mutate production state.

## Profile reuse

Use role profiles across boards. A repository name in a profile name is a signal that migration may be needed, not a requirement for isolation. Create a project-specific profile only when credentials, tools, memory, or safety policy must be isolated.

Model strength normally belongs in a task-level override. Separate profiles are justified by behavior or permissions, not just by “Sonnet versus Opus.”
