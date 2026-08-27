"""Contract tests for the explicit post-review delivery lifecycle."""

from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PROFILE_ROLES = ROOT / "docs" / "profile-roles.md"
LIFECYCLE = ROOT / "docs" / "factory-delivery-lifecycle.md"
WORKFLOW = ROOT / "skills" / "kanban-implementation-workflow" / "SKILL.md"
PROGRESS = ROOT / "skills" / "kanban-progress-evidence" / "SKILL.md"
RUNTIME = ROOT / "docs" / "kanban-factory-runtime.md"
POLICY = ROOT / "examples" / "project-policy.yaml"


def test_policy_declares_integration_and_release_gates_separately():
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))

    assert "integration_operator" in policy["profiles"]
    delivery = policy["delivery"]
    assert delivery["integration"]["required"] is True
    assert delivery["integration"]["pull_request_required"] is True
    assert delivery["integration"]["host_review_required"] is True
    assert set(delivery["integration"]["merge_requires"]) >= {
        "independent_review",
        "required_ci",
        "host_review",
    }
    assert delivery["release"]["required"] is True
    assert delivery["release"]["deployment_requires_policy"] is True
    assert delivery["release"]["post_action_verification_required"] is True


def test_lifecycle_doc_keeps_all_delivery_states_distinct():
    text = LIFECYCLE.read_text(encoding="utf-8").lower()

    for phrase in (
        "implementation complete",
        "review complete",
        "integration complete",
        "deployment complete",
        "pull request",
        "host review",
        "merged commit",
        "post-action verification",
        "worktree is not delivery",
        "all worker stages are kanban tasks assigned to hermes profiles",
    ):
        assert phrase in text

    assert "review verdict" in text
    assert "does not imply" in text


def test_roles_and_workflow_have_real_integration_and_release_owners():
    roles = PROFILE_ROLES.read_text(encoding="utf-8").lower()
    workflow = WORKFLOW.read_text(encoding="utf-8").lower()
    readme = README.read_text(encoding="utf-8").lower()

    assert "## integration operator" in roles
    assert "pull request" in roles
    assert "host review" in roles
    assert "## release/gitops" in roles
    assert "merged commit" in roles
    assert "integration operator" in workflow
    assert "create or verify the pull request" in workflow
    assert "host review" in workflow
    assert "merge" in workflow
    runtime = RUNTIME.read_text(encoding="utf-8").lower()
    assert "## delivery handoff" in runtime
    assert "integration-operator" in runtime
    assert "merged revision" in runtime
    progress = PROGRESS.read_text(encoding="utf-8").lower()
    compact_progress = " ".join(progress.split())
    assert "verified implementation/review is not integration or deployment" in compact_progress
    assert "post-action" in progress
    assert "integration-operator" in readme
    assert "factory-delivery-lifecycle.md" in readme


def test_delivery_contract_forbids_inference_from_worker_or_review_state():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (LIFECYCLE, PROFILE_ROLES, WORKFLOW)
    ).lower()
    assert "worktree" in text and "not delivery" in text
    assert "review complete" in text and "integration complete" in text
    assert "deployment" in text
    assert "read back" in text
