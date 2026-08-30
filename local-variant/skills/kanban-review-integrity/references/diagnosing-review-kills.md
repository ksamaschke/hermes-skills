# Diagnosing review kills from worker logs

How to tell whether a killed adversarial review was caused by scope, by the
budget model, or by the execution boundary. Getting this wrong produces
continuation chains that shrink scope forever without fixing anything.

## Where the evidence lives

Kanban worker logs are per-task, outside the profile log directory:

```bash
~/.hermes/kanban/boards/<board>/logs/<task_id>.log
```

Board state and the failure record:

```bash
hermes kanban --board <board> stats --json
hermes kanban --board <board> list --status blocked --json
hermes kanban --board <board> show <task_id> --json
```

The `gave_up` event carries the arithmetic, e.g. `elapsed 608s > limit 600s`.

## The decisive measurements

Three cheap greps separate a scope problem from a budget problem.

**1. Sum the worker's own command time.** Hermes logs each command's duration:

```bash
L=~/.hermes/kanban/boards/<board>/logs/<task>.log
grep -oE '[0-9]+\.[0-9]s' "$L" | sed 's/s$//' | awk '{s+=$1} END {print s}'
```

If command time alone approaches or exceeds the cap, the reviewer was doing
forbidden work — no amount of scope narrowing fixes it. A real case summed
**674.6s against a 600s cap**.

**2. Find the individual long commands.**

```bash
grep -oE '[0-9]+\.[0-9]s' "$L" | sed 's/s$//' | awk '$1>30' | sort -rn | head
```

A single 420s entry is almost always `make test` / `make validate` — the full
project gate running inside a review. That evidence belongs to the implementer
and CI and should be cited, not re-run.

**3. Check for provider backoff at the head of the log.**

```bash
grep -c "429\|cooling down\|RateLimit\|model_cooldown" "$L"
head -c 600 "$L"
```

A `429` / `model_cooldown` at the start means retries burned the same wall clock
the review needed. The route failed, not the scope.

## Reading the continuation depth

The packet body records `continuation_depth`, `continuation_of`, and
`failure_run`. Cross-check depth against scope size:

- depth 3 with **one file and one acceptance question**, still killed →
  conclusive proof that narrowing is not the remedy. Escalate as a factory
  fault.
- depth 1 with seven files and multiple questions → genuine change-set size;
  splitting is correct.

## Decision table

| Log signal | Cause | Correct remedy |
|---|---|---|
| Command time ≈ or > cap; one 300s+ command | Full gate inside the review | Remove the gate command, cite CI evidence, re-dispatch **same** slice |
| `429` / `model_cooldown` near log start | Provider backoff | Re-probe/fix the route, re-dispatch **same** slice |
| Scope is a directory/module/glob | Invalid packet | Fix the packet scope, re-dispatch **same** slice |
| Small command time, many files opened | Genuine change-set size | Split the change manifest into strict subsets |
| Already minimal scope, still killed | Budget/boundary defect | Escalate as factory fault; do **not** continue the chain |

## Anti-pattern this prevents

A board showed 15 blocked cards, 12 on the reviewer lane, as a chain of
`Review continuation ...` cards at increasing depth. Each recovery pass narrowed
scope because "narrow further" was the only prescribed remedy. The actual causes
were a one-tier wall clock and an execution boundary that permitted the project
gate. Scope had stopped being the variable several continuations earlier.

**Before creating any continuation, read the failed leaf's log and classify the
cause.** A continuation created without that classification is a guess.

## Verifying a policy fix actually lands

A runtime cap can be enforced in *code*, not just documented in prose. Check for
a validator that hard-rejects any other value before assuming configuration can
fix it:

```bash
grep -rn "max_runtime_seconds.*!= *[0-9]" <factory-repo>/scripts/
```

After changing policy, confirm no stale literal survives anywhere in the
contract surface:

```bash
grep -rn "600-second\|600 seconds\|max_runtime_seconds=600\|max_runtime_seconds: 600" \
  <factory-repo>/docs <factory-repo>/skills <factory-repo>/examples
grep -rln "600-second\|600 seconds\|max_runtime_seconds=600" ~/.hermes/skills/
```

Search `references/` subdirectories too — support files carry policy that the
SKILL.md grep misses, and a reference file can silently contradict the contract
it supports.
