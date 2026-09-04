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
    ppid: int | None
    pgrp: int | None
    session: int | None
    start_time: int | None
    state: str | None
    env: Mapping[str, str]
    env_readable: bool = True
    readable: bool = True


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
    env_readable = env is not None
    ppid, pgrp, session, start_time, state = parsed
    return ProcessRecord(
        pid=pid,
        ppid=ppid,
        pgrp=pgrp,
        session=session,
        start_time=start_time,
        state=state,
        env=env or {},
        env_readable=env_readable,
    )



def _unknown_process_record(pid: int) -> ProcessRecord:
    """Represent a numeric procfs entry whose identity could not be read."""

    return ProcessRecord(
        pid=pid,
        ppid=None,
        pgrp=None,
        session=None,
        start_time=None,
        state=None,
        env={},
        env_readable=False,
        readable=False,
    )


def iter_process_records(*, proc_root: Path = PROC_ROOT) -> list[ProcessRecord]:
    """Return a snapshot, retaining unreadable numeric entries as unknowns."""

    if not proc_root.is_dir():
        return [_unknown_process_record(0)]
    records: list[ProcessRecord] = []
    try:
        entries = list(proc_root.iterdir())
    except (FileNotFoundError, PermissionError, OSError):
        return [_unknown_process_record(0)]
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
        except ValueError:
            records.append(_unknown_process_record(0))
            continue
        record = read_process_record(pid, proc_root=proc_root)
        records.append(record if record is not None else _unknown_process_record(pid))
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

    if not record.env_readable:
        return False
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
    if not isinstance(detail, Mapping):
        return {}
    nested = detail.get("task")
    return nested if isinstance(nested, Mapping) else detail



def _task_identity_reason(detail: Mapping[str, Any], task_id: str) -> str | None:
    row = _task_row(detail)
    value = row.get("id")
    if not isinstance(value, str) or not value:
        return "task readback identity is missing"
    if value != task_id:
        return "task readback identity does not match requested task"
    return None


def _terminal_reason_for_task(
    detail: Mapping[str, Any], *, task_id: str | None
) -> str | None:
    if task_id is not None:
        identity_reason = _task_identity_reason(detail, task_id)
        if identity_reason:
            return identity_reason
    row = _task_row(detail)
    status = str(row.get("status") or "").strip().lower()
    if status not in TERMINAL_TASK_STATES:
        return f"task status={status or 'unknown'} is not terminal"
    if "current_run_id" not in row:
        return "task current run state is missing"
    current_run = row["current_run_id"]
    if current_run not in (None, ""):
        return "task still has a current run"
    return None



def _terminal_reason(detail: Mapping[str, Any]) -> str | None:
    return _terminal_reason_for_task(detail, task_id=None)


def _record_is_readable(record: ProcessRecord) -> bool:
    return (
        isinstance(record, ProcessRecord)
        and record.readable
        and isinstance(record.pid, int)
        and record.pid > 0
        and isinstance(record.ppid, int)
        and isinstance(record.pgrp, int)
        and isinstance(record.session, int)
        and isinstance(record.start_time, int)
        and isinstance(record.state, str)
        and isinstance(record.env, Mapping)
    )


def _validated_groups(
    records: Iterable[ProcessRecord],
    *,
    task_id: str,
    board: str,
    kanban_db: str | os.PathLike[str] | None,
    current_pid: int,
) -> tuple[list[ProcessGroup], list[str]]:
    all_records = list(records)
    unreadable = [
        record
        for record in all_records
        if not isinstance(record, ProcessRecord) or not _record_is_readable(record)
    ]
    if unreadable:
        first = unreadable[0]
        pid = getattr(first, "pid", "unknown")
        return [], [f"process enumeration contains unreadable record {pid}"]
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
    try:
        own_group = os.getpgrp()
    except (ProcessLookupError, PermissionError, OSError):
        return [], ["could not determine reaper process group"]
    try:
        own_session = os.getsid(current_pid)
    except (ProcessLookupError, PermissionError, OSError):
        return [], ["could not determine reaper session"]
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

    if set(group.pids) != set(group.start_times):
        return False, f"process group {group.pgrp} has an incomplete identity snapshot"
    current = {
        pid: read_process_record(pid, proc_root=proc_root) for pid in group.pids
    }
    for pid in group.pids:
        expected_start = group.start_times[pid]
        record = current.get(pid)
        if record is None:
            return False, f"pid {pid} could not be read during revalidation"
        if not _record_is_readable(record) or record.pid != pid:
            return False, f"pid {pid} is unreadable during revalidation"
        if record.start_time != expected_start:
            return False, f"pid {pid} changed start time"
        if record.session != group.session:
            return False, f"pid {pid} changed session"
        if record.pgrp != group.pgrp:
            return False, f"pid {pid} changed process group"
        if not process_identity_matches(
            record, task_id=task_id, board=board, kanban_db=kanban_db
        ):
            return False, f"pid {pid} changed task identity"
    # A new, unbound member in the same session/group is a fail-closed race.
    enumerated = iter_process_records(proc_root=proc_root)
    if any(not _record_is_readable(record) for record in enumerated):
        return False, "process enumeration is incomplete during revalidation"
    enumerated_by_pid = {record.pid for record in enumerated}
    missing = [pid for pid in group.pids if pid not in enumerated_by_pid]
    if missing:
        return False, f"process enumeration missed captured pid {missing[0]}"
    for record in enumerated:
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
    def proc_entry_present(pid: int) -> bool:
        try:
            (proc_root / str(pid)).stat()
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return True

    live: list[int] = []
    for pid, expected_start in group.start_times.items():
        record = read_process_record(pid, proc_root=proc_root)
        if record is None:
            if proc_entry_present(pid):
                live.append(pid)
            continue
        if not _record_is_readable(record) or record.pid != pid:
            live.append(pid)
            continue
        if record.state == "Z":
            continue
        if record.start_time != expected_start:
            live.append(pid)
            continue
        # Once a group has been signalled, a surviving process remains a
        # survivor even if it clears or makes its environment unreadable. The
        # next destructive signal is separately guarded by
        # ``_snapshot_still_bound``; do not misreport an identity-changing
        # survivor as exited.
        live.append(pid)
    return live



def _refresh_task_reason(
    refresh: Callable[[], Mapping[str, Any] | None],
    *,
    task_id: str,
    signal_name: str,
) -> str | None:
    try:
        latest = refresh()
    except Exception:
        return f"task readback failed before {signal_name}"
    if latest is None:
        return f"task readback disappeared before {signal_name}"
    return _terminal_reason_for_task(latest, task_id=task_id)


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
    refresh: Callable[[], Mapping[str, Any] | None],
) -> tuple[list[int], list[int], list[str]]:
    group_list = list(groups)
    errors: list[str] = []
    term_sent: list[int] = []
    term_authorized: list[ProcessGroup] = []
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
        task_reason = _refresh_task_reason(
            refresh, task_id=task_id, signal_name="SIGTERM"
        )
        if task_reason:
            errors.append(task_reason)
            continue
        try:
            killpg(group.pgrp, signal.SIGTERM)
            term_sent.append(group.pgrp)
            term_authorized.append(group)
        except ProcessLookupError:
            # The group already ended; this is an idempotent success.
            pass
        except PermissionError:
            errors.append(f"permission denied for process group {group.pgrp}")
        except OSError as exc:
            errors.append(f"signal failed for process group {group.pgrp}: {exc.errno}")

    deadline = monotonic() + max(0.0, grace_seconds)
    # Only groups that passed pre-SIGTERM validation and received SIGTERM are
    # authorized to reach the force-kill phase.
    remaining_groups = term_authorized
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
        task_reason = _refresh_task_reason(
            refresh, task_id=task_id, signal_name="SIGKILL"
        )
        if task_reason:
            errors.append(task_reason)
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
    identity_reason = _task_identity_reason(detail, task_id)
    if identity_reason:
        return {"status": "skipped", "task_id": task_id, "reason": identity_reason}
    initial_reason = _terminal_reason_for_task(detail, task_id=task_id)
    if initial_reason:
        return {"status": "not_applicable", "task_id": task_id, "reason": initial_reason}
    if not proc_root.is_dir():
        return {
            "status": "unsupported",
            "task_id": task_id,
            "reason": "procfs is unavailable; no process-name fallback is permitted",
        }
    if not dry_run and refresh is None:
        return {
            "status": "skipped",
            "task_id": task_id,
            "reason": "task refresh is required before cleanup",
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

    try:
        latest = refresh() if refresh is not None else detail
    except Exception:
        return {
            "status": "skipped",
            "task_id": task_id,
            "reason": "task readback failed before cleanup",
        }
    if latest is None:
        return {
            "status": "skipped",
            "task_id": task_id,
            "reason": "task readback disappeared before cleanup",
        }
    latest_reason = _terminal_reason_for_task(latest, task_id=task_id)
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

    assert refresh is not None
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
        refresh=refresh,
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
