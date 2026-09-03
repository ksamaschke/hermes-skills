#!/usr/bin/env python3
"""Safely reconcile task-owned Hermes workers after terminal Kanban handoff.

Kanban workers are launched in a new POSIX session and inherit a small set of
identity environment variables.  A worker can therefore outlive the board run
that launched it (for example after ``kanban_block``), while a descendant such
as ``sleep`` remains alive.  This module only reaps a process group when the
current task readback is terminal, the process identity is exact, and every
member of the target group carries the same task/board identity.

The implementation is deliberately dependency-free and Linux/POSIX-focused.
On platforms without ``/proc`` it returns an explicit unsupported result rather
than falling back to broad process-name matching.
"""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


PROC_ROOT = Path("/proc")
TASK_ENV = "HERMES_KANBAN_TASK"
RUN_ENV = "HERMES_KANBAN_RUN_ID"
BOARD_ENV = "HERMES_KANBAN_BOARD"
DB_ENV = "HERMES_KANBAN_DB"
IDENTITY_ENV_KEYS = frozenset({TASK_ENV, RUN_ENV, BOARD_ENV, DB_ENV})
TERMINAL_TASK_STATES = frozenset(
    {"archived", "blocked", "cancelled", "done", "failed", "review"}
)


@dataclass(frozen=True)
class ProcessRecord:
    """Small, non-sensitive process snapshot used for identity checks."""

    pid: int
    ppid: int
    pgrp: int
    session: int
    start_time: int
    state: str
    env: Mapping[str, str]


@dataclass(frozen=True)
class ProcessGroup:
    """A validated task-owned process group ready for bounded termination."""

    session: int
    pgrp: int
    pids: tuple[int, ...]
    start_times: Mapping[int, int]



def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, OSError):
        return None



def _read_environ(path: Path) -> dict[str, str] | None:
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    values: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        key_bytes, value_bytes = item.split(b"=", 1)
        try:
            key = key_bytes.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - defensive for unusual procfs
            continue
        if key not in IDENTITY_ENV_KEYS:
            continue
        values[key] = value_bytes.decode("utf-8", errors="replace")
    return values



def _parse_stat(raw: str) -> tuple[int, int, int, int, str] | None:
    """Parse selected ``/proc/<pid>/stat`` fields.

    The command name is parenthesized and may itself contain ``)``; using the
    final closing parenthesis keeps the field offsets stable for such names.
    Fields after ``comm`` start at field 3, so starttime (field 22) is index 19.
    """

    end = raw.rfind(")")
    if end < 0:
        return None
    fields = raw[end + 2 :].split()
    if len(fields) <= 19:
        return None
    try:
        state = fields[0]
        ppid = int(fields[1])
        pgrp = int(fields[2])
        session = int(fields[3])
        start_time = int(fields[19])
    except (TypeError, ValueError):
        return None
    return ppid, pgrp, session, start_time, state



def read_process_record(pid: int, *, proc_root: Path = PROC_ROOT) -> ProcessRecord | None:
    """Read one process without exposing its command line or unrelated env."""

    if pid <= 0:
        return None
    process_dir = proc_root / str(pid)
    stat = _read_text(process_dir / "stat")
    if stat is None:
        return None
    parsed = _parse_stat(stat)
    if parsed is None:
        return None
    env = _read_environ(process_dir / "environ")
    if env is None:
        return None
    ppid, pgrp, session, start_time, state = parsed
    return ProcessRecord(
        pid=pid,
        ppid=ppid,
        pgrp=pgrp,
        session=session,
        start_time=start_time,
        state=state,
        env=env,
    )



def iter_process_records(*, proc_root: Path = PROC_ROOT) -> list[ProcessRecord]:
    """Return a bounded snapshot of readable processes on a procfs host."""

    if not proc_root.is_dir():
        return []
    records: list[ProcessRecord] = []
    try:
        entries = list(proc_root.iterdir())
    except (FileNotFoundError, PermissionError, OSError):
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        record = read_process_record(int(entry.name), proc_root=proc_root)
        if record is not None:
            records.append(record)
    return records



def _normalise_path(value: str | os.PathLike[str] | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(Path(text).expanduser().resolve(strict=False))
    except (OSError, RuntimeError, ValueError):
        return text



def process_identity_matches(
    record: ProcessRecord,
    *,
    task_id: str,
    board: str,
    kanban_db: str | os.PathLike[str] | None = None,
) -> bool:
    """Require task identity plus at least one exact board binding."""

    if record.env.get(TASK_ENV, "").strip() != task_id:
        return False
    expected_board = board.strip()
    actual_board = record.env.get(BOARD_ENV, "").strip()
    expected_db = _normalise_path(kanban_db)
    actual_db = _normalise_path(record.env.get(DB_ENV))

    # A configured board must be present and exact.  If callers do not have a
    # board slug, an exact shared DB path is the alternative binding.
    if expected_board:
        if actual_board != expected_board:
            return False
    elif expected_db is None or actual_db != expected_db:
        return False

    if expected_db is not None and actual_db is not None and actual_db != expected_db:
        return False
    return bool(actual_board or actual_db)



def _task_row(detail: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = detail.get("task")
    return nested if isinstance(nested, Mapping) else detail



def _terminal_reason(detail: Mapping[str, Any]) -> str | None:
    row = _task_row(detail)
    status = str(row.get("status") or "").strip().lower()
    if status not in TERMINAL_TASK_STATES:
        return f"task status={status or 'unknown'} is not terminal"
    current_run = row.get("current_run_id")
    if current_run not in (None, ""):
        return "task still has a current run"
    return None



def _validated_groups(
    records: Iterable[ProcessRecord],
    *,
    task_id: str,
    board: str,
    kanban_db: str | os.PathLike[str] | None,
    current_pid: int,
) -> tuple[list[ProcessGroup], list[str]]:
    all_records = list(records)
    matching = [
        record
        for record in all_records
        if process_identity_matches(
            record, task_id=task_id, board=board, kanban_db=kanban_db
        )
    ]
    if not matching:
        return [], []

    by_session: dict[int, list[ProcessRecord]] = {}
    for record in all_records:
        if any(record.session == match.session for match in matching):
            by_session.setdefault(record.session, []).append(record)

    groups: list[ProcessGroup] = []
    unsafe: list[str] = []
    own_group = os.getpgrp()
    try:
        own_session = os.getsid(current_pid)
    except (ProcessLookupError, PermissionError, OSError):
        own_session = -1
    for session, members in by_session.items():
        if session <= 1 or session == own_session:
            unsafe.append(f"session {session} is not an isolated worker session")
            continue
        if any(
            not process_identity_matches(
                member, task_id=task_id, board=board, kanban_db=kanban_db
            )
            for member in members
        ):
            unsafe.append(f"session {session} contains an unbound process")
            continue

        by_group: dict[int, list[ProcessRecord]] = {}
        for member in members:
            by_group.setdefault(member.pgrp, []).append(member)
        for pgrp, group_members in by_group.items():
            if pgrp <= 1 or pgrp == own_group:
                unsafe.append(f"process group {pgrp} is not safely killable")
                continue
            groups.append(
                ProcessGroup(
                    session=session,
                    pgrp=pgrp,
                    pids=tuple(sorted(member.pid for member in group_members)),
                    start_times={member.pid: member.start_time for member in group_members},
                )
            )
    return groups, unsafe



def _snapshot_still_bound(
    group: ProcessGroup,
    *,
    task_id: str,
    board: str,
    kanban_db: str | os.PathLike[str] | None,
    proc_root: Path,
) -> tuple[bool, str | None]:
    """Reject PID reuse or a changed process identity before signalling."""

    current = {
        pid: read_process_record(pid, proc_root=proc_root) for pid in group.pids
    }
    for pid, expected_start in group.start_times.items():
        record = current.get(pid)
        if record is None:
            continue
        if record.start_time != expected_start:
            return False, f"pid {pid} changed start time"
        if not process_identity_matches(
            record, task_id=task_id, board=board, kanban_db=kanban_db
        ):
            return False, f"pid {pid} changed task identity"
    # A new, unbound member in the same session/group is a fail-closed race.
    for record in iter_process_records(proc_root=proc_root):
        if record.session == group.session and record.pgrp == group.pgrp:
            if not process_identity_matches(
                record, task_id=task_id, board=board, kanban_db=kanban_db
            ):
                return False, f"process group {group.pgrp} gained an unbound member"
    return True, None



def _live_pids(
    group: ProcessGroup,
    *,
    proc_root: Path,
    task_id: str,
    board: str,
    kanban_db: str | os.PathLike[str] | None,
) -> list[int]:
    live: list[int] = []
    for pid, expected_start in group.start_times.items():
        record = read_process_record(pid, proc_root=proc_root)
        if record is None or record.state == "Z":
            continue
        if record.start_time != expected_start:
            continue
        if process_identity_matches(
            record, task_id=task_id, board=board, kanban_db=kanban_db
        ):
            live.append(pid)
    return live



def _terminate_groups(
    groups: Iterable[ProcessGroup],
    *,
    task_id: str,
    board: str,
    kanban_db: str | os.PathLike[str] | None,
    proc_root: Path,
    grace_seconds: float,
    killpg: Callable[[int, int], None],
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> tuple[list[int], list[int], list[str]]:
    group_list = list(groups)
    errors: list[str] = []
    term_sent: list[int] = []
    for group in group_list:
        safe, reason = _snapshot_still_bound(
            group,
            task_id=task_id,
            board=board,
            kanban_db=kanban_db,
            proc_root=proc_root,
        )
        if not safe:
            errors.append(reason or f"process group {group.pgrp} failed revalidation")
            continue
        try:
            killpg(group.pgrp, signal.SIGTERM)
            term_sent.append(group.pgrp)
        except ProcessLookupError:
            # The group already ended; this is an idempotent success.
            pass
        except PermissionError:
            errors.append(f"permission denied for process group {group.pgrp}")
        except OSError as exc:
            errors.append(f"signal failed for process group {group.pgrp}: {exc.errno}")

    deadline = monotonic() + max(0.0, grace_seconds)
    remaining_groups = group_list
    while remaining_groups and monotonic() < deadline:
        remaining_groups = [
            group
            for group in remaining_groups
            if _live_pids(
                group,
                proc_root=proc_root,
                task_id=task_id,
                board=board,
                kanban_db=kanban_db,
            )
        ]
        if remaining_groups:
            sleep(min(0.05, max(0.0, deadline - monotonic())))

    kill_sent: list[int] = []
    for group in remaining_groups:
        safe, reason = _snapshot_still_bound(
            group,
            task_id=task_id,
            board=board,
            kanban_db=kanban_db,
            proc_root=proc_root,
        )
        if not safe:
            errors.append(reason or f"process group {group.pgrp} failed kill revalidation")
            continue
        try:
            killpg(group.pgrp, signal.SIGKILL)
            kill_sent.append(group.pgrp)
        except ProcessLookupError:
            pass
        except PermissionError:
            errors.append(f"permission denied for process group {group.pgrp}")
        except OSError as exc:
            errors.append(f"kill failed for process group {group.pgrp}: {exc.errno}")

    if kill_sent:
        deadline = monotonic() + min(2.0, max(0.2, grace_seconds))
        while monotonic() < deadline:
            still_live = [
                pid
                for group in group_list
                for pid in _live_pids(
                    group,
                    proc_root=proc_root,
                    task_id=task_id,
                    board=board,
                    kanban_db=kanban_db,
                )
            ]
            if not still_live:
                break
            sleep(0.05)

    survivors = [
        pid
        for group in group_list
        for pid in _live_pids(
            group,
            proc_root=proc_root,
            task_id=task_id,
            board=board,
            kanban_db=kanban_db,
        )
    ]
    return term_sent + kill_sent, survivors, errors



def reap_terminal_task_workers(
    detail: Mapping[str, Any],
    *,
    task_id: str,
    board: str,
    kanban_db: str | os.PathLike[str] | None = None,
    refresh: Callable[[], Mapping[str, Any] | None] | None = None,
    dry_run: bool = False,
    grace_seconds: float = 2.0,
    proc_root: Path = PROC_ROOT,
    current_pid: int | None = None,
    killpg: Callable[[int, int], None] = os.killpg,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Reap only workers whose task is terminal and has no current run.

    ``refresh`` is called after the initial process scan and immediately before
    signalling.  A task that is reclaimed or re-dispatched during the scan is
    therefore left untouched.  The returned fields are deliberately bounded
    and contain no command lines or arbitrary environment values.
    """

    task_id = task_id.strip()
    if not task_id:
        return {"status": "skipped", "task_id": task_id, "reason": "empty task id"}
    initial_reason = _terminal_reason(detail)
    if initial_reason:
        return {"status": "not_applicable", "task_id": task_id, "reason": initial_reason}
    row_task_id = str(_task_row(detail).get("id") or "").strip()
    if row_task_id and row_task_id != task_id:
        return {
            "status": "skipped",
            "task_id": task_id,
            "reason": "task readback identity does not match requested task",
        }
    if not proc_root.is_dir():
        return {
            "status": "unsupported",
            "task_id": task_id,
            "reason": "procfs is unavailable; no process-name fallback is permitted",
        }

    pid = current_pid if current_pid is not None else os.getpid()
    records = iter_process_records(proc_root=proc_root)
    groups, unsafe = _validated_groups(
        records,
        task_id=task_id,
        board=board,
        kanban_db=kanban_db,
        current_pid=pid,
    )
    if not groups:
        result: dict[str, Any] = {"status": "none", "task_id": task_id}
        if unsafe:
            result.update({"status": "unsafe", "reason": "; ".join(unsafe[:3])})
        return result

    latest = refresh() if refresh is not None else detail
    if latest is None:
        return {
            "status": "skipped",
            "task_id": task_id,
            "reason": "task readback disappeared before cleanup",
        }
    latest_reason = _terminal_reason(latest)
    if latest_reason:
        return {"status": "skipped", "task_id": task_id, "reason": latest_reason}

    # Re-scan after the task readback.  This closes the most important race and
    # also drops a worker that ended naturally while the board was read.
    records = iter_process_records(proc_root=proc_root)
    groups, unsafe_after_refresh = _validated_groups(
        records,
        task_id=task_id,
        board=board,
        kanban_db=kanban_db,
        current_pid=pid,
    )
    unsafe.extend(unsafe_after_refresh)
    if not groups:
        result = {"status": "none", "task_id": task_id}
        if unsafe:
            result.update({"status": "unsafe", "reason": "; ".join(unsafe[:3])})
        return result

    pids = sorted({pid for group in groups for pid in group.pids})
    groups_payload = [group.pgrp for group in groups]
    if dry_run:
        return {
            "status": "would_reap",
            "task_id": task_id,
            "pids": pids,
            "process_groups": groups_payload,
        }

    signalled, survivors, errors = _terminate_groups(
        groups,
        task_id=task_id,
        board=board,
        kanban_db=kanban_db,
        proc_root=proc_root,
        grace_seconds=grace_seconds,
        killpg=killpg,
        sleep=sleep,
        monotonic=monotonic,
    )
    result = {
        "status": "reaped" if not survivors and not errors else "partial",
        "task_id": task_id,
        "pids": pids,
        "process_groups": groups_payload,
        "signalled_groups": signalled,
        "survivors": survivors,
    }
    if errors or unsafe:
        result["reason"] = "; ".join((unsafe + errors)[:3])
    return result
