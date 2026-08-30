# Factory reviewer routing reference

This reference records the verified runtime procedure from the factory reviewer
cost correction. It is intentionally secret-free; credentials and tokens stay
in Hermes auth/config stores and are never copied into evidence.

## Effective profiles

The factory has two reviewer entry points:

- `reviewer`
- `vanillacore-reviewer`

Both must resolve to the same configured route when they serve the same factory
policy:

```text
model.default: gpt-5.6-luna
model.provider: openai-codex
agent.reasoning_effort: max
toolsets.0.reasoning_effort: max
fallback_providers: []
```

The old route was `homelab/claude-opus-5` through
`custom:homelab-reviewer`. Remove that stale provider block when it is no
longer used so future status output cannot mistake an unused provider for the
active route.

## Applied command sequence

```bash
hermes --profile vanillacore-reviewer config set model.default gpt-5.6-luna
hermes --profile vanillacore-reviewer config set model.provider openai-codex
hermes --profile vanillacore-reviewer config set agent.reasoning_effort max
hermes --profile vanillacore-reviewer config set toolsets.0.reasoning_effort max
hermes --profile vanillacore-reviewer config unset providers.homelab-reviewer

hermes --profile reviewer config set toolsets.0.reasoning_effort max
hermes --profile reviewer config unset providers.homelab-reviewer
```

The `agent.reasoning_effort` setter may emit a compatibility warning on some
Hermes versions while still saving the custom key. Treat that warning as a
reason to verify the resolved config and runtime request, not as proof that the
change failed.

## Required readback

```bash
for profile in reviewer vanillacore-reviewer; do
  hermes --profile "$profile" config get model.default
  hermes --profile "$profile" config get model.provider
  hermes --profile "$profile" config get agent.reasoning_effort
  hermes --profile "$profile" config get toolsets.0.reasoning_effort
  hermes --profile "$profile" config get providers.homelab-reviewer.model
  hermes --profile "$profile" profile show "$profile"
done
```

Expected active values are `gpt-5.6-luna`, `openai-codex`, `max`, and `max`.
The stale-provider lookup should say the key is not set.

Use a bounded route probe for the domain profile:

```bash
hermes --profile vanillacore-reviewer --ignore-rules \
  --oneshot 'Return exactly ROUTE_CHECK and nothing else.' \
  --reasoning max --usage-file /tmp/vanillacore-reviewer-route-check.json
```

The probe should return `ROUTE_CHECK`. The usage JSON should show
`completed: true`, `failed: false`, `api_calls: 1`, `model: gpt-5.6-luna`,
and `provider: openai-codex`.

Inspect active processes before reporting an immediate switch. Configuration
is read when a worker starts; an already-running old worker does not change
model in place. If no worker for the exact profile is active, the next review
uses the new route.

## Independence caveat

The current factory implementer route also uses OpenAI/Codex. Therefore moving
both reviewer profiles to GPT-5.6 Luna sacrifices the normal cross-vendor
independence property. This was an explicit user-directed cost decision, not a
reason to relabel same-family review as independent. Keep every timeout,
read-only, exact-scope, and verdict-integrity rule unchanged, and report the
same-family caveat with review evidence.
