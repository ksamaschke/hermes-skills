#!/usr/bin/env python3
"""Validate a generic Hermes Software Factory environment overlay.

The validator deliberately only reads the supplied YAML file. It does not
resolve repositories, contact services, or inspect a runtime environment.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml


REQUIRED_FIELDS = (
    "version",
    "environment.kind",
    "gitops.controller",
    "gitops.repository",
    "factory.repository",
    "tracker.kind",
    "tracker.project",
    "tracker.board",
    "profiles.orchestrator",
    "profiles.implementer",
    "profiles.code_reviewer",
    "providers.aliases.local_qwen",
    "providers.aliases.external",
    "models.aliases.orchestrator",
    "models.aliases.implementer",
    "models.aliases.code_reviewer",
    "brain.optional",
    "brain.existing",
    "brain.missing",
    "brain.installation",
    "brain.provider.homelab",
    "brain.provider.external",
    "secrets.tracker",
    "secrets.model",
)

_SECRET_VALUE_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret_value",
    "token",
    "value",
}
_TOKEN_RE = re.compile(
    r"(?:\b(?:gh[pousr]|glpat)_[A-Za-z0-9_-]{16,}\b|\bsk-[A-Za-z0-9_-]{16,}\b)"
)


_MISSING = object()


def _value_at(document: Mapping[str, Any], path: str) -> Any:
    value: Any = document
    for component in path.split("."):
        if not isinstance(value, Mapping) or component not in value:
            return _MISSING
        value = value[component]
    return value


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalise_key(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_")


def _find_inline_secret_values(value: Any, path: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if _normalise_key(key) in _SECRET_VALUE_KEYS:
                errors.append(
                    f"{child_path}: inline secret values are not allowed"
                )
                continue
            errors.extend(_find_inline_secret_values(child, child_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            errors.extend(_find_inline_secret_values(child, f"{path}[{index}]"))
    elif isinstance(value, str) and _TOKEN_RE.search(value):
        errors.append(f"{path}: inline secret values are not allowed")
    return errors


def _validate_secret_references(document: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    secrets = _value_at(document, "secrets")
    if not isinstance(secrets, Mapping):
        return errors

    for name, reference in secrets.items():
        path = f"secrets.{name}"
        if not isinstance(reference, Mapping):
            errors.append(
                f"{path} must be a name/key reference; "
                "inline secret values are not allowed"
            )
            continue
        if set(reference) != {"name", "key"}:
            errors.append(
                f"{path} must contain only name/key; inline secret values are not allowed"
            )
            continue
        for component in ("name", "key"):
            if not _is_non_empty_string(reference[component]):
                errors.append(f"{path}.{component} must be a non-empty reference")
    return errors


def validate(document: Any) -> list[str]:
    """Return deterministic validation errors for a factory environment document."""

    if not isinstance(document, Mapping):
        return ["document must be a YAML mapping"]

    errors: list[str] = []
    for path in REQUIRED_FIELDS:
        value = _value_at(document, path)
        if value is _MISSING or value is None:
            errors.append(f"missing required field: {path}")
        elif path == "version":
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                errors.append("version must be a positive integer")
        elif path == "brain.optional":
            continue
        elif path.startswith("secrets."):
            continue
        elif not _is_non_empty_string(value):
            errors.append(f"field must be a non-empty string: {path}")

    brain_optional = _value_at(document, "brain.optional")
    if brain_optional is not _MISSING and brain_optional is not True:
        errors.append("brain.optional must be true")

    expected_brain_policy = {
        "brain.existing": "detect_and_reuse",
        "brain.missing": "explicit_approval_required",
        "brain.installation": "never_automatic",
        "brain.provider.homelab": "local_qwen",
        "brain.provider.external": "external",
    }
    for path, expected in expected_brain_policy.items():
        value = _value_at(document, path)
        if value is not _MISSING and value != expected:
            errors.append(f"{path} must be {expected!r}")

    errors.extend(_validate_secret_references(document))
    errors.extend(_find_inline_secret_values(document))
    return sorted(set(errors))


def load(path: Path) -> Any:
    """Load YAML from *path* without performing any external lookups."""

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_file(path: Path) -> list[str]:
    """Return validation errors for a YAML file, including read/parse errors."""

    try:
        return validate(load(path))
    except OSError as exc:
        return [f"could not read {path}: {exc}"]
    except yaml.YAMLError as exc:
        return [f"invalid YAML in {path}: {exc}"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="factory environment YAML file")
    args = parser.parse_args(argv)

    errors = validate_file(args.path)
    if errors:
        print(f"{args.path}: invalid factory environment", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"{args.path}: valid factory environment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
