from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_factory_operations_require_origin_notification_for_blockers():
    text = (ROOT / "skills" / "kanban-factory-operations" / "SKILL.md").read_text()
    assert "Do **not** create a" in text
    assert "Matrix subscription for the individual task" in text
    assert "central dispatcher/digest report" in text
    assert "workers write" in text
    assert "board state and events" in text


def test_recovery_requires_current_handoff_for_old_blocked_events():
    text = (ROOT / "skills" / "software-factory-recovery" / "SKILL.md").read_text()
    assert "individual task cards must not contact Matrix directly" in text
    assert "central HEX digest" in text
    assert "Do not create or retain per-task Matrix subscriptions" in text
