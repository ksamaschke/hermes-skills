---
name: scoped-subagent-audits
description: "Run scoped subagent audits with explicit time budgets."
version: 0.1.0
author: Karsten Samaschke, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [subagents, audits, reviews, timeouts, orchestration]
    related_skills: []
---

# Scoped Subagent Audits Skill

Use this skill for read-only audits, design reviews, adversarial reviews, and
verification tasks delegated to subagents. It prevents two common failures:
workers wandering through an entire repository and workers timing out before
producing a useful result.

## When to Use

- A subagent will inspect more than one file, resource, or log source.
- A review must correlate repository state with live infrastructure.
- A task is read-only but too large or noisy for the parent context.
- A previous worker timed out, drifted, or returned an unverifiable claim.

Do not use for a single mechanical read or one deterministic command; use the
parent tool directly instead.

## Non-Negotiable Rule

Every audit/review subagent receives both:

1. an explicit scoped working set; and
2. an explicit time budget appropriate to the work.

Never dispatch a broad "inspect everything" task with the default timeout.
Never let a worker discover its own scope by recursively scanning the whole
repository.

## Time Budget

Use these minimum budgets when the execution backend exposes a timeout:

- focused file/config review: 600 seconds;
- repository plus live-resource audit: 900 seconds;
- stateful infrastructure or cross-system review: 1,200 seconds;
- multi-phase forensic review: 1,800 seconds or split into separate phases.

If `delegate_task` does not expose a timeout for the current backend, split the
work into bounded phases or run a terminal-backed worker with an explicit
`timeout_s` of at least 900 seconds. Do not pretend the backend's hidden default
is sufficient.

## Scope Packet

Give every worker a scope packet containing:

- repository root and exact directories/files to inspect;
- live namespaces, resource names, hosts, or log windows;
- read-only versus allowed mutation boundary;
- prohibited data, especially Secrets, tokens, credentials, and private keys;
- concrete questions to answer;
- required evidence format and maximum output size;
- a stop condition when the requested evidence is complete.

Prefer a short list of exact paths and resource names over a broad keyword.
Explicitly exclude unrelated directories, generated caches, vendored code, and
credential-bearing files.

## Procedure

1. **Define the acceptance questions.** Write the smallest set of facts the
   parent needs to decide or implement the change.
2. **Build the scope packet.** Name files, namespaces, resources, and time
   windows. State `read-only` unless mutation is explicitly required.
3. **Choose the budget.** Apply the minimums above; increase the budget before
   dispatching if the worker must correlate live systems or stateful storage.
4. **Require checkpoints.** Tell the worker to return an initial inventory,
   intermediate findings, and a final evidence table before the deadline.
5. **Dispatch one bounded task or a small parallel batch.** Keep independent
   audits separate. Do not make one worker own unrelated systems.
6. **Monitor live transcripts.** Use `delegate_task(action="list")`; steer a
   worker that expands scope, repeats a failing command, or spends most of its
   budget on irrelevant output.
7. **Recover deliberately.** If a worker approaches its deadline without a
   result, steer it to return partial evidence immediately or stop it and
   re-dispatch the missing slice with a narrower scope and a larger budget.
8. **Verify in the parent.** Treat worker reports as hypotheses. Re-read the
   named files, inspect the named live resources, and run the decisive test
   directly before claiming a finding or external side effect.

## Required Worker Output

Require a compact report with:

- `scope_checked`;
- `evidence` containing paths, resource names, timestamps, and command results;
- `findings` separated into verified facts and hypotheses;
- `gaps` or timed-out slices;
- `recommendation` limited to the requested decision;
- `mutations`: always `none` for a read-only audit.

A timeout is a failed/incomplete audit, not a negative finding. Preserve the
gap explicitly and do not summarize an absent result as "no issue found."

## Pitfalls

- A 600-second default is not enough for a broad live infrastructure audit.
- A worker that reads a full repository often spends its budget on irrelevant
  files and returns without the decisive resource evidence.
- Parallel workers must not share a mutable checkout unless the task is strictly
  read-only; use isolated worktrees or temporary clones.
- Never ask a worker to print Secret data for comparison. Compare names, keys,
  hashes, references, or redacted structure instead.
- Do not trust claims that a worker changed, uploaded, merged, or deployed
  something. Require a handle and verify it independently.

## Verification

The parent task is complete only when:

- every requested scope slice has a result or an explicit gap;
- the worker's evidence is independently reproduced where material;
- no worker exceeded scope without parent approval;
- the actual execution output backs the final claim;
- timeouts and partial results are reported honestly.
