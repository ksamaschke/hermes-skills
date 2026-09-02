#!/usr/bin/env python3
"""Read live Forgejo/Kanban state and render a plain-English snapshot.

Forgejo remains the backlog source of truth. This adapter is read-only and
separates issue labels, local task completion, independent review verdicts, and
actual Forgejo closure eligibility.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List


RUNTIME_SCRIPT_DIR = Path.home() / ".hermes" / "scripts"
TRACKED_SCRIPT_DIR = Path(__file__).resolve().parent
for script_dir in (RUNTIME_SCRIPT_DIR, TRACKED_SCRIPT_DIR):
    script_path = str(script_dir)
    if script_path in sys.path:
        sys.path.remove(script_path)
    sys.path.insert(0, script_path)

import forgejo_kanban_sync as sync  # type: ignore[import-not-found]
from vanillacore_ai_gateway_snapshot_report import classify_issue, render_report


CONFIG_PATH = Path("/Users/karsten/.hermes/scripts/vanillacore-ai-gateway-sync.json")
BOARD = "vanillacore-ai-gateway"
MILESTONE = "VanillaCore AI Gateway remediation"
RELEASE_LABEL = "release-blocker"


def milestone_title(issue: Dict[str, Any]) -> str:
    value = issue.get("milestone") or {}
    if isinstance(value, dict):
        return str(value.get("title", ""))
    return str(value)


def snapshot_source_number(task: Dict[str, Any]) -> int | None:
    number = sync.task_source_number(task)
    if number is not None:
        return number
    text = "\n".join([str(task.get("title", "")), str(task.get("body", ""))])
    import re

    match = re.search(r"\bForgejo\s+#(\d+)\b", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def main() -> int:
    config = sync.load_json(CONFIG_PATH)
    headers = sync.credential_headers(config["source"])
    issues = sync.fetch_issues(config, headers)
    tasks = sync.hermes_run(["kanban", "--board", BOARD, "list"], json_output=True)

    milestone_issues = [
        issue for issue in issues if milestone_title(issue) == MILESTONE
    ]
    labeled = sorted(
        (
            issue
            for issue in milestone_issues
            if RELEASE_LABEL in sync.issue_labels(issue)
            and str(issue.get("state", "")).lower() == "open"
        ),
        key=lambda issue: int(issue["number"]),
    )

    tasks_by_issue: Dict[int, List[Dict[str, Any]]] = {}
    for task in tasks:
        number = snapshot_source_number(task)
        if number is not None:
            tasks_by_issue.setdefault(number, []).append(task)

    results: List[Dict[str, Any]] = []
    for issue in labeled:
        number = int(issue["number"])
        projections = sorted(
            tasks_by_issue.get(number, []),
            key=lambda task: str(task.get("id", "")),
        )
        details = [
            sync.hermes_run(
                ["kanban", "--board", BOARD, "show", str(task["id"])],
                json_output=True,
            )
            for task in projections
        ]
        result = classify_issue(number, projections, details)
        result["title"] = str(issue.get("title") or "Untitled issue")
        result["forgejo_state"] = str(issue.get("state") or "unknown")
        results.append(result)

    print(render_report(milestone_issues, results))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Release snapshot ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
