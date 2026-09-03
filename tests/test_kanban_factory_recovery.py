"""Unit tests for the deterministic factory recovery add-on."""

from __future__ import annotations

import os
import importlib.util
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import kanban_worker_reaper as reaper


SCRIPT = Path(__file__).parents[1] / "scripts" / "kanban_factory_recovery.py"
CRON_SHIM = Path(__file__).parents[1] / "scripts" / "kanban_factory_recovery_cron.py"


spec = importlib.util.spec_from_file_location("kanban_factory_recovery", SCRIPT)
assert spec is not None and spec.loader is not None
factory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factory)


def test_collision_parser_matches_git_worktree_error():
    value = factory._collision(
        "fatal: 'kanban/project-231' is already used by worktree at "
        "'/repo/.worktrees/t_parent'"
    )
    assert value is not None
    branch, path = value
    assert branch == "kanban/project-231"
    assert path == Path("/repo/.worktrees/t_parent")


def test_collision_parser_matches_alternate_git_wording():
    value = factory._collision(
        "branch kanban/project-231 is already checked out at /repo/.worktrees/t_parent"
    )
    assert value is not None
    assert value[0] == "kanban/project-231"
    assert value[1] == Path("/repo/.worktrees/t_parent")


def test_collision_parser_rejects_unrelated_errors():
    assert factory._collision("cargo test failed") is None


def test_parked_detection_uses_imported_label_metadata():
    assert factory._is_parked({"labels": ["slice-11", "parked"]})
    assert not factory._is_parked({"labels": ["slice-11"]})


def test_parked_detection_supports_imported_body_metadata():
    assert factory._is_parked({"body": "- Labels: enhancement, parked, slice-3"})
    assert not factory._is_parked({"body": "- Labels: enhancement, slice-3"})


def test_parked_acknowledgement_is_idempotent():
    assert not factory._parked_acknowledged({"comments": []})
    assert factory._parked_acknowledged(
        {"comments": [{"body": "[factory] parked backlog acknowledged: preserve state"}]}
    )


def test_cron_shim_requires_explicit_board_and_script():
    text = CRON_SHIM.read_text(encoding="utf-8")
    assert 'os.environ.get("HERMES_FACTORY_RECOVERY_SCRIPT")' in text
    assert 'os.environ.get("HERMES_FACTORY_BOARD")' in text
    assert "HERMES_FACTORY_RECOVERY_SCRIPT is required" in text
    assert "HERMES_FACTORY_BOARD is required" in text


def test_repo_root_resolves_git_common_dir_for_linked_worktree(monkeypatch):
    monkeypatch.setattr(
        factory,
        "_run",
        lambda argv, cwd=None: (0, "/repo/.git", "")
        if argv[-1] == "--git-common-dir"
        else (1, "", ""),
    )
    assert factory._repo_root(Path("/repo/.worktrees/t_done")) == Path("/repo")


def test_real_linked_worktree_reports_common_root_and_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "factory@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Factory Test"],
        check=True,
    )
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "test fixture"], check=True)
    linked = repo / ".worktrees" / "t_done"
    linked.parent.mkdir()
    subprocess.run(
        [
            "git", "-C", str(repo), "worktree", "add", "-q", "-b",
            "kanban/project-231", str(linked), "HEAD",
        ],
        check=True,
    )
    assert factory._repo_root(linked) == repo.resolve()
    assert factory._worktree_branch(linked) == "kanban/project-231"


def test_repair_preserves_collision_when_branch_does_not_match(monkeypatch, tmp_path):
    occupied = tmp_path / "t_owner"
    occupied.mkdir()
    task = {"id": "t_blocked", "status": "blocked"}
    detail = {
        "task": task,
        "runs": [{
            "status": "spawn_failed",
            "error": "fatal: 'kanban/project-231' is already used by worktree at "
            f"'{occupied}'",
        }],
    }
    monkeypatch.setattr(factory, "_task_detail", lambda board, task_id: detail)
    monkeypatch.setattr(factory, "_repo_root", lambda path: Path("/repo"))
    monkeypatch.setattr(factory, "_worktree_branch", lambda path: "kanban/other-231")
    result = factory._repair_collision("generic-board", task, dry_run=False)
    assert result is not None
    assert "does not match" in result


def _terminal_detail(task_id: str, *, current_run_id=None):
    return {
        "task": {
            "id": task_id,
            "status": "blocked",
            "current_run_id": current_run_id,
        }
    }


def _start_task_worker(task_id: str, board: str):
    # The child deliberately stays alive so the test proves process-group
    # cleanup, rather than merely observing the Hermes parent exit.
    script = (
        "import subprocess, time; "
        "subprocess.Popen(['sleep', '60']); "
        "time.sleep(60)"
    )
    env = os.environ.copy()
    env.update(
        {
            "HERMES_KANBAN_TASK": task_id,
            "HERMES_KANBAN_RUN_ID": "41",
            "HERMES_KANBAN_BOARD": board,
            "HERMES_KANBAN_DB": "/tmp/factory-reaper-test-board.db",
        }
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _wait_for_task_process(task_id: str, board: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        records = reaper.iter_process_records()
        if any(
            reaper.process_identity_matches(
                record,
                task_id=task_id,
                board=board,
                kanban_db="/tmp/factory-reaper-test-board.db",
            )
            for record in records
        ):
            return
        time.sleep(0.05)
    raise AssertionError(f"task worker {task_id} did not become visible in procfs")


def _kill_task_worker(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, 9)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
def test_terminal_blocked_task_reaps_worker_and_live_descendant():
    task_id = "t_reaper_terminal"
    board = "factory-reaper-test"
    process = _start_task_worker(task_id, board)
    try:
        _wait_for_task_process(task_id, board)
        detail = _terminal_detail(task_id)
        report = factory.reap_terminal_task_workers(
            detail,
            task_id=task_id,
            board=board,
            kanban_db="/tmp/factory-reaper-test-board.db",
            refresh=lambda: detail,
            grace_seconds=1,
        )
        assert report["status"] == "reaped"
        assert report["survivors"] == []
        process.wait(timeout=5)
    finally:
        _kill_task_worker(process)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux procfs")
def test_reaper_does_not_kill_worker_for_active_run_or_board_mismatch():
    active_id = "t_reaper_active"
    active = _start_task_worker(active_id, "factory-reaper-test")
    mismatched_id = "t_reaper_mismatch"
    mismatched = _start_task_worker(mismatched_id, "other-board")
    try:
        _wait_for_task_process(active_id, "factory-reaper-test")
        _wait_for_task_process(mismatched_id, "other-board")

        active_detail = _terminal_detail(active_id, current_run_id=41)
        active_report = factory.reap_terminal_task_workers(
            active_detail,
            task_id=active_id,
            board="factory-reaper-test",
            kanban_db="/tmp/factory-reaper-test-board.db",
            refresh=lambda: active_detail,
        )
        assert active_report["status"] == "not_applicable"
        assert active.poll() is None

        mismatch_detail = _terminal_detail(mismatched_id)
        mismatch_report = factory.reap_terminal_task_workers(
            mismatch_detail,
            task_id=mismatched_id,
            board="factory-reaper-test",
            kanban_db="/tmp/factory-reaper-test-board.db",
            refresh=lambda: mismatch_detail,
        )
        assert mismatch_report["status"] == "none"
        assert mismatched.poll() is None
    finally:
        _kill_task_worker(active)
        _kill_task_worker(mismatched)


def test_recover_runs_terminal_worker_reconciliation_for_blocked_tasks(monkeypatch):
    task = {"id": "t_blocked", "status": "blocked"}
    monkeypatch.setattr(factory, "_json_command", lambda *args: [task])
    monkeypatch.setattr(
        factory,
        "_reconcile_terminal_worker",
        lambda board, task, dry_run: "t_blocked: terminal worker reconciliation=reaped",
    )
    monkeypatch.setattr(factory, "_acknowledge_parked", lambda *args, **kwargs: None)
    monkeypatch.setattr(factory, "_repair_collision", lambda *args, **kwargs: None)
    assert factory.recover("factory-reaper-test", dry_run=False) == [
        "t_blocked: terminal worker reconciliation=reaped"
    ]


def test_cli_timeout_is_converted_to_bounded_failure(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(kwargs.get("args", args[0]), kwargs["timeout"])

    monkeypatch.setattr(factory.subprocess, "run", timeout)
    monkeypatch.setenv("HERMES_FACTORY_CLI_TIMEOUT_SECONDS", "7")

    assert factory._run(["hermes", "kanban"])[0:2] == (124, "")
    assert "7s" in factory._run(["hermes", "kanban"])[2]


def test_readonly_task_detail_fallback_uses_only_terminal_identity(tmp_path, monkeypatch):
    db = tmp_path / "kanban.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE tasks (id TEXT, status TEXT, current_run_id INTEGER, "
        "title TEXT, priority INTEGER, created_at INTEGER)"
    )
    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
        ("t_sqlite", "blocked", None, "blocked task", 1, 1),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    monkeypatch.setattr(
        factory,
        "_json_command",
        lambda *args: (_ for _ in ()).throw(RuntimeError("CLI unavailable")),
    )

    assert factory._task_detail("factory-reaper-test", "t_sqlite") == {
        "_readback": "sqlite",
        "task": {"id": "t_sqlite", "status": "blocked", "current_run_id": None},
    }
