"""Focused tests for Forgejo source marker parsing."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "forgejo_kanban_sync.py"
SPEC = importlib.util.spec_from_file_location("forgejo_kanban_sync_source_markers_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def test_relative_source_marker_is_not_associated_with_an_issue() -> None:
    assert sync.task_source_number({"body": "Source issue: /issues/20"}) is None


@pytest.mark.parametrize(
    ("label", "scheme", "number"),
    [
        ("Source issue", "https", 17),
        ("Source_issue", "http", 18),
    ],
)
def test_source_marker_accepts_absolute_http_urls(
    label: str, scheme: str, number: int,
) -> None:
    marker = (
        f"{label}: {scheme}://forgejo.example.invalid/racktaq/hermes-software-factory"
        f"/issues/{number}"
    )
    assert sync.task_source_number({"body": marker}) == number


@pytest.mark.parametrize(
    "body",
    [
        "Source_issue:\t/issues/21",
        "Source issue:    ",
        "Source issue: https://forgejo.example.invalid/repo/issues/",
        "Source issue: https://forgejo.example.invalid/repo/issues/not-a-number",
        "Source issue: https://forgejo.example.invalid/repo/issues/20-extra",
        "Source issue: https://forgejo.example.invalid/repo/issues/0",
        "Source issue:\nhttps://forgejo.example.invalid/repo/issues/22",
        "Source issue: https://forgejo.example.invalid/repo/issues/23#fragment",
        "Source issue: https://forgejo.example.invalid/repo#fragment/issues/24",
        "Source issue: #fragment",
        "#fragment",
        (
            "Unrelated text mentioning Source issue: "
            "https://forgejo.example.invalid/repo/issues/25"
        ),
    ],
)
def test_source_marker_rejects_malformed_relative_and_unrelated_text(body: str) -> None:
    assert sync.task_source_number({"body": body}) is None


def test_explicit_source_key_precedes_source_marker_and_legacy_title() -> None:
    task = {
        "title": "Forgejo #13: legacy title",
        "body": "\n".join([
            "Forgejo source key: forgejo:forgejo.example.invalid/repo#11",
            "Source issue: https://forgejo.example.invalid/repo/issues/12",
        ]),
    }
    assert sync.task_source_number(task) == 11


def test_source_marker_precedes_legacy_title() -> None:
    task = {
        "title": "Forgejo #13: legacy title",
        "body": "Source_issue: https://forgejo.example.invalid/repo/issues/12",
    }
    assert sync.task_source_number(task) == 12


def test_legacy_title_is_source_number_fallback() -> None:
    assert sync.task_source_number({"title": "Forgejo #13: legacy title"}) == 13
