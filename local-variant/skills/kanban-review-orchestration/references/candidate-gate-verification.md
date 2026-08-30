# Stale candidate identity and clean-checkout fixture rot

A validated case where a Kanban board reported "idle, waiting for an
independent review" while the real blocker was a red build gate on a
candidate whose identity had silently changed.

## Symptom

- Board: 0 running, 0 review, 0 ready, 1 dependency-gated todo, 15 blocked.
- Every blocked card is a reviewer timeout at 602–614s against a 600s cap.
- The progress digest concludes "IDLE-BY-GATING, next gate: obtain a fresh
  APPROVED verdict" and "no human action required".
- The tracker issue is still open with no merge or release evidence.

The digest is coherent and cites real run ids. It is still wrong about the
next gate, because it never ran the build.

## What the gate actually showed

From a fresh clone of the candidate worktree (clean tree, deterministic
across two runs):

```text
Ran 63 tests in 72.155s
FAILED (errors=2)
```

Only the test target was red; the validator and render targets exited 0. CI
ran the test target on every pull request, so the branch could not merge.

## Root cause: committing a delta invalidates fixtures written against it

Every review packet named the candidate as:

```text
candidate_commit: <base-sha> plus the uncommitted implementation delta
                  in the target worktree
```

A later commit turned that uncommitted delta into tracked files. Two fixtures
had been written assuming those paths were absent from a clone, and both
broke on the same underlying change:

- `clone_tools.mkdir()` on a directory the clone now already contains →
  `FileExistsError`.
- Copy tracked files over identical content, then `git add --all && git
  commit` → nothing staged → git exits 1, surfacing as
  `CalledProcessError: ['git','commit',...] exit status 1`.

The second one impersonates an environment problem. Rule it out before
believing it: check `git config user.name` / `user.email` and make a control
commit in a scratch repo. Git's own message is the tell — "nothing to commit,
working tree clean" is a fixture-logic defect, not missing identity.

## The timing check that explains the stall

Compare the newest approval timestamp against the newest commit on the
candidate branch. Here the approval predated the commit by a day, and every
blocked leaf still pointed at the superseded base. The reviewers were not
merely slow; they were reviewing an artifact that no longer existed. Adding
review capacity would not have fixed it.

## Verification sequence

```bash
T=$(mktemp -d); git clone -q --no-hardlinks "$WORKTREE" "$T/c"; cd "$T/c"
git rev-parse HEAD; git status --porcelain | wc -l   # confirm clean
make test > /tmp/gate.out 2> /tmp/gate.err; echo "exit=$?"
grep -nE "^(Ran |OK|FAILED|ERROR:)" /tmp/gate.err
```

Capture the exit code on its own line before any pipe. Re-run the failing
tests alone, twice, to prove determinism. Then locate the commit that
introduced the tracked paths (`git log --diff-filter=A -- <path>`) to tie the
failures to a single cause.

## Routing the repair

File one focused implementer card scoped to the test file alone, with the
root cause, both failing locations, and the ruled-out environment
hypothesis stated explicitly. Require in the acceptance criteria:

- the gate green from a **fresh clone**, not the worktree;
- the total test count unchanged, proving nothing was skipped or deleted;
- the fixtures made robust to the path being present or absent, so they
  cannot rot again on the next tracking-status change.

State plainly that the tests must not be weakened, skipped, marked expected
failures, or removed to reach green. A fixture that stops exercising
clean-checkout completeness is a failed repair, not a fix.

Do not resurrect review leaves pinned to the dead revision. Once the gate is
green there is a genuinely new candidate, and it needs fresh leaves against
the new commit.

## Timeout caps are a policy question

When reviewer runs fail by seconds against their cap and a single build step
consumes a large share of the budget, raising the cap and narrowing the scope
are both legitimate. That trade belongs to the operator; present the measured
numbers and ask rather than silently editing the cap.
