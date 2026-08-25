from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_factory_operations_require_origin_notification_for_blockers():
    text = (ROOT / "skills" / "kanban-factory-operations" / "SKILL.md").read_text()
    assert "notify-list <task_id> --json" in text
    assert "notify+wake" in text
    assert "must never be left silently" in text


def test_recovery_requires_current_handoff_for_old_blocked_events():
    text = (ROOT / "skills" / "software-factory-recovery" / "SKILL.md").read_text()
    assert "Existing subscriptions created after an old event do not replay" in text
    assert "current blocker handoff" in text
