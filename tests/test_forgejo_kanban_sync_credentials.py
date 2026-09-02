"""Credential fallback tests for the generic Forgejo reconciler."""

from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "forgejo_kanban_sync.py"
SPEC = importlib.util.spec_from_file_location("forgejo_kanban_sync_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def _source(cache: Path) -> dict[str, str]:
    return {
        "credential_host": "forgejo.example.invalid",
        "credential_cache": str(cache),
    }


def test_helper_success_populates_private_cache(tmp_path: Path) -> None:
    cache = tmp_path / "headers.json"
    completed = subprocess.CompletedProcess(
        ["git", "credential", "fill"],
        0,
        stdout="username=test-user\npassword=test-password\n",
        stderr="",
    )
    sync._CREDENTIAL_HEADERS.clear()
    with mock.patch.object(sync.subprocess, "run", return_value=completed) as run:
        headers = sync.credential_headers(_source(cache))

    assert headers["Authorization"].startswith("Basic ")
    assert headers["User-Agent"] == "HEX-forgejo-sync/1.0"
    assert json.loads(cache.read_text()) == headers
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600
    environment = run.call_args.kwargs["env"]
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_ASKPASS"] == "/usr/bin/false"


def test_legacy_cache_path_key_is_supported(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    assert sync.credential_cache_path({
        "credential_host": "forgejo.example.invalid",
        "credential_cache_path": str(path),
    }) == path


def test_noninteractive_helper_failure_uses_cached_headers(tmp_path: Path) -> None:
    cache = tmp_path / "headers.json"
    expected = {
        "Accept": "application/json",
        "Authorization": "Basic test-only-cache-value",
        "User-Agent": "HEX-forgejo-sync/1.0",
    }
    sync.atomic_json_write(cache, expected, mode=0o600)
    sync._CREDENTIAL_HEADERS.clear()
    failure = subprocess.CalledProcessError(128, ["git", "credential", "fill"])
    with mock.patch.object(sync.subprocess, "run", side_effect=failure):
        actual = sync.credential_headers(_source(cache))

    assert actual == expected


def test_missing_cache_names_interactive_population_remedy(tmp_path: Path) -> None:
    cache = tmp_path / "missing.json"
    sync._CREDENTIAL_HEADERS.clear()
    failure = subprocess.CalledProcessError(128, ["git", "credential", "fill"])
    with mock.patch.object(sync.subprocess, "run", side_effect=failure):
        try:
            sync.credential_headers(_source(cache))
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("missing cache should fail")

    assert str(cache) in message
    assert "read-only sync interactively" in message