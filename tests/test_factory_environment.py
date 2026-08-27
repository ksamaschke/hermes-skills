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


def test_example_declares_provider_auth_modes_without_credentials():
    document = _example()
    auth = document["providers"]["auth"]

    assert set(auth) == {"local_qwen", "external"}
    assert auth["local_qwen"]["mode"] == "subscription"
    assert set(auth["local_qwen"]) == {"mode", "credential_store"}
    assert auth["local_qwen"]["credential_store"]
    assert auth["external"]["mode"] == "api_key"
    assert set(auth["external"]) == {"mode", "secret"}
    assert set(auth["external"]["secret"]) == {"name", "key"}


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


def test_validator_accepts_api_key_and_subscription_for_each_provider():
    for provider in ("local_qwen", "external"):
        for mode in ("api_key", "subscription"):
            document = _example()
            if mode == "api_key":
                document["providers"]["auth"][provider] = {
                    "mode": mode,
                    "secret": {"name": "provider-secret", "key": "api-key"},
                }
            else:
                document["providers"]["auth"][provider] = {
                    "mode": mode,
                    "credential_store": "profile-credential-store",
                }

            assert validator.validate(document) == []


def test_validator_rejects_unsupported_auth_mode():
    document = _example()
    document["providers"]["auth"]["external"]["mode"] = "oauth"

    errors = validator.validate(document)

    assert errors == [
        "providers.auth.external.mode must be one of: api_key, subscription"
    ]


def test_validator_rejects_api_key_without_secret_reference():
    document = _example()
    del document["providers"]["auth"]["external"]["secret"]

    errors = validator.validate(document)

    assert "providers.auth.external.secret must be a name/key reference" in errors


def test_validator_rejects_inline_auth_secret_values():
    document = _example()
    document["providers"]["auth"]["external"]["secret"]["value"] = (
        "must-not-be-committed"
    )

    errors = validator.validate(document)

    assert any(
        "providers.auth.external.secret" in error and "name/key" in error
        for error in errors
    )
    assert any("inline secret" in error for error in errors)


def test_validator_rejects_unsupported_environment_kind():
    document = _example()
    document["environment"]["kind"] = "production"

    errors = validator.validate(document)

    assert errors == ["environment.kind must be one of: external, homelab"]


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
