"""Regression tests for the external review-dispatch recovery add-on."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "kanban_review_successor_recovery.py"
spec = importlib.util.spec_from_file_location("review_successor_recovery", SCRIPT)
assert spec is not None and spec.loader is not None
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


VALID_BODY = """review_type: read-only adversarial code review leaf
implementation_task: t_impl
source_issue: https://forgejo.example/issues/42
target_repository: ai-gateway
target_worktree: /repo/.worktrees/t_impl
branch: review/t_impl
candidate_commit: abcdef0123456789abcdef0123456789abcdef01
implementer_profile: implementer (OpenAI family)
reviewer_profile: reviewer (Claude family)
vendor_family_independent: true
read_only_source: true
max_runtime_seconds: 600
max_retries: 1
allowed_verdicts: APPROVED, CHANGES_REQUESTED, REVIEW-INCOMPLETE
review_lens: fail-closed review evidence
exact_scope:
- tools/validator.py
one_acceptance_question: Does the validator reject incomplete approval evidence?
focused_checks:
- python3 -m unittest tools.test_validator
- git diff --check
stop_condition: stop after the named file, one question, focused checks, and mutation readback.
"""

VALID_TASK = {
    "id": "t_review",
    "title": "Review leaf Forgejo #42: validator",
    "body": VALID_BODY,
    "assignee": "reviewer",
    "status": "ready",
    "workspace_path": "/repo/.worktrees/t_impl",
    "max_runtime_seconds": 600,
    "max_retries": 1,
}


BROAD_TASK = {
    "id": "t_broad",
    "title": "Fresh independent review: Forgejo #42 broad packet",
    "body": """review_type: read-only adversarial code review leaf
implementation_task: t_impl
source_issue: https://forgejo.example/issues/42
target_repository: ai-gateway
target_worktree: /repo/.worktrees/t_impl
branch: review/t_impl
candidate_commit: abcdef0123456789abcdef0123456789abcdef01
implementer_profile: implementer (OpenAI family)
reviewer_profile: reviewer (Claude family)
vendor_family_independent: true
read_only_source: true
max_runtime_seconds: 600
review_lens: broad approval evidence
exact_scope:
- file-01.py
- file-02.py
- file-03.py
- file-04.py
- file-05.py
- file-06.py
- file-07.py
Acceptance questions to answer:
1. Does the validator reject incomplete approval evidence?
2. Does the fan-in preserve the current successor frontier?
focused_checks:
- python3 -m unittest
stop_condition: stop at the named scope and questions.
""",
    "assignee": "reviewer",
    "status": "ready",
    "workspace_path": "/repo/.worktrees/t_impl",
    "max_runtime_seconds": 600,
    "max_retries": None,
}


LEGACY_LIVE_SHAPE_TASK = {
    "id": "t_legacy",
    "title": "Fresh independent review: Forgejo #8 trusted catalog",
    "body": """Review type: fresh read-only adversarial code/config review.
Implementation task: t_impl. Source issue: Forgejo #8 — https://forgejo.example/issues/8.
Target repository/worktree: /repo/.worktrees/t_impl.
Branch: review/t_impl. Candidate is HEAD abcdef0123456789abcdef0123456789abcdef01 plus the current candidate delta.
Implementer: implementer. Reviewer: reviewer (independent vendor-family review).

## Exact scope
- tools/validator.py

## Review lens and acceptance questions
Lens: fail-closed validation.
1. Does the validator reject incomplete approval evidence?

## Focused checks
- python3 -m unittest tools.test_validator

Read-only source: true.
""",
    "assignee": "reviewer",
    "status": "ready",
    "workspace_path": "/repo/.worktrees/t_impl",
    "max_runtime_seconds": None,
    "max_retries": None,
}


def test_valid_packet_has_a_durable_bounded_contract():
    packet = recovery.parse_review_packet(VALID_TASK)

    assert recovery.validate_review_packet(VALID_TASK) == []
    assert packet["files"] == ["tools/validator.py"]
    assert packet["questions"] == ["Does the validator reject incomplete approval evidence?"]
    assert packet["review_lens"] == "fail-closed review evidence"
    assert packet["max_runtime_seconds"] == 600
    assert packet["max_retries"] == 1


def test_explicit_false_independence_is_not_overridden_by_prose():
    task = {
        **VALID_TASK,
        "body": VALID_BODY.replace(
            "vendor_family_independent: true",
            "vendor_family_independent: false\n\nThis is an independent vendor-family review.",
        ),
    }

    packet = recovery.parse_review_packet(task)

    assert packet["vendor_family_independent"] is False
    assert any("vendor-family independence" in error for error in recovery.validate_review_packet(task))


def test_ordinal_inside_an_authored_question_is_preserved():
    for question in (
        "Does step 3. get validated before dispatch?",
        "After RFC 7231 5. is the header honored?",
        "Does section 2) cover the retry path?",
    ):
        task = {
            **VALID_TASK,
            "body": VALID_BODY.replace(
                "Does the validator reject incomplete approval evidence?",
                question,
            ),
        }
        assert recovery.parse_review_packet(task)["questions"] == [question]


def test_inline_numbered_questions_are_rejected_as_multi_question_scope():
    task = {
        **VALID_TASK,
        "body": VALID_BODY.replace(
            "one_acceptance_question: Does the validator reject incomplete approval evidence?",
            "one_acceptance_question: 1) Does the validator reject incomplete approval evidence? 2) Does it preserve the verdict?",
        ),
    }

    assert recovery.parse_review_packet(task)["questions"] == [
        "Does the validator reject incomplete approval evidence?",
        "Does it preserve the verdict?",
    ]
    assert any("exactly one acceptance question" in error for error in recovery.validate_review_packet(task))


def test_legacy_fresh_packet_is_classified_but_missing_durable_fields_are_rejected():
    packet = recovery.parse_review_packet(LEGACY_LIVE_SHAPE_TASK)

    assert recovery.is_review_leaf(LEGACY_LIVE_SHAPE_TASK)
    assert packet["candidate_commit"] == "abcdef0123456789abcdef0123456789abcdef01"
    assert packet["branch"] == "review/t_impl"
    assert packet["implementer_profile"] == "implementer"
    assert packet["reviewer_profile"] == "reviewer"
    assert packet["vendor_family_independent"] is True
    assert packet["questions"] == ["Does the validator reject incomplete approval evidence?"]
    assert any("durable task max_retries" in error for error in recovery.validate_review_packet(LEGACY_LIVE_SHAPE_TASK))


def test_guard_rejects_broad_packet_and_missing_retry_override():
    errors = recovery.validate_review_packet(BROAD_TASK)

    assert any("one acceptance question" in error for error in errors)
    assert any("at most" in error and "files" in error for error in errors)
    assert any("max_retries" in error for error in errors)


def test_guard_only_selects_dispatchable_review_leaves():
    rows = [
        VALID_TASK,
        BROAD_TASK,
        {**BROAD_TASK, "id": "t_blocked", "status": "blocked"},
        {"id": "t_fanin", "title": "Review synthesis", "body": "review_type: bounded fan-in", "status": "ready"},
        {"id": "t_impl", "title": "Implementation", "body": "", "status": "ready"},
    ]

    candidates = recovery.dispatchable_review_packets(rows)

    assert [row["id"] for row, _ in candidates] == ["t_broad"]
    assert candidates[0][1]


def test_guard_can_supply_durable_budget_fields_missing_from_list_json():
    list_row = {key: value for key, value in VALID_TASK.items() if key != "max_runtime_seconds"}

    assert recovery.dispatchable_review_packets(
        [list_row],
        durable_loader=lambda task_id: {"max_runtime_seconds": 600, "max_retries": 1},
    ) == []


def test_durable_task_fields_read_the_board_row_without_writing(tmp_path, monkeypatch):
    database = tmp_path / "kanban.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, max_runtime_seconds INTEGER, max_retries INTEGER)"
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?, ?, ?)",
            ("t_review", 600, 1),
        )
        connection.commit()
    monkeypatch.setattr(recovery, "_board_db_path", lambda board: database)

    assert recovery._durable_task_fields("board", "t_review") == {
        "max_runtime_seconds": 600,
        "max_retries": 1,
    }


def test_timeout_terminal_state_overrides_summary_and_comment():
    detail = {
        "task": VALID_TASK,
        "runs": [
            {
                "id": 1,
                "status": "done",
                "outcome": "completed",
                "metadata": {"verdict": "APPROVED"},
                "started_at": 1000,
                "ended_at": 1100,
            },
            {
                "id": 2,
                "status": "timed_out",
                "outcome": "timed_out",
                "summary": "Partial result: APPROVED, but the worker timed out.",
                "started_at": 1200,
                "ended_at": None,
            },
        ],
        "comments": [{"author": "reviewer", "body": "CHANGES_REQUESTED: partial finding"}],
    }

    assert recovery.latest_timeout(detail)["id"] == 2
    assert recovery.review_verdict(detail) == "REVIEW-INCOMPLETE"


def test_newer_successful_run_supersedes_an_older_timeout():
    detail = {
        "task": VALID_TASK,
        "runs": [
            {"id": 1, "status": "timed_out", "outcome": "timed_out", "summary": "APPROVED", "started_at": 1000, "ended_at": 1100},
            {"id": 2, "status": "done", "outcome": "completed", "metadata": {"verdict": "APPROVED"}, "started_at": 1200, "ended_at": 1300},
        ],
        "comments": [],
    }

    assert recovery.latest_timeout(detail) is None
    assert recovery.review_verdict(detail) == "APPROVED"


def test_metadata_changes_requested_wins_over_a_stale_approval_comment():
    detail = {
        "task": VALID_TASK,
        "runs": [
            {
                "id": 3,
                "status": "done",
                "outcome": "completed",
                "metadata": {"review_outcome": "CHANGES_REQUESTED"},
                "started_at": 1200,
                "ended_at": 1300,
            },
        ],
        "comments": [{"author": "reviewer", "body": "APPROVED: stale older run"}],
    }

    assert recovery.review_verdict(detail) == "CHANGES_REQUESTED"


def test_active_newer_run_does_not_inherit_an_older_comment_verdict():
    detail = {
        "task": VALID_TASK,
        "runs": [
            {"id": 1, "status": "done", "outcome": "completed", "metadata": {"verdict": "APPROVED"}, "started_at": 1000, "ended_at": 1100},
            {"id": 2, "status": "running", "outcome": "running", "started_at": 1200, "ended_at": None},
        ],
        "comments": [{"author": "reviewer", "body": "APPROVED: old run"}],
    }

    assert recovery.review_verdict(detail) == ""


def test_reclaimed_or_blocked_terminal_runs_are_incomplete():
    for outcome in ("reclaimed", "blocked"):
        detail = {
            "task": VALID_TASK,
            "runs": [
                {"id": 1, "status": outcome, "outcome": outcome, "started_at": 1200, "ended_at": None, "summary": "APPROVED"},
            ],
            "comments": [{"author": "reviewer", "body": "APPROVED: stale comment"}],
        }
        assert recovery.review_verdict(detail) == "REVIEW-INCOMPLETE"


def test_successors_are_one_question_and_small_file_slices():
    packet = recovery.parse_review_packet(BROAD_TASK)
    specs = recovery.successor_specs(packet)

    assert specs
    assert len(specs) <= recovery.MAX_SUCCESSOR_SPECS
    assert all(len(spec["files"]) <= recovery.MAX_FILES_PER_SUCCESSOR for spec in specs)
    assert all(spec["question"] for spec in specs)
    assert len({spec["question"] for spec in specs}) == 2
    assert all(recovery.is_strict_successor(packet, spec) for spec in specs)
    assert all(set(spec["files"]) <= set(packet["files"]) for spec in specs)


def test_successors_preserve_a_compound_authored_question():
    question = "Does the validator enforce tenant isolation, rate limiting and audit logging?"
    task = {
        **VALID_TASK,
        "body": VALID_BODY.replace(
            "- tools/validator.py",
            "- tools/validator.py\n- tools/a.py\n- tools/b.py",
        ).replace(
            "Does the validator reject incomplete approval evidence?",
            question,
        ),
    }

    packet = recovery.parse_review_packet(task)
    specs = recovery.successor_specs(packet)

    assert specs
    assert {spec["question"] for spec in specs} == {question}


def test_multi_question_single_file_keeps_every_authored_question():
    task = {
        **BROAD_TASK,
        "body": BROAD_TASK["body"].replace(
            "- file-02.py\n- file-03.py\n- file-04.py\n- file-05.py\n- file-06.py\n- file-07.py\n",
            "",
        ),
    }

    packet = recovery.parse_review_packet(task)
    specs = recovery.successor_specs(packet)

    assert [spec["question"] for spec in specs] == packet["questions"]
    assert all(recovery.is_strict_successor(packet, spec) for spec in specs)


def test_successor_fanout_is_bounded_without_partial_coverage():
    files = "\n".join(f"- tools/file_{index:02d}.py" for index in range(17))
    task = {**VALID_TASK, "body": VALID_BODY.replace("- tools/validator.py", files)}

    assert recovery.successor_specs(recovery.parse_review_packet(task)) == []


def test_successor_body_round_trips_through_the_packet_guard():
    packet = recovery.parse_review_packet(BROAD_TASK)
    spec = recovery.successor_specs(packet)[0]
    body = recovery.successor_body(
        packet,
        {"id": 7, "status": "timed_out", "outcome": "timed_out"},
        spec,
        original_task_id=packet["task_id"],
    )
    successor = {
        "id": "t_successor",
        "title": "Review continuation",
        "body": body,
        "assignee": packet["reviewer_profile"],
        "status": "ready",
        "workspace_path": packet["target_worktree"],
        "max_runtime_seconds": 600,
        "max_retries": 1,
    }

    assert recovery.validate_review_packet(successor) == []
    assert recovery.parse_review_packet(successor)["questions"] == [spec["question"]]


def test_successor_preserves_all_authored_focused_checks():
    body = VALID_BODY.replace(
        "- git diff --check",
        "- git diff --check\n- python3 -m unittest tools.test_validator --verbose",
    )
    task = {**VALID_TASK, "body": body}
    packet = recovery.parse_review_packet(task)
    broad_packet = {**packet, "files": ["tools/a.py", "tools/b.py", "tools/c.py"]}

    spec = recovery.successor_specs(broad_packet)[0]
    successor_body = recovery.successor_body(
        broad_packet,
        {"id": 7, "status": "timed_out", "outcome": "timed_out"},
        spec,
        original_task_id="t_review",
    )

    assert spec["checks"] == packet["checks"]
    assert "python3 -m unittest tools.test_validator --verbose" in successor_body


def test_successor_recovery_does_not_stop_at_a_fixed_depth():
    packet = recovery.parse_review_packet(
        {
            **BROAD_TASK,
            "id": "t_depth_three",
            "body": BROAD_TASK["body"] + "continuation_depth: 3\n",
        }
    )

    assert recovery.successor_specs(packet)


def test_atomic_timed_out_leaf_has_no_fake_infinite_successor():
    packet = recovery.parse_review_packet(
        {
            **VALID_TASK,
            "id": "t_atomic",
            "body": VALID_BODY.replace("max_retries: 1", "max_retries: 1\ncontinuation_depth: 3"),
        }
    )

    assert recovery.successor_specs(packet) == []


def test_fan_in_follows_the_recursive_successor_frontier():
    fanin = {
        "id": "t_fanin",
        "body": "review_type: bounded fan-in\nleaf_tasks: t_old, t_done\n",
    }
    rows = [
        {
            "id": "t_old",
            "status": "blocked",
            "body": "review_type: read-only adversarial code review leaf\n",
        },
        {
            "id": "t_first",
            "status": "blocked",
            "body": "review_type: read-only adversarial code review leaf\ncontinuation_of: t_old\nfailure_run: 1\n",
        },
        {
            "id": "t_current",
            "status": "ready",
            "body": "review_type: read-only adversarial code review leaf\ncontinuation_of: t_first\nfailure_run: 2\n",
        },
        {"id": "t_done", "status": "done", "body": ""},
    ]

    assert recovery.replacement_fanin_parents(fanin, rows, {"t_old": 1}) == ["t_current", "t_done"]


def test_fan_in_creation_has_every_frontier_parent_in_the_create_call():
    fanin = {"id": "t_fanin", "title": "Review synthesis", "assignee": "default", "workspace_path": "/repo/.worktrees/t_impl", "body": "review_type: bounded fan-in"}
    command = recovery.fanin_command("board", fanin, ["t_one", "t_two"], "body", "key")

    assert command.count("--parent") == 2
    assert command[command.index("--parent") + 1] == "t_one"
    assert command[command.index("--parent", command.index("--parent") + 1) + 1] == "t_two"
    assert "--max-retries" in command
    assert command[command.index("--max-retries") + 1] == "1"


def test_dry_run_does_not_settle_an_existing_replacement_fan_in(monkeypatch):
    fanin = {
        "id": "t_fanin",
        "title": "Review synthesis",
        "status": "todo",
        "body": "review_type: bounded fan-in\nleaf_tasks: t_old\n",
    }
    old = {
        "id": "t_old",
        "status": "blocked",
        "body": "review_type: read-only adversarial code review leaf\n",
    }
    successor = {
        "id": "t_successor",
        "status": "ready",
        "body": "review_type: read-only adversarial code review leaf\ncontinuation_of: t_old\nfailure_run: 1\n",
    }
    key = recovery.fanin_key("t_fanin", ["t_successor"])
    existing = {
        "id": "t_existing",
        "status": "todo",
        "body": (
            "review_type: bounded fan-in\n"
            "replaces_fan_in: t_fanin\n"
            f"review_successor_idempotency_key: {key}\n"
        ),
    }
    rows = [fanin, old, successor, existing]
    details = {
        "t_fanin": {"task": fanin},
        "t_old": {
            "task": old,
            "runs": [{"id": 1, "status": "timed_out", "outcome": "timed_out"}],
        },
    }
    settled = []
    monkeypatch.setattr(
        recovery,
        "_show",
        lambda board, task_id: details.get(
            task_id,
            {"task": next(row for row in rows if row["id"] == task_id)},
        ),
    )
    monkeypatch.setattr(
        recovery,
        "_settle_old_fanin",
        lambda board, fanin_id, replacement_id: settled.append((fanin_id, replacement_id)),
    )

    changes = []
    recovery._recover_fanins("board", rows, apply=False, changes=changes)

    assert settled == []
    assert any("existing replacement fan-in t_existing" in change for change in changes)


def test_atomic_successor_recovery_rolls_back_created_cards_on_later_failure(tmp_path, monkeypatch):
    task = {
        **BROAD_TASK,
        "status": "blocked",
        "workspace_path": str(tmp_path),
        "body": BROAD_TASK["body"].replace("/repo/.worktrees/t_impl", str(tmp_path)),
    }
    details = {
        task["id"]: {
            "task": task,
            "runs": [{"id": 1, "status": "timed_out", "outcome": "timed_out"}],
        }
    }
    created = []
    archived = []
    monkeypatch.setattr(recovery, "_show", lambda board, task_id: details[task_id])
    monkeypatch.setattr(recovery, "_preflight_profile", lambda profile: None)

    def fake_create(board, packet, failure, spec, key):
        if not created:
            created.append("t_created")
            return "t_created"
        raise RuntimeError("simulated readback failure")

    monkeypatch.setattr(recovery, "_create_successor", fake_create)
    monkeypatch.setattr(recovery, "_archive_created_task", lambda board, task_id: archived.append(task_id))

    rows = [task]
    changes = []
    recovery._recover_review_leaves("board", rows, apply=True, changes=changes)

    assert archived == ["t_created"]
    assert all(row["id"] != "t_created" for row in rows)
    assert any("successor batch rolled back" in change for change in changes)


def test_missing_branch_is_not_sent_to_successor_creation(tmp_path, monkeypatch):
    task = {
        **BROAD_TASK,
        "status": "blocked",
        "workspace_path": str(tmp_path),
        "body": BROAD_TASK["body"].replace("branch: review/t_impl\n", "").replace(
            "/repo/.worktrees/t_impl", str(tmp_path)
        ),
    }
    detail = {
        "task": task,
        "runs": [{"id": 1, "status": "timed_out", "outcome": "timed_out"}],
    }
    created = []
    monkeypatch.setattr(recovery, "_show", lambda board, task_id: detail)
    monkeypatch.setattr(
        recovery,
        "_create_successor",
        lambda *args: created.append(True),
    )

    rows = [task]
    changes = []
    recovery._recover_review_leaves("board", rows, apply=True, changes=changes)

    assert created == []
    assert any("missing branch" in change for change in changes)


def test_dry_run_recovery_calls_no_mutating_helpers(tmp_path, monkeypatch):
    task = {
        **BROAD_TASK,
        "status": "blocked",
        "workspace_path": str(tmp_path),
        "body": BROAD_TASK["body"].replace("/repo/.worktrees/t_impl", str(tmp_path)),
    }
    detail = {
        "task": task,
        "runs": [{"id": 1, "status": "timed_out", "outcome": "timed_out"}],
    }
    monkeypatch.setattr(recovery, "_show", lambda board, task_id: detail)
    for name in ("_create_successor", "_comment_once", "_archive_created_task", "_settle_old_fanin"):
        monkeypatch.setattr(recovery, name, lambda *args: (_ for _ in ()).throw(AssertionError(name)))

    rows = [task]
    changes = []
    recovery._recover_review_leaves("board", rows, apply=False, changes=changes)

    assert len(changes) == recovery.MAX_SUCCESSOR_SPECS
    assert all("would create review successor" in change for change in changes)


def test_failed_create_rediscovers_and_archives_an_orphan_by_exact_key(monkeypatch):
    packet = recovery.parse_review_packet(BROAD_TASK)
    spec = recovery.successor_specs(packet)[0]
    key = "review-successor:t_broad:7:" + spec["key"]
    archived = []
    monkeypatch.setattr(recovery, "_json_command", lambda *args: (_ for _ in ()).throw(RuntimeError("timeout")))
    monkeypatch.setattr(
        recovery,
        "_list",
        lambda board: [{"id": "t_orphan", "body": f"review_successor_idempotency_key: {key}\n"}],
    )
    monkeypatch.setattr(recovery, "_archive_created_task", lambda board, task_id: archived.append(task_id))

    try:
        recovery._create_successor(
            "board",
            packet,
            {"id": 7, "status": "timed_out", "outcome": "timed_out"},
            spec,
            key,
        )
    except RuntimeError as exc:
        assert str(exc) == "timeout"
    else:
        raise AssertionError("expected create failure")

    assert archived == ["t_orphan"]


def test_existing_key_requires_an_exact_field_value():
    key = "review-successor:t_old:7:q-01"
    rows = [
        {"id": "t_note", "body": f"unrelated note mentions {key}"},
        {"id": "t_match", "body": f"review_successor_idempotency_key: {key}\n"},
    ]

    assert recovery._existing_key(rows[:1], key) is None
    assert recovery._existing_key(rows, key)["id"] == "t_match"


def test_board_path_lookup_is_cached_per_board(tmp_path, monkeypatch):
    calls = []

    def fake_json_command(*args):
        calls.append(args)
        return [{"slug": "cache-board", "db_path": str(tmp_path / "kanban.db")}]

    recovery._board_db_path.cache_clear()
    monkeypatch.setattr(recovery, "_json_command", fake_json_command)

    assert recovery._board_db_path("cache-board") == (tmp_path / "kanban.db").resolve()
    assert recovery._board_db_path("cache-board") == (tmp_path / "kanban.db").resolve()
    assert len(calls) == 1


def test_cron_wrapper_requires_an_explicit_board_and_uses_a_sibling_script():
    wrapper = (ROOT / "scripts" / "kanban_review_successor_recovery_cron.py").read_text(encoding="utf-8")

    assert 'os.environ.get("HERMES_FACTORY_BOARD", "").strip()' in wrapper
    assert "HERMES_FACTORY_BOARD is required" in wrapper
    assert "with_name(\"kanban_review_successor_recovery.py\")" in wrapper
    assert '"--apply"' in wrapper
    assert '"--quiet"' in wrapper
