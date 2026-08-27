"""Tests for the reusable factory-environment onboarding contract."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import yaml

from scripts import validate_factory_environment as validator


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "factory-environment.yaml"
SCRIPT = ROOT / "scripts" / "validate_factory_environment.py"
EXPECTED_SECTIONS = {
    "version",
    "environment",
    "gitops",
    "factory",
    "tracker",
    "profiles",
    "providers",
    "models",
    "brain",
    "secrets",
}


def _example() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def test_example_contains_only_generic_contract_sections():
    document = _example()

    assert set(document) == EXPECTED_SECTIONS
    assert document["environment"]["kind"]
    assert document["gitops"]["controller"]
    assert document["gitops"]["repository"]
    assert document["factory"]["repository"]
    assert set(document["tracker"]) == {"kind", "project", "board"}
    assert set(document["profiles"]) == {
        "orchestrator",
        "implementer",
        "code_reviewer",
    }
    assert set(document["providers"]["aliases"]) == {"local_qwen", "external"}
    assert set(document["models"]["aliases"]) == {
        "orchestrator",
        "implementer",
        "code_reviewer",
    }


def test_example_declares_optional_brain_and_reference_only_secrets():
    document = _example()

    assert document["brain"] == {
        "optional": True,
        "existing": "detect_and_reuse",
        "missing": "explicit_approval_required",
        "installation": "never_automatic",
        "provider": {"homelab": "local_qwen", "external": "external"},
    }
    for reference in document["secrets"].values():
        assert set(reference) == {"name", "key"}


def test_example_passes_deterministic_validator():
    assert validator.validate(_example()) == []


def test_validator_reports_missing_required_fields():
    document = _example()
    del document["tracker"]["board"]
    del document["profiles"]["code_reviewer"]

    errors = validator.validate(document)

    assert "missing required field: tracker.board" in errors
    assert "missing required field: profiles.code_reviewer" in errors


def test_validator_rejects_inline_secret_values():
    document = _example()
    document["secrets"]["tracker"]["value"] = "must-not-be-committed"

    errors = validator.validate(document)

    assert any("secrets.tracker" in error and "name/key" in error for error in errors)
    assert any("inline secret" in error for error in errors)


def test_validator_accepts_secret_references_by_name_and_key_only():
    document = _example()
    document["secrets"] = {
        "tracker": {"name": "tracker-credentials", "key": "access-token"},
        "model": {"name": "model-credentials", "key": "api-key"},
    }

    assert validator.validate(document) == []


def test_cli_returns_nonzero_for_invalid_document(tmp_path):
    document = _example()
    del document["gitops"]["repository"]
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "missing required field: gitops.repository" in result.stderr


def test_cli_does_not_need_external_services(tmp_path):
    document = copy.deepcopy(_example())
    path = tmp_path / "valid.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT)},
    )

    assert result.returncode == 0
    assert "valid" in result.stdout.lower()
