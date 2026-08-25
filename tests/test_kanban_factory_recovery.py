"""Unit tests for the deterministic factory recovery add-on."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


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
