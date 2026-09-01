#!/usr/bin/env python3
"""Packet integrity for factory review dispatch: build it right, don't fan out a lie.

This module is deliberately outside Hermes core. It closes three defects that
together burned six worker runs on one review card with zero source inspection:

1. **Duplicate review of an already-approved candidate.** A review-cycle that
   keys only on "open PR + no leaf on this board" re-reviews an artifact whose
   exact tree already carries a terminal approving verdict from an independent
   reviewer. The duplicate reviewer lacks the round-1/round-2 history and
   naturally re-flags deliberate, adjudicated asymmetries — so acting on its
   findings *reverts mutation-proven fixes*. See ``approving_review_for_tree``.

2. **Packet generation that asserts unmeasured evidence.** ``"all gate commands
   green"`` is not gate evidence: no command, no exit code, no commit. When the
   repo has no committed CI workflow at the candidate, such a citation is
   unverifiable *by construction* and a correct reviewer must return
   REVIEW-INCOMPLETE. See ``GateEvidence`` and ``render_gate_evidence``.

3. **Splitting an invalid packet.** Fanning out a packet that fails validation
   multiplies the defect by N; each leaf inherits the same missing hunk ranges
   and the same unverifiable gate citation. A strict-subset manifest is the
   remedy for change-set *size*, never for invalidity. See
   ``classify_packet_defects`` and ``split_decision``.

Nothing here mutates a source worktree, a tracker, or Git history. Every helper
is pure except the ones that shell out to read-only ``git`` plumbing.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


# --------------------------------------------------------------------------
# Candidate identity
#
# A commit sha is NOT a stable identifier for "the artifact that was reviewed".
# The observed failure: an implementation card was reviewed and APPROVED while
# its work sat *uncommitted* in a worktree, so no run recorded any sha. A later
# recovery commit ("preserve uncommitted work") gave that identical content a
# brand-new sha, which then looked unreviewed to the review-cycle.
#
# The tree hash is the artifact identity that survives that. Two commits with
# different shas, different parents and different messages that produce the same
# tree ARE the same reviewed content.

_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b", re.IGNORECASE)


def _git(repo: str | Path, *args: str, timeout: int = 60) -> tuple[int, str]:
    """Run read-only git plumbing. Returns (exit_code, stdout.strip())."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, (proc.stdout or "").strip()


def tree_of(repo: str | Path, rev: str) -> str:
    """Return the tree hash for ``rev``, or "" when it cannot be resolved.

    The tree hash — not the commit sha — is the identity of the reviewed
    content. Rebases, cherry-picks, amended messages and "preserve uncommitted
    work" recovery commits all change the sha while leaving the tree identical.
    """
    if not str(rev).strip():
        return ""
    code, out = _git(repo, "rev-parse", f"{rev}^{{tree}}")
    return out if code == 0 and out else ""


def worktree_tree(worktree: str | Path) -> str:
    """Return the tree hash of a worktree's HEAD, or "" if unavailable."""
    return tree_of(worktree, "HEAD")


# --------------------------------------------------------------------------
# Terminal approving review lookup


TERMINAL_APPROVAL_OUTCOMES = {"completed", "done", "approved"}
APPROVED_METADATA_KEYS = ("review_outcome", "overall_verdict", "verdict")


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            import json

            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _is_approval(run: dict) -> bool:
    """True when this run is a terminal APPROVED verdict.

    Fail closed: an approval must be positively stated in run metadata or in a
    summary that is not negated ("not approved", "unapproved"). A merely
    ``completed`` run is not an approval.
    """
    outcome = str(run.get("outcome") or run.get("status") or "").strip().lower()
    if outcome not in TERMINAL_APPROVAL_OUTCOMES:
        return False
    meta = _as_dict(run.get("metadata"))
    for key in APPROVED_METADATA_KEYS:
        raw = str(meta.get(key) or "").strip().upper().replace("-", "_")
        if raw == "APPROVED":
            return True
        if raw in {"CHANGES_REQUESTED", "REVIEW_INCOMPLETE"}:
            return False
    summary = str(run.get("summary") or "")
    if re.search(r"\b(?:not|never|un)\s*approved\b", summary, re.IGNORECASE):
        return False
    return bool(re.search(r"\bapproved\b", summary, re.IGNORECASE))


@dataclass(frozen=True)
class ApprovingReview:
    """A terminal approving verdict already on record for a candidate tree."""

    task_id: str
    run_id: Any
    reviewer_profile: str
    tree: str
    summary: str = ""

    def as_note(self) -> str:
        return (
            f"candidate tree {self.tree[:12]} already carries a terminal APPROVED "
            f"verdict from independent reviewer '{self.reviewer_profile}' "
            f"(task {self.task_id}, run {self.run_id})"
        )


def approving_review_for_tree(
    *,
    repo: str | Path,
    candidate_rev: str,
    tasks: Iterable[dict],
    runs_for_task,
    implementer_profiles: Sequence[str] = (),
    resolve_worktree=None,
) -> Optional[ApprovingReview]:
    """Return an existing terminal approval covering ``candidate_rev``'s tree.

    ``tasks`` are board rows; ``runs_for_task(task_id)`` yields that task's runs.
    A run counts only when its profile is an *independent* reviewer — never one
    of ``implementer_profiles`` — because self-approval is not review evidence.

    Matching is by tree, resolved from each task's worktree HEAD (or, when the
    task records a sha in its body/metadata, from that sha). Returns ``None``
    when no prior approval covers this exact content, which is the signal to
    create a review card.
    """
    target = tree_of(repo, candidate_rev)
    if not target:
        return None
    implementers = {p.strip().lower() for p in implementer_profiles if str(p).strip()}

    for task in tasks:
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        runs = [r for r in (runs_for_task(task_id) or []) if isinstance(r, dict)]
        approvals = [r for r in runs if _is_approval(r)]
        if not approvals:
            continue

        # Resolve the tree this task's approval actually covers.
        candidates: list[str] = []
        worktree = task.get("workspace_path")
        if resolve_worktree is not None:
            worktree = resolve_worktree(task) or worktree
        if worktree and Path(str(worktree)).is_dir():
            found = worktree_tree(str(worktree))
            if found:
                candidates.append(found)
        blob = " ".join(
            str(task.get(k) or "") for k in ("body", "result", "title")
        )
        for run in approvals:
            blob += " " + str(run.get("summary") or "")
            blob += " " + str(run.get("metadata") or "")
        for sha in dict.fromkeys(_SHA_RE.findall(blob)):
            found = tree_of(repo, sha)
            if found:
                candidates.append(found)

        if target not in candidates:
            continue
        approval = approvals[-1]
        profile = str(approval.get("profile") or "").strip()
        if profile.lower() in implementers:
            continue
        return ApprovingReview(
            task_id=task_id,
            run_id=approval.get("id"),
            reviewer_profile=profile or "unknown",
            tree=target,
            summary=str(approval.get("summary") or "")[:400],
        )
    return None


# --------------------------------------------------------------------------
# Change manifest with real per-path hunk ranges


@dataclass(frozen=True)
class ChangedPath:
    path: str
    ranges: tuple[str, ...] = ()

    def render(self) -> str:
        """Render as ``path:1-20,40-55`` — the manifest form validators accept."""
        if not self.ranges:
            return self.path
        return f"{self.path}:{','.join(self.ranges)}"


_HUNK_RE = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")
_DIFF_HEADER_RE = re.compile(r"^\+\+\+ b/(.+)$")


def changed_paths_with_hunks(
    repo: str | Path, base: str, candidate: str
) -> list[ChangedPath]:
    """Return changed paths with real per-path added-line ranges.

    ``git diff -U0 <base>..<candidate>`` gives exact hunk headers; a zero-length
    hunk (pure deletion, ``+N,0``) is recorded at its anchor line so the path is
    never silently dropped from the manifest.
    """
    code, out = _git(repo, "diff", "-U0", f"{base}..{candidate}", timeout=180)
    if code != 0:
        return []
    per_path: dict[str, list[str]] = {}
    current: Optional[str] = None
    for line in out.splitlines():
        header = _DIFF_HEADER_RE.match(line)
        if header:
            current = header.group(1).strip()
            per_path.setdefault(current, [])
            continue
        if current is None:
            continue
        hunk = _HUNK_RE.match(line)
        if not hunk:
            continue
        start = int(hunk.group(1))
        count = int(hunk.group(2)) if hunk.group(2) is not None else 1
        if count <= 0:
            # Deletion-only hunk: anchor a 1-line range so the path keeps a range.
            per_path[current].append(f"{max(start, 1)}-{max(start, 1)}")
        else:
            per_path[current].append(f"{start}-{start + count - 1}")
    return [ChangedPath(p, tuple(r)) for p, r in per_path.items()]


# --------------------------------------------------------------------------
# Gate evidence that is measured, or honestly declared absent


@dataclass(frozen=True)
class GateEvidence:
    """One executed gate command, with the commit it actually ran against."""

    command: str
    exit_code: int
    commit: str
    detail: str = ""
    run_reference: str = ""

    def render(self) -> str:
        parts = [
            f"- `{self.command}` — exit {self.exit_code} @ {self.commit[:12]}",
        ]
        if self.detail:
            parts.append(f" ({self.detail})")
        if self.run_reference:
            parts.append(f" [{self.run_reference}]")
        return "".join(parts)


# Phrases that assert a green gate without naming a command, exit code or
# commit. A reviewer cannot verify any of these, so they must never reach a
# packet. This is the exact string that made one review card unreviewable.
_UNVERIFIABLE_GATE_PHRASES = (
    "all gate commands green",
    "all gates green",
    "all checks passed",
    "gate green",
    "gate is green",
    "ci green",
    "ci passed",
    "everything passes",
    "all tests pass",
    "all tests passed",
    "tests green",
)

# An explicit, honest declaration that no gate result exists. This is the
# CORRECT output when a repo has no committed gate — it asserts nothing the
# reviewer cannot check, so it must not be flagged as an unverifiable citation.
_HONEST_ABSENCE_MARKERS = (
    "gate absent",
    "gate not measured",
    "no gate evidence is cited",
    "declared, not asserted",
)


def is_unverifiable_gate_citation(text: str) -> bool:
    """True when a gate citation asserts green without measurable evidence.

    Three outcomes, only one of which is a defect:

    * measured evidence (command + exit code)      -> verifiable, fine;
    * an explicit declaration that no gate exists  -> honest, fine;
    * a bare green claim, or nothing at all        -> unverifiable, a defect.
    """
    value = " ".join(str(text or "").split()).strip().lower()
    if not value:
        return True
    if any(marker in value for marker in _HONEST_ABSENCE_MARKERS):
        # An honest absence is only acceptable if it does not ALSO claim green.
        return any(phrase in value for phrase in _UNVERIFIABLE_GATE_PHRASES)
    if any(phrase in value for phrase in _UNVERIFIABLE_GATE_PHRASES):
        # Still unverifiable unless it also carries a real exit code.
        return not re.search(r"exit(?:\s+code)?[\s=:]*\d+", value)
    has_exit = bool(re.search(r"exit(?:\s+code)?[\s=:]*\d+", value))
    has_command = bool(re.search(r"(cargo|pnpm|npm|npx|make|pytest|go|mvn|gradle|dotnet|bash|sh)\b", value))
    return not (has_exit and has_command)


def repo_declares_gate(repo: str | Path, rev: str) -> bool:
    """True when the repo has a committed gate at ``rev``.

    Checked against the *commit tree*, not the working directory: an untracked
    ``.forgejo/workflows/gate.yml`` sitting in someone's checkout is not a gate
    the candidate commit carries, and citing it would be another unverifiable
    claim.
    """
    code, out = _git(
        repo, "ls-tree", "-r", "--name-only", rev,
        "--", "Makefile", "makefile", ".forgejo/workflows", ".github/workflows",
        ".gitlab-ci.yml", "justfile", "Justfile",
    )
    return code == 0 and bool(out.strip())


def render_gate_evidence(
    evidence: Sequence[GateEvidence],
    *,
    gate_declared: bool,
    candidate: str,
) -> str:
    """Render measured gate evidence, or honestly declare the gate absent.

    Never emits an assertion the reviewer cannot check. When nothing was
    measured, it says so and tells the reviewer that missing gate evidence is a
    finding against the implementation card — not a licence to run the gate.
    """
    if evidence:
        lines = [
            "The orchestrator executed these diff-targeted gates. Do NOT re-run them;",
            "confirm the evidence corresponds to the candidate commit and cite it.",
            "",
        ]
        lines.extend(item.render() for item in evidence)
        stale = [e for e in evidence if e.commit and not candidate.startswith(e.commit[:12])
                 and not e.commit.startswith(candidate[:12])]
        if stale:
            lines.append("")
            lines.append(
                "WARNING: some evidence above was measured against a different commit "
                "than the candidate; treat it as stale."
            )
        return "\n".join(lines)

    if not gate_declared:
        return (
            "GATE ABSENT (declared, not asserted).\n\n"
            f"This repository has no committed Makefile and no CI workflow directory at "
            f"{candidate[:12]}, so no repository gate result exists for this candidate.\n"
            "No gate evidence is cited because none could be measured. Do not treat this "
            "as a successful gate, and do not run a full project gate to compensate — run only "
            "the diff-targeted checks listed above and record the gate as absent in `gaps`."
        )
    return (
        "GATE NOT MEASURED (declared, not asserted).\n\n"
        "This repository declares a gate, but this packet carries no measured result for "
        f"{candidate[:12]}. Missing gate evidence is a CHANGES_REQUESTED finding against "
        "the implementation card. Do not run the full gate yourself."
    )


# --------------------------------------------------------------------------
# Packet defect classification and the split decision


# Defects that make a packet *invalid*. Splitting cannot repair any of these —
# every child inherits them verbatim, which is how one bad packet became four.
INVALIDITY_MARKERS = (
    "missing ",
    "must be",
    "must contain at least",
    "non-file paths",
    "hunk range",
    "gate evidence",
    "unverifiable",
    "implementation instructions",
    "read_only_source",
    "vendor-family",
    "allowed_verdicts",
    "does not match",
    "exactly one acceptance question",
    "one review lens is required",
)

# The only defect a strict-subset split is allowed to remedy: genuine size.
SIZE_MARKERS = ("at most", "exceeds", "too many", "more than")


@dataclass(frozen=True)
class PacketDefects:
    invalid: tuple[str, ...] = ()
    size_only: tuple[str, ...] = ()

    @property
    def is_invalid(self) -> bool:
        return bool(self.invalid)

    @property
    def is_size_only(self) -> bool:
        return bool(self.size_only) and not self.invalid


def classify_packet_defects(errors: Iterable[str]) -> PacketDefects:
    """Split validation errors into invalidity vs. mere oversize.

    Oversize is the *only* class a strict-subset manifest can fix. Anything else
    is a packet-repair job for the packet generator.
    """
    invalid: list[str] = []
    size_only: list[str] = []
    for raw in errors:
        text = str(raw).strip()
        if not text:
            continue
        lowered = text.lower()
        if any(m in lowered for m in SIZE_MARKERS) and not any(
            m in lowered for m in ("missing", "must be 1", "must be 1800")
        ):
            size_only.append(text)
        elif any(m in lowered for m in INVALIDITY_MARKERS):
            invalid.append(text)
        else:
            invalid.append(text)
    return PacketDefects(tuple(invalid), tuple(size_only))


@dataclass(frozen=True)
class SplitDecision:
    allowed: bool
    action: str  # "split" | "repair"
    reason: str
    defects: PacketDefects = field(default_factory=PacketDefects)

    def as_block_reason(self) -> str:
        return f"[packet-repair] {self.reason}"


def split_decision(errors: Iterable[str]) -> SplitDecision:
    """Decide whether a packet may be split, or must be repaired first.

    This is the whole of defect 2: a decomposer that calls this before fanning
    out cannot turn one invalid packet into N invalid leaves.
    """
    defects = classify_packet_defects(errors)
    if defects.is_invalid:
        return SplitDecision(
            allowed=False,
            action="repair",
            reason=(
                "packet fails validation; splitting would copy the same defect into every "
                "leaf. Repair the packet before any fan-out. Defects: "
                + "; ".join(defects.invalid)
            ),
            defects=defects,
        )
    if defects.is_size_only:
        return SplitDecision(
            allowed=True,
            action="split",
            reason="change-set size only; a strict-subset manifest is the correct remedy: "
            + "; ".join(defects.size_only),
            defects=defects,
        )
    return SplitDecision(
        allowed=False,
        action="repair",
        reason="packet has no size defect to split on",
        defects=defects,
    )


def inherited_packet_defects(parent_errors: Iterable[str], child_body: str) -> list[str]:
    """Return parent defects a child body still carries.

    Used to catch leaves that were already created from an invalid parent: if
    the child reproduces the parent's unverifiable gate citation or still has no
    hunk ranges, it is an inherited-invalid leaf and must not be dispatched.
    """
    body = str(child_body or "")
    carried: list[str] = []
    for raw in parent_errors:
        text = str(raw).strip()
        lowered = text.lower()
        if "gate evidence" in lowered or "unverifiable" in lowered:
            if any(p in body.lower() for p in _UNVERIFIABLE_GATE_PHRASES):
                carried.append(text)
        elif "hunk range" in lowered:
            if not re.search(r":\d+-\d+", body):
                carried.append(text)
    return carried
