#!/usr/bin/env python3
"""Deterministic recovery layer around Hermes Kanban.

This is intentionally outside Hermes core. It repairs only mechanical factory
state that the supervised Hermes dispatcher cannot infer safely by itself:

* legacy LLM cron jobs that have creation-time provider/model snapshots but no
  durable fields;
* Kanban spawn failures caused solely by a duplicate clean managed Git
  worktree.

Dirty or non-managed worktrees are preserved and reported as genuine operator
disposition cases. Product failures, provider failures, review findings, and
human decisions are never auto-unblocked here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


_COLLISION_RE = re.compile(
    r"(?:['\"](?P<branch>[^'\"]+)['\"]\s+is already used by worktree at\s+['\"](?P<path>[^'\"]+)['\"]|"
    r"branch\s+(?P<branch2>[^\s]+)\s+is already checked out at\s+(?P<path2>[^\s]+))",
    re.IGNORECASE,
)


def _run(argv: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _hermes(*args: str) -> tuple[int, str, str]:
    return _run(["hermes", *args])


def _json_command(*args: str) -> Any:
    code, stdout, stderr = _hermes(*args)
    if code != 0:
        raise RuntimeError(stderr or stdout or f"hermes {' '.join(args)} failed")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from hermes {' '.join(args)}: {exc}") from exc


def _jobs_path() -> Path:
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    return (Path(hermes_home).expanduser() if hermes_home else Path.home() / ".hermes") / "cron" / "jobs.json"


def _repair_cron_pins(*, dry_run: bool) -> list[str]:
    path = _jobs_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    repairs: list[str] = []
    for job in data.get("jobs", []):
        if not isinstance(job, dict) or job.get("no_agent") or not job.get("enabled", True):
            continue
        provider = str(job.get("provider") or "").strip()
        model = str(job.get("model") or "").strip()
        snapshot_provider = str(job.get("provider_snapshot") or "").strip()
        snapshot_model = str(job.get("model_snapshot") or "").strip()
        if provider or model or not (snapshot_provider or snapshot_model):
            continue
        # A partial snapshot is not enough to invent the missing axis. Leave it
        # alone for the normal job failure/preflight path.
        if not (snapshot_provider and snapshot_model):
            continue
        job_id = str(job.get("id") or "").strip()
        if not job_id:
            continue
        if dry_run:
            repairs.append(
                f"would pin cron {job_id} to {snapshot_provider}/{snapshot_model}"
            )
            continue
        code, stdout, stderr = _run(
            [
                "hermes",
                "cron",
                "edit",
                job_id,
                "--provider",
                snapshot_provider,
                "--model",
                snapshot_model,
            ]
        )
        if code != 0:
            repairs.append(f"cron {job_id} pin failed: {stderr or stdout}")
            continue
        repairs.append(f"pinned cron {job_id} to {snapshot_provider}/{snapshot_model}")

    return repairs


def _task_detail(board: str, task_id: str) -> dict[str, Any] | None:
    try:
        value = _json_command("kanban", "--board", board, "show", "--json", task_id)
    except RuntimeError:
        return None
    return value if isinstance(value, dict) else None


def _latest_spawn_error(detail: dict[str, Any]) -> str:
    runs = detail.get("runs") or []
    for run in reversed(runs):
        if not isinstance(run, dict):
            continue
        if run.get("status") in {"spawn_failed", "gave_up"}:
            return str(run.get("error") or "")
    return ""


def _collision(error: str) -> tuple[str, Path] | None:
    match = _COLLISION_RE.search(error)
    if not match:
        return None
    branch = match.group("branch") or match.group("branch2")
    path = match.group("path") or match.group("path2")
    if not branch or not path:
        return None
    return branch, Path(path).expanduser().resolve(strict=False)


def _git_status(path: Path) -> tuple[bool, str]:
    code, stdout, stderr = _run(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=all"]
    )
    if code != 0:
        return False, stderr or stdout
    return True, stdout


def _repo_root(path: Path) -> Path | None:
    code, stdout, _ = _run(["git", "-C", str(path), "rev-parse", "--show-toplevel"])
    if code != 0 or not stdout:
        return None
    return Path(stdout).expanduser().resolve(strict=False)


def _repair_collision(board: str, task: dict[str, Any], *, dry_run: bool) -> str | None:
    task_id = str(task.get("id") or "")
    detail = _task_detail(board, task_id)
    if not detail:
        return None
    task_row = detail.get("task") or {}
    if task_row.get("status") != "blocked":
        return None
    error = _latest_spawn_error(detail)
    collision = _collision(error)
    if collision is None:
        return None
    branch, occupied = collision
    repo = _repo_root(occupied)
    if repo is None or occupied == repo or not occupied.is_dir():
        return f"{task_id}: collision path is not a managed linked worktree"
    owner_id = occupied.name
    if not owner_id.startswith("t_"):
        return f"{task_id}: preserved worktree with non-task owner {occupied}"
    owner_detail = _task_detail(board, owner_id)
    owner_status = ((owner_detail or {}).get("task") or {}).get("status")
    if owner_status not in {"done", "archived", "failed", "cancelled"}:
        return f"{task_id}: preserved worktree {occupied} owned by status={owner_status!r}"
    ok, dirty = _git_status(occupied)
    if not ok:
        return f"{task_id}: could not inspect {occupied}: {dirty}"
    if dirty:
        return f"{task_id}: preserved dirty worktree {occupied}"
    if dry_run:
        return f"would remove clean worktree {occupied} for branch {branch} and unblock {task_id}"

    code, stdout, stderr = _run(
        ["git", "-C", str(repo), "worktree", "remove", str(occupied)]
    )
    if code != 0 or occupied.exists():
        return f"{task_id}: clean worktree removal failed: {stderr or stdout}"

    code, stdout, stderr = _hermes(
        "kanban",
        "--board",
        board,
        "unblock",
        task_id,
        "factory recovery: removed clean duplicate worktree; preserved branch and retried dispatch",
    )
    if code != 0:
        return f"{task_id}: unblock failed after cleanup: {stderr or stdout}"

    verify = _task_detail(board, task_id)
    status = ((verify or {}).get("task") or {}).get("status")
    if status not in {"ready", "todo"}:
        return f"{task_id}: cleanup succeeded but readback status is {status!r}"
    return f"recovered {task_id}: removed clean {occupied} and read back status={status}"


def recover(board: str, *, dry_run: bool = False) -> list[str]:
    changes = _repair_cron_pins(dry_run=dry_run)
    try:
        tasks = _json_command("kanban", "--board", board, "list", "--status", "blocked", "--json")
    except RuntimeError:
        return changes
    if not isinstance(tasks, list):
        return changes
    for task in tasks:
        if isinstance(task, dict):
            change = _repair_collision(board, task, dry_run=dry_run)
            if change:
                changes.append(change)
    return changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", default=os.environ.get("HERMES_FACTORY_BOARD", "minna"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        changes = recover(args.board, dry_run=args.dry_run)
    except Exception as exc:
        print(f"factory recovery failed: {exc}", file=sys.stderr)
        return 1
    if changes:
        print("\n".join(changes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
