#!/usr/bin/env python3
"""Stop invalid review packets from being fanned out, and quarantine the leaves.

This is the external add-on for factory defect 2. It is deliberately outside
Hermes core: the core auto-decomposer is a generic triage-to-workgraph LLM step
with no concept of a review packet, and it must not grow one.

The failure it prevents, observed end to end on a live board:

    review card (invalid packet: 17 paths, no hunk ranges,
                 gate cited as the literal "all gate commands green")
      -> reviewer returns REVIEW-INCOMPLETE  (correct)
      -> reviewer returns REVIEW-INCOMPLETE  (correct, second run)
      -> block-loop detection escalates the card to `triage`
      -> core auto-decomposer fans `triage` out into 4 children
      -> all 4 inherit the same invalid packet
      -> all 4 return REVIEW-INCOMPLETE

Six worker runs, zero source inspection. Splitting cannot repair a validity
failure; it multiplies it.

Two guards, run in order:

``quarantine``
    A review card sitting in ``triage`` is one dispatcher tick away from being
    fanned out. If its packet is invalid, archive it before that happens and
    file a single packet-repair card. ``triage`` cards cannot be blocked
    (``hermes kanban block`` rejects the transition), so archive is the only
    durable stop — verified against the live CLI.

``sweep``
    For leaves already created from an invalid parent, block any that still
    carry the parent's defect, so an inherited-invalid packet cannot be
    dispatched a second time.

Neither guard touches a source worktree, a tracker, or Git history.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_packet_integrity as rpi  # noqa: E402

MARKER = "[packet-repair-guard]"
REVIEW_HINTS = (
    "review_kind:",
    "read_only_source:",
    "adversarial review",
    "adversarial read-only",
    "review leaf",
    "candidate_commit:",
)


def _hermes(*args: str, timeout: int = 120) -> tuple[int, str, str]:
    exe = shutil.which("hermes") or "hermes"
    env = os.environ.copy()
    env.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)
    proc = subprocess.run(
        [exe, *args], text=True, capture_output=True, timeout=timeout, env=env, check=False
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _hermes_json(*args: str) -> Any:
    code, out, err = _hermes(*args, "--json")
    if code != 0:
        raise RuntimeError(f"hermes {' '.join(args[:4])} failed: {err or out}")
    return json.loads(out)


def board_db(board: str) -> Path:
    for entry in _hermes_json("kanban", "boards", "list") or []:
        if isinstance(entry, dict) and entry.get("slug") == board:
            return Path(str(entry.get("db_path"))).expanduser()
    raise RuntimeError(f"no board database for {board}")


def read_tasks(board: str) -> list[dict]:
    """Read task rows directly.

    `PRAGMA query_only` rather than a `mode=ro` URI: the board is WAL, and a
    read-only handle cannot create the `-shm` file it needs when no writer holds
    the database open ("unable to open database file"). Verified on this host.
    """
    conn = sqlite3.connect(str(board_db(board)), timeout=10)
    try:
        conn.execute("PRAGMA query_only=1")
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT id, title, body, assignee, status, created_by, workspace_path, "
            "max_runtime_seconds, max_retries FROM tasks"
        )]
    finally:
        conn.close()


def looks_like_review(task: dict) -> bool:
    body = str(task.get("body") or "").lower()
    title = str(task.get("title") or "").lower()
    if any(hint in body for hint in REVIEW_HINTS):
        return True
    return title.startswith("review ") or " review " in title


# --------------------------------------------------------------------------
# Packet validation focused on the two defects that actually caused the burn.


def packet_errors(task: dict) -> list[str]:
    """Return the packet defects that make this review card undispatchable.

    Intentionally narrow: it checks the things whose absence provably wastes a
    review lane, not the full reviewer contract. A card that passes here can
    still be rejected by the stricter contract validator downstream.
    """
    body = str(task.get("body") or "")
    errors: list[str] = []

    scope = _scope_entries(body)
    if not scope:
        errors.append("exact_scope must contain file paths")
    else:
        unranged = [p for p in scope if not re.search(r":\d+-\d+", p)]
        if unranged:
            errors.append(
                f"exact_scope entries carry no hunk range ({len(unranged)}/{len(scope)} paths): "
                + ", ".join(unranged[:3])
                + ("..." if len(unranged) > 3 else "")
            )

    gate = _gate_section(body)
    if rpi.is_unverifiable_gate_citation(gate):
        shown = " ".join(gate.split())[:80] or "(absent)"
        errors.append(f"gate evidence is unverifiable: '{shown}'")

    if not re.search(r"\b[0-9a-f]{40}\b", body):
        errors.append("missing candidate_commit")
    return errors


def _scope_entries(body: str) -> list[str]:
    """Collect the change manifest entries from a packet body."""
    lines = body.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"^\s*(?:#+\s*)?(?:exact[_ ]scope|change manifest)\b", line, re.I):
            capturing = True
            continue
        if capturing:
            if stripped.startswith("#"):
                break
            if stripped.startswith("-"):
                out.append(stripped[1:].strip().strip("`"))
                continue
            if not stripped:
                if out:
                    break
                continue
            if not out:
                continue
            break
    return [o for o in out if o and not o.lower().startswith("(+")]


def _gate_section(body: str) -> str:
    """Extract the gate-evidence prose from a packet body."""
    match = re.search(
        r"(?:#+\s*)?gate evidence[^\n]*\n(.*?)(?=\n\s*#|\Z)", body, re.I | re.S
    )
    if match:
        return match.group(1).strip()
    match = re.search(r"(?im)^\s*gate(?:_note|_evidence)?\s*:\s*(.+)$", body)
    return match.group(1).strip() if match else ""


# --------------------------------------------------------------------------
# Guard 1: quarantine invalid review packets sitting in triage.


def repair_card_body(task: dict, errors: list[str], decision) -> str:
    return f"""{MARKER} A review packet failed validation and was stopped before fan-out.

original_review_card: {task.get('id')}
original_title: {task.get('title')}

## Why this is a repair job, not a split

{decision.reason}

Splitting an invalid packet does not repair it: every child inherits the same
defect and returns REVIEW-INCOMPLETE for the same reason. A strict-subset
manifest is the remedy for genuine change-set *size*, never for invalidity.

## Defects to fix

{chr(10).join(f'- {e}' for e in errors)}

## Definition of done

- The change manifest lists each changed path WITH per-path hunk ranges in the
  form `path:1-20,40-55`. `git diff -U0 <base>..<candidate>` emits these directly.
- Gate evidence cites a real command, its exit code, and the commit it ran
  against — or explicitly declares the gate absent. Never assert a bare green.
- The packet names its candidate commit.

Once repaired, re-dispatch a fresh review card. Do not re-run the original.
"""


def quarantine(board: str, *, apply: bool) -> list[str]:
    """Archive invalid review packets in `triage` before the decomposer sees them.

    `triage` is the pre-fan-out state the core auto-decomposer polls. Cards there
    cannot be blocked — `hermes kanban block` rejects the transition — so archive
    is the only durable stop.
    """
    changes: list[str] = []
    for task in read_tasks(board):
        if str(task.get("status") or "") != "triage":
            continue
        if not looks_like_review(task):
            continue
        errors = packet_errors(task)
        if not errors:
            continue
        decision = rpi.split_decision(errors)
        if decision.allowed:
            changes.append(f"{task['id']}: size-only defect, split permitted; left in triage")
            continue

        tid = task["id"]
        if not apply:
            changes.append(f"would quarantine {tid} (invalid packet): {'; '.join(errors)}")
            continue

        code, out, err = _hermes("kanban", "--board", board, "archive", tid)
        if code != 0:
            changes.append(f"{tid}: archive FAILED: {err or out}")
            continue
        after = _task_status(board, tid)
        if after != "archived":
            changes.append(f"{tid}: archive readback FAILED (status={after!r})")
            continue

        rc, rout, rerr = _hermes(
            "kanban", "--board", board, "create",
            f"Repair review packet: {str(task.get('title') or tid)[:60]}",
            "--body", repair_card_body(task, errors, decision),
            "--assignee", os.environ.get("PACKET_REPAIR_ASSIGNEE", "default"),
            "--priority", "80",
            "--idempotency-key", f"packet-repair:{tid}",
            "--created-by", "packet-repair-guard",
            "--json",
        )
        repair_id = ""
        if rc == 0:
            try:
                payload = json.loads(rout)
                repair_id = str((payload.get("task") or payload).get("id") or "")
            except Exception:
                repair_id = ""
        changes.append(
            f"quarantined {tid} (invalid packet, {len(errors)} defect(s)); "
            f"repair card {repair_id or 'CREATE FAILED: ' + (rerr or rout)[:120]}"
        )
    return changes


def _task_status(board: str, task_id: str) -> str:
    try:
        detail = _hermes_json("kanban", "--board", board, "show", task_id)
    except Exception:
        return ""
    task = detail.get("task") if isinstance(detail, dict) else None
    return str((task or detail or {}).get("status") or "")


# --------------------------------------------------------------------------
# Guard 2: block leaves that inherited a parent's invalid packet.


DISPATCHABLE = {"ready", "todo", "review"}


def sweep(board: str, *, apply: bool) -> list[str]:
    """Block already-created leaves that still carry an invalid packet."""
    changes: list[str] = []
    for task in read_tasks(board):
        if str(task.get("status") or "") not in DISPATCHABLE:
            continue
        if not looks_like_review(task):
            continue
        errors = packet_errors(task)
        if not errors:
            continue
        if rpi.split_decision(errors).allowed:
            continue
        tid = task["id"]
        reason = f"{MARKER} not dispatched: " + "; ".join(errors)
        if not apply:
            changes.append(f"would block {tid}: {'; '.join(errors)}")
            continue
        code, out, err = _hermes(
            "kanban", "--board", board, "block", tid, reason, "--kind", "needs_input"
        )
        if code != 0:
            changes.append(f"{tid}: block FAILED: {err or out}")
            continue
        after = _task_status(board, tid)
        changes.append(
            f"blocked {tid} (inherited-invalid packet)"
            if after == "blocked"
            else f"{tid}: block readback FAILED (status={after!r})"
        )
    return changes


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--board", default=os.environ.get("HERMES_FACTORY_BOARD", "minna"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)
    try:
        changes = quarantine(args.board, apply=args.apply)
        changes += sweep(args.board, apply=args.apply)
    except Exception as exc:
        print(f"packet guard failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if changes and not args.quiet:
        print("\n".join(changes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
