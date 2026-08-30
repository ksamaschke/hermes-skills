---
name: factory-review-routing
description: Use for factory reviewer model and effort routing.
version: 1.0.0
author: HEX
license: MIT
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [software-factory, kanban, reviewer, model-routing, cost-control, verification]
    related_skills: [subscription-agent-steering, kanban-reviewer-contract, kanban-review-integrity]
---

# Factory Reviewer Routing

Use this skill when a software-factory or Kanban reviewer is too expensive,
uses the wrong provider, times out because of an unsuitable route, or needs a
specific model and reasoning effort. It governs the **active Hermes reviewer
profile** used by the board, not merely a generated project agent or a stale
example file.

## Core policy

- Reviewer model changes are runtime profile changes. Change the profile that
  the board actually assigns, commonly `reviewer` or a domain profile such as
  `vanillacore-reviewer`.
- For the current factory preference, use `gpt-5.6-luna` through the
  authenticated `openai-codex` provider with `max` reasoning effort.
- Keep both the profile's agent effort and the reviewer toolset effort explicit
  at `max` when the user asks for maximum effort. A model change without an
  effort readback is incomplete.
- Remove stale unused provider blocks that still point at an expensive Opus
  route. A stale provider is a future routing footgun and makes status reports
  ambiguous.
- Do not change review semantics while changing model economics. Reviewers
  remain read-only, exact-scope, independently dispatched, and skeptical.
- A timeout, crash, wrong target, mutation, or incomplete run remains
  `REVIEW-INCOMPLETE`; a cheaper or faster model does not convert incomplete
  evidence into approval.

The verified command sequence and the current profile snapshot are in
`references/factory-reviewer-routing.md`.

## Procedure

### 1. Identify the real reviewer route

Read the board/task assignment and map it to the actual Hermes profile. Do not
assume that the profile named `reviewer` is the one running a domain board.
Inspect the effective profile configuration with:

```bash
hermes --profile <profile> config get model --json
hermes --profile <profile> config get agent --json
hermes --profile <profile> config get toolsets --json
hermes --profile <profile> profile show <profile>
```

Record the old model, provider, fallback chain, agent effort, toolset effort,
and whether a long-running worker is already using the profile.

### 2. Apply the requested route through the supported config surface

Use Hermes profile-scoped configuration commands rather than editing generated
runtime artifacts:

```bash
hermes --profile <profile> config set model.default gpt-5.6-luna
hermes --profile <profile> config set model.provider openai-codex
hermes --profile <profile> config set agent.reasoning_effort max
hermes --profile <profile> config set toolsets.0.reasoning_effort max
```

If the profile contains an obsolete provider block for the old model, remove
that block with `config unset` or a narrowly scoped config edit. Do not delete
unrelated provider credentials or rewrite the profile wholesale. Keep
`fallback_providers` empty unless the user explicitly asks for a fallback.

Apply the same normalization to every active reviewer entry point only when
they serve the same factory policy. Do not change implementer, dispatcher, or
researcher routes as an incidental side effect.

### 3. Check independence before reporting success

Compare the selected review provider family with the implementer family.
Cross-family review is the normal integrity control. If the user's requested
model puts reviewer and implementer in the same family, do not silently claim
independent review. Report the lost independence explicitly and preserve the
review verdict restrictions. If the factory has an explicit waiver mechanism,
write the waiver in the same configuration change; never leave a known failing
invariant unexplained.

A model switch is allowed when the user explicitly requests it, but the
resulting evidence is still not stronger than the reviewer route's actual
independence. Same-family review can be useful for cost/latency or a factory
self-review, but it is not a substitute for an independent vendor family.

### 4. Verify effective configuration, not only the file

Read back every setting:

```bash
for key in model.default model.provider agent.reasoning_effort toolsets.0.reasoning_effort; do
  hermes --profile <profile> config get "$key"
done
hermes --profile <profile> config get providers.<old-provider>.model
```

The last command should report that the stale provider is unset when it was
removed. Run `config check` and `profile show`; warnings about a custom effort
compatibility key are not proof of failure if the resolved config and runtime
probe demonstrate that Hermes passes the setting through.

Run one bounded, no-tools route probe for the exact profile:

```bash
hermes --profile <profile> --ignore-rules \
  --oneshot 'Return exactly ROUTE_CHECK and nothing else.' \
  --reasoning max --usage-file /tmp/<profile>-route-check.json
```

Read the usage file and require:

- `completed: true`;
- `failed: false`;
- the requested model and provider;
- one bounded API call or another clearly bounded execution result.

Never print credentials or raw authorization material while verifying the
route.

### 5. Handle already-running workers honestly

Profile configuration is read when a worker starts. Inspect running processes
for the exact profile before claiming an immediate switch. If no worker for the
profile is active, the next dispatch uses the new route. If an old worker is
active, do not claim it changed models mid-run; let it finish or use the
factory's explicit drain/restart mechanism. Do not kill unrelated workers.

### 6. Re-run a representative review

A one-shot route probe proves reachability, not review quality. After the
configuration change, dispatch one bounded representative review with the
normal exact-scope packet and read-only contract. Verify its run metadata names:

- the exact reviewer profile;
- resolved model and provider;
- reasoning effort;
- candidate repository/commit and file scope;
- no mutations;
- terminal outcome and evidence.

Keep the timeout bounded. If the review times out, classify it as
`REVIEW-INCOMPLETE`, inspect the process/log, and use a narrower successor
rather than retrying the same broad prompt indefinitely.

## Cost and quality trade-offs

Moving ordinary reviews from Opus to GPT-5.6 Luna can materially reduce token
pressure while retaining a high reasoning setting. Maximum effort does not
justify broad prompts, giant inherited toolsets, or unbounded context. Keep
review packets exact-scope and keep the reviewer toolset minimal.

A model/provider change cannot repair a bad review packet, missing repository
capability, excessive concurrency, or an invalid successor chain. Diagnose
those independently.

## Non-goals and invariants

- Do not edit `.cursor/agents/*.md` or other generated lane artifacts as the
  source of truth.
- Do not change Hermes core for a profile-routing request.
- Do not weaken timeout handling, verdict parsing, issue-bound checks, reviewer
  independence reporting, or read-only enforcement.
- Do not treat a successful route probe as approval of any code change.
- Do not turn a same-family reviewer into a falsely labelled independent
  reviewer.
- Do not expose secrets while inspecting provider configuration.

## Verification checklist

Before closing the routing change, provide direct evidence for:

1. old and new effective model/provider;
2. agent and reviewer-toolset effort;
3. stale provider removal or explicit retention rationale;
4. `config check`/`profile show` result;
5. bounded usage record with model/provider;
6. active-worker status;
7. vendor-family comparison and any explicit waiver/caveat;
8. representative review outcome, if dispatched.

The reusable exact configuration and probe output shape are kept in
`references/factory-reviewer-routing.md`.
