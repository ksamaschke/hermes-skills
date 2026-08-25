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


def test_runtime_contract_documents_explicit_profiles_and_fallbacks():
    text = (ROOT / "docs" / "kanban-factory-runtime.md").read_text()
    assert "Human-facing default profile" in text
    assert "The decomposer is not a separate Hermes profile" in text
    assert "implementation-skills.yaml" in text
    assert "auto_subscribe_on_create: false" in text
    assert "provider: openai-codex" in text
    assert "model: gpt-5.6-luna" in text


def test_central_reporting_does_not_make_internal_state_a_user_chore():
    text = (ROOT / "docs" / "central-kanban-reporting.md").read_text()
    assert "Internal execution failures are not human blockers" in text
    assert "Never enumerate parked task IDs" in text
    assert "No human action" in text


def test_factory_operations_preserve_parked_tasks_without_escalating_them():
    text = (ROOT / "skills" / "kanban-factory-operations" / "SKILL.md").read_text()
    assert "never unblock or dispatch them" in text
    assert "Only genuine human decisions" in text
