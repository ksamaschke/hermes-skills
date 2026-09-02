from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CRON_SUPERVISION = ROOT / "examples" / "factory-cron-supervision.yaml"


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


def test_runtime_contract_documents_generic_profiles_and_fallbacks():
    text = (ROOT / "docs" / "kanban-factory-runtime.md").read_text()
    assert "## Role routing" in text
    assert "The decomposer creates a task graph" in text
    assert "project policy" in text
    assert "auto_subscribe_on_create: false" in text
    assert "auxiliary provider" in text
    assert "model" in text


def test_central_reporting_does_not_make_internal_state_a_user_chore():
    text = (ROOT / "docs" / "central-kanban-reporting.md").read_text()
    assert "Internal execution failures are not human blockers" in text
    assert "Never enumerate parked task IDs" in text
    assert "No human action" in text


def test_factory_operations_preserve_parked_tasks_without_escalating_them():
    text = (ROOT / "skills" / "kanban-factory-operations" / "SKILL.md").read_text()
    assert "never unblock or dispatch them" in text
    assert "Only genuine human decisions" in text


def test_agent_supervised_cron_example_separates_controllers_from_supervisor():
    config = yaml.safe_load(CRON_SUPERVISION.read_text(encoding="utf-8"))
    controllers = config["controllers"]
    supervisor = config["supervisor"]

    assert controllers
    assert all(controller["no_agent"] is True for controller in controllers)
    assert all(controller["deliver"] == "local" for controller in controllers)
    assert supervisor["no_agent"] is False
    assert supervisor["context_from"] == [controller["id"] for controller in controllers]
    assert supervisor["continuity"] is True
    assert supervisor["workdir"] == "<project-workdir>"
    assert set(supervisor["enabled_toolsets"]) >= {
        "terminal",
        "file",
        "code_execution",
    }
    assert set(supervisor["skills"]) >= {
        "kanban-factory-operations",
        "software-factory-recovery",
        "factory-reporting",
        "kanban-progress-evidence",
    }
    assert supervisor["deliver"] == "<human-delivery-target>"
    assert supervisor["attach_to_session"] is True


def test_agent_supervision_contract_requires_action_first_reporting():
    paths = [
        ROOT / "docs" / "central-kanban-reporting.md",
        ROOT / "skills" / "kanban-implementation-workflow" / "SKILL.md",
        ROOT / "skills" / "kanban-factory-operations" / "SKILL.md",
        ROOT / "skills" / "software-factory-recovery" / "SKILL.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    for phrase in (
        "context_from",
        "continuity",
        "deliver: local",
        "Controller output is an observation, never proof",
        "ACTIVE",
        "IDLE-BY-GATING",
        "STALLED",
        "[SILENT]",
        "bot-chat",
        "reads back",
    ):
        assert phrase in combined, phrase
