#!/usr/bin/env python3
"""Generic Forgejo-to-Hermes-Kanban reconciler.

Forgejo is the product backlog source of truth. Hermes Kanban owns execution
state. This add-on is deliberately outside Hermes core and never runs the
Kanban dispatcher; the supervised gateway remains the sole dispatcher.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


QUEUE_STATUSES = {"ready", "todo", "triage", "scheduled"}
ACTIVE_REVIEW_STATUSES = {"review", "running"}


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.expanduser().read_text())
    if not isinstance(value, dict):
        raise ValueError(f"configuration must be an object: {path}")
    return value


def atomic_json_write(
    path: Path,
    value: Dict[str, Any],
    *,
    mode: Optional[int] = None,
) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        if mode is not None:
            path.chmod(mode)
    finally:
        if os.path.exists(name):
            os.unlink(name)


_CREDENTIAL_HEADERS: Dict[str, Dict[str, str]] = {}


def credential_cache_path(source: Dict[str, Any]) -> Path:
    configured = source.get("credential_cache") or source.get("credential_cache_path")
    if configured:
        return Path(str(configured)).expanduser()
    host = str(source["credential_host"])
    key = hashlib.sha256(host.encode()).hexdigest()[:16]
    return Path("~/.hermes/state/forgejo-credential-cache").expanduser() / f"{key}.json"


def _validated_headers(value: Any, path: Path) -> Dict[str, str]:
    if not isinstance(value, dict):
        raise RuntimeError(f"credential cache is not an object: {path}")
    headers = {str(key): str(item) for key, item in value.items()}
    if not headers.get("Authorization") or not headers.get("User-Agent"):
        raise RuntimeError(f"credential cache is missing required headers: {path}")
    headers.setdefault("Accept", "application/json")
    return headers


def _resolve_credential_headers(source: Dict[str, Any]) -> Dict[str, str]:
    host = str(source["credential_host"])
    environment = os.environ.copy()
    environment.setdefault("GIT_TERMINAL_PROMPT", "0")
    environment.setdefault("GIT_ASKPASS", "/usr/bin/false")
    result = subprocess.run(
        ["git", "credential", "fill"],
        input=f"protocol=https\nhost={host}\n\n",
        text=True,
        capture_output=True,
        check=True,
        env=environment,
    )
    values = dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
        if "=" in line
    )
    username = values.get("username", "")
    password = values.get("password", "")
    if not password:
        raise RuntimeError("credential helper returned no Forgejo credential")
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {
        "Accept": "application/json",
        "Authorization": f"Basic {token}",
        "User-Agent": "HEX-forgejo-sync/1.0",
    }


def credential_headers(source: Dict[str, Any]) -> Dict[str, str]:
    """Resolve Forgejo auth, falling back to a private non-interactive cache."""
    cache = credential_cache_path(source)
    cache_key = str(cache)
    if cache_key in _CREDENTIAL_HEADERS:
        return dict(_CREDENTIAL_HEADERS[cache_key])
    try:
        headers = _resolve_credential_headers(source)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        if not cache.is_file():
            raise RuntimeError(
                f"cannot resolve Forgejo credentials and no cache exists at {cache}; "
                "run one read-only sync interactively to populate it"
            ) from exc
        try:
            headers = _validated_headers(json.loads(cache.read_text()), cache)
        except (OSError, ValueError, RuntimeError) as cache_exc:
            raise RuntimeError(
                f"cannot resolve Forgejo credentials and cache is unusable at {cache}"
            ) from cache_exc
    else:
        atomic_json_write(cache, headers, mode=0o600)
    _CREDENTIAL_HEADERS[cache_key] = dict(headers)
    return dict(headers)


def forgejo_get(source: Dict[str, Any], path: str, headers: Dict[str, str]) -> Any:
    request = urllib.request.Request(
        str(source["api_root"]).rstrip("/") + path,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Forgejo GET {path} returned HTTP {exc.code}") from exc


def fetch_issues(config: Dict[str, Any], headers: Dict[str, str]) -> List[Dict[str, Any]]:
    source = config["source"]
    repo = str(source["repository"])
    result: List[Dict[str, Any]] = []
    for page in range(1, 101):
        page_items = forgejo_get(
            source,
            f"/repos/{repo}/issues?state=all&type=issues&limit=50&page={page}",
            headers,
        )
        if not page_items:
            break
        result.extend(page_items)
        if len(page_items) < 50:
            break
    return result


def hermes_bin() -> str:
    return shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")


def hermes_run(args: List[str], json_output: bool = False) -> Any:
    environment = os.environ.copy()
    environment.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)
    command = [hermes_bin()] + args
    if json_output:
        command.append("--json")
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        env=environment,
        timeout=90,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().replace("\n", " ")
        raise RuntimeError(f"Hermes command failed: {detail[:500]}")
    if not json_output:
        return result.stdout
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Hermes command returned non-JSON output") from exc


def issue_labels(issue: Dict[str, Any]) -> Set[str]:
    return {
        str(label.get("name", "")).strip().lower()
        for label in issue.get("labels", [])
    }


def issue_assignees(issue: Dict[str, Any]) -> List[str]:
    raw_assignees = issue.get("assignees")
    if not isinstance(raw_assignees, list):
        return []
    return [
        str(assignee.get("login", "")).strip()
        for assignee in raw_assignees
        if isinstance(assignee, dict) and assignee.get("login")
    ]


def field_value(body: str, field: str) -> str:
    match = re.search(
        rf"^\*\*{re.escape(field)}:\*\*\s*(.+)$",
        body,
        re.MULTILINE,
    )
    return match.group(1).strip() if match else ""


def issue_dependencies(issue: Dict[str, Any]) -> List[int]:
    body = str(issue.get("body", ""))
    references: List[int] = []
    for field in ("Depends on", "Gated by"):
        value = field_value(body, field)
        references.extend(int(number) for number in re.findall(r"#(\d+)", value))
    return sorted(set(references))


def issue_grouping_parent(issue: Dict[str, Any]) -> Optional[int]:
    value = field_value(str(issue.get("body", "")), "Parent")
    match = re.fullmatch(r"#(\d+)", value)
    return int(match.group(1)) if match else None


def target_key(config: Dict[str, Any], issue: Dict[str, Any]) -> str:
    body = str(issue.get("body", ""))
    target = field_value(body, "Target repo")
    title = str(issue.get("title", ""))
    haystack = f"{target} {title}".lower()
    for mapping in config.get("target_mappings", []):
        if any(str(token).lower() in haystack for token in mapping.get("contains", [])):
            return str(mapping["key"])
    return "core"


def workspace_path(config: Dict[str, Any], key: str) -> Path:
    roots = config["workspace_roots"]
    if key not in roots:
        raise ValueError(f"no workspace root configured for target key {key!r}")
    path = Path(str(roots[key])).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError(f"configured workspace root is not a directory: {path}")
    return path


def desired_profile(config: Dict[str, Any], issue: Dict[str, Any]) -> str:
    profiles = config["profiles"]
    assignee_map = profiles.get("forgejo_assignees", {})
    for login in issue_assignees(issue):
        if login in assignee_map:
            return str(assignee_map[login])
    if issue_labels(issue) & set(profiles.get("review_labels", [])):
        return str(profiles["reviewer"])
    return str(profiles["default_implementer"])


def source_key(config: Dict[str, Any], issue_number: int) -> str:
    source = config["source"]
    return f"forgejo:{source['credential_host']}/{source['repository']}#{issue_number}"


def fingerprint(issue: Dict[str, Any]) -> str:
    normalized = {
        "number": issue.get("number"),
        "state": issue.get("state"),
        "title": issue.get("title"),
        "body": issue.get("body"),
        "labels": sorted(issue_labels(issue)),
        "assignees": sorted(issue_assignees(issue)),
    }
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def task_source_number(task: Dict[str, Any]) -> Optional[int]:
    text = "\n".join([str(task.get("title", "")), str(task.get("body", ""))])
    patterns = [
        r"Forgejo source key:\s*[^#\n]+#(\d+)",
        r"Source issue:\s*[^\n]*/issues/(\d+)",
        r"^Forgejo #(\d+):",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def task_fingerprint(task: Dict[str, Any]) -> Optional[str]:
    match = re.search(
        r"^Forgejo source fingerprint:\s*(sha256:[0-9a-f]+)$",
        str(task.get("body", "")),
        re.MULTILINE,
    )
    return match.group(1) if match else None


def source_issue_url(config: Dict[str, Any], issue_number: int) -> str:
    source = config["source"]
    return f"{str(source['web_root']).rstrip('/')}/{source['repository']}/issues/{issue_number}"


def task_body(config: Dict[str, Any], issue: Dict[str, Any], target: str) -> str:
    number = int(issue["number"])
    grouping_parent = issue_grouping_parent(issue)
    dependencies = issue_dependencies(issue)
    grouping = (
        f"#{grouping_parent} (grouping only; not an execution dependency)"
        if grouping_parent is not None else "none"
    )
    execution = ", ".join(f"#{item}" for item in dependencies) or "none"
    return "\n".join([
        f"Forgejo source key: {source_key(config, number)}",
        f"Forgejo source fingerprint: {fingerprint(issue)}",
        f"Source issue: {source_issue_url(config, number)}",
        f"Forgejo issue number: #{number}",
        f"Target checkout key: {target}",
        f"Forgejo grouping parent: {grouping}",
        f"Forgejo execution dependencies: {execution}",
        "Forgejo managed execution dependencies: true",
        "",
        "Execute the Forgejo issue exactly as written. Work in the isolated Kanban worktree.",
        "TDD first for behavior changes: add a failing regression test before production changes.",
        "Do not edit the Forgejo issue description; progress belongs in Kanban handoff/comments.",
        "Independent review is mandatory before completion. Do not deploy live infrastructure.",
        "",
        "## Forgejo issue body",
        str(issue.get("body", "")).strip(),
    ])


def issue_is_actionable(config: Dict[str, Any], issue: Dict[str, Any]) -> bool:
    return (
        str(issue.get("state", "")).lower() == "open"
        and not (issue_labels(issue) & set(config.get("excluded_labels", [])))
    )


def priority(issue: Dict[str, Any], config: Dict[str, Any]) -> int:
    labels = issue_labels(issue)
    if "severity:high" in labels or "release-blocker" in labels:
        return 100
    if "review-gap" in labels:
        return 90
    if "severity:medium" in labels:
        return 70
    return 40


def max_runtime(issue: Dict[str, Any]) -> str:
    labels = issue_labels(issue)
    if "review-gap" in labels:
        return "15m"
    if "severity:high" in labels or "release-blocker" in labels:
        return "30m"
    return "20m"


def create_task(
    config: Dict[str, Any], issue: Dict[str, Any], dependency_task_ids: List[str],
) -> Dict[str, Any]:
    board = str(config["board"])
    target = target_key(config, issue)
    repo = workspace_path(config, target)
    profile = desired_profile(config, issue)
    number = int(issue["number"])
    title = f"Forgejo #{number}: {str(issue.get('title', '')).strip()}"
    command = [
        "kanban", "--board", board, "create", title,
        "--body", task_body(config, issue, target),
        "--assignee", profile,
        "--workspace", f"worktree:{repo}",
        "--branch", f"kanban/forgejo-{number}",
        "--project", str(config["project"]),
        "--priority", str(priority(issue, config)),
        "--max-runtime", max_runtime(issue),
        "--max-retries", "1" if profile == config["profiles"]["reviewer"] else "2",
        "--idempotency-key", source_key(config, number),
    ]
    for parent_id in dependency_task_ids:
        command.extend(["--parent", parent_id])
    return hermes_run(command, json_output=True)


def task_show(config: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    return hermes_run(["kanban", "--board", str(config["board"]), "show", task_id], json_output=True)


def comment(config: Dict[str, Any], task_id: str, text: str) -> None:
    hermes_run(["kanban", "--board", str(config["board"]), "comment", task_id, text])


def block(config: Dict[str, Any], task_id: str, reason: str) -> None:
    hermes_run([
        "kanban", "--board", str(config["board"]), "block", "--kind", "needs_input", task_id, reason,
    ])


def unblock(config: Dict[str, Any], task_id: str, reason: str) -> None:
    hermes_run([
        "kanban", "--board", str(config["board"]), "unblock", task_id, "--reason", reason,
    ])


def reassign(config: Dict[str, Any], task_id: str, profile: str, reason: str) -> None:
    hermes_run([
        "kanban", "--board", str(config["board"]), "reassign", task_id, profile, "--reason", reason,
    ])


def link(config: Dict[str, Any], parent_id: str, child_id: str) -> None:
    hermes_run(["kanban", "--board", str(config["board"]), "link", parent_id, child_id])


def unlink(config: Dict[str, Any], parent_id: str, child_id: str) -> None:
    hermes_run(["kanban", "--board", str(config["board"]), "unlink", parent_id, child_id])


def reconcile_links(
    config: Dict[str, Any], issue: Dict[str, Any], task: Dict[str, Any],
    task_by_source: Dict[int, Dict[str, Any]], changes: List[str],
) -> None:
    if not config.get("reconciliation", {}).get("manage_execution_dependency_links", True):
        return
    dependencies = issue_dependencies(issue)
    if not dependencies and "Forgejo managed execution dependencies: true" not in str(task.get("body", "")):
        return
    missing = [number for number in dependencies if number not in task_by_source]
    if missing:
        changes.append(f"#{issue['number']} dependency tasks missing: {','.join(map(str, missing))}")
        return
    details = task_show(config, str(task["id"]))
    current_parents = set(details.get("parents", []))
    desired_parents = {
        str(task_by_source[number]["id"]) for number in dependencies
    }
    for parent_id in sorted(desired_parents - current_parents):
        link(config, parent_id, str(task["id"]))
        changes.append(f"linked execution dependency {parent_id}->{task['id']}")
    if "Forgejo managed execution dependencies: true" in str(task.get("body", "")):
        for parent_id in sorted(current_parents - desired_parents):
            unlink(config, parent_id, str(task["id"]))
            changes.append(f"unlinked stale execution dependency {parent_id}->{task['id']}")


def human_report(report: Dict[str, Any]) -> str:
    heading = "Forgejo backlog synchronizer"
    if report.get("dry_run"):
        heading += " (dry run)"
    lines = [
        heading,
        f"Board: {report.get('board', 'unknown')}",
        f"Forgejo issues: {report.get('forgejo_issues', 0)}",
        f"Actionable issues: {report.get('actionable', 0)}",
    ]

    created = report.get("created") or []
    lines.append(
        "Created tasks: " + (
            ", ".join(f"Forgejo #{number}" for number in created)
            if created else "none"
        )
    )

    deferred = report.get("deferred") or []
    lines.extend(["", "Deferred:"])
    if deferred:
        lines.extend(f"- {item}" for item in deferred)
    else:
        lines.append("- none")

    closed_sources = report.get("closed_sources") or []
    lines.extend(["", "Closed Forgejo source issues (not errors):"])
    if closed_sources:
        lines.append("- " + ", ".join(f"Forgejo #{number}" for number in closed_sources))
    else:
        lines.append("- none")

    excluded_sources = report.get("excluded_sources") or []
    lines.extend(["", "Excluded from execution policy (not errors):"])
    if excluded_sources:
        lines.extend(f"- {item}" for item in excluded_sources)
    else:
        lines.append("- none")

    orphaned = report.get("orphaned") or []
    lines.extend(["", "Orphaned Kanban tasks:"])
    if orphaned:
        lines.extend(f"- Forgejo #{number}" for number in orphaned)
    else:
        lines.append("- none")

    changes = report.get("changes") or []
    if changes:
        lines.extend(["", "Changes:"])
        lines.extend(f"- {change}" for change in changes)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", help="emit the legacy machine-readable report")
    args = parser.parse_args()
    config = load_json(Path(args.config))
    headers = credential_headers(config["source"])
    issues = fetch_issues(config, headers)
    issues_by_number = {int(issue["number"]): issue for issue in issues}
    tasks = hermes_run(["kanban", "--board", str(config["board"]), "list"], json_output=True)
    task_by_source: Dict[int, Dict[str, Any]] = {}
    for task in tasks:
        number = task_source_number(task)
        if number is not None and number not in task_by_source:
            task_by_source[number] = task
    state_path = Path(str(config["state_file"])).expanduser()
    state = load_json(state_path) if state_path.exists() else {"sources": {}}
    state.setdefault("sources", {})
    changes: List[str] = []
    deferred: List[str] = []
    created: List[int] = []
    inactive: List[int] = []
    closed_sources = sorted(
        int(issue["number"])
        for issue in issues
        if str(issue.get("state", "")).lower() == "closed"
    )
    excluded_sources = []
    for issue in issues:
        if str(issue.get("state", "")).lower() != "open":
            continue
        labels = sorted(issue_labels(issue) & set(config.get("excluded_labels", [])))
        if labels:
            excluded_sources.append(
                f"Forgejo #{issue['number']} ({','.join(labels)})"
            )

    actionable_numbers = {
        number for number, issue in issues_by_number.items()
        if issue_is_actionable(config, issue)
    }
    ordered_issues = sorted(
        issues,
        key=lambda issue: (-priority(issue, config), int(issue["number"])),
    )

    for issue in ordered_issues:
        number = int(issue["number"])
        key = source_key(config, number)
        record = state["sources"].setdefault(key, {})
        task = task_by_source.get(number)
        current_fingerprint = fingerprint(issue)
        if task is None and number in actionable_numbers:
            dependencies = issue_dependencies(issue)
            missing = [dep for dep in dependencies if dep not in task_by_source]
            if missing:
                deferred.append(f"#{number} waits for execution dependencies {','.join(map(str, missing))}")
                continue
            dependency_ids = [str(task_by_source[dep]["id"]) for dep in dependencies]
            if args.dry_run:
                created.append(number)
                continue
            task = create_task(config, issue, dependency_ids)
            task_by_source[number] = task
            tasks.append(task)
            created.append(number)
            changes.append(f"created Kanban task {task['id']} for Forgejo #{number}")
        if task is None:
            continue

        task_status = str(task.get("status", ""))
        task_fp = task_fingerprint(task)
        previous_fp = record.get("fingerprint") or task_fp
        metadata_marker_missing = task_fp is None and not record.get("metadata_comment")
        if metadata_marker_missing:
            record["metadata_comment"] = True
            if not args.dry_run:
                comment(config, str(task["id"]), "[forgejo-sync] source_key=" + key + " fingerprint=" + current_fingerprint + " grouping_parent=" + str(issue_grouping_parent(issue)) + " execution_dependencies=" + ",".join(map(str, issue_dependencies(issue))) + " (Parent is grouping metadata only; only Depends on/Gated by become Kanban execution links.)")
                changes.append(f"recorded durable source identity for {task['id']}")
        if previous_fp != current_fingerprint:
            if not args.dry_run:
                comment(config, str(task["id"]), f"[forgejo-sync] source changed: {key} fingerprint={current_fingerprint} state={issue.get('state')} labels={','.join(sorted(issue_labels(issue)))}; re-read the Forgejo issue before continuing.")
            if (
                str(record.get("state", "")).lower() == "open"
                and str(issue.get("state", "")).lower() == "closed"
            ):
                changes.append(f"Forgejo source closed; updated {task['id']}")
            else:
                changes.append(f"source changed for {task['id']}")

        desired = desired_profile(config, issue)
        if (
            config.get("reconciliation", {}).get("reassign_queued_tasks", True)
            and task_status in QUEUE_STATUSES
            and str(task.get("assignee")) != desired
        ):
            if not args.dry_run:
                reassign(config, str(task["id"]), desired, f"[forgejo-sync] source assignment/label mapping now selects {desired}")
            changes.append(f"reassigned {task['id']} to {desired}")

        active = number in actionable_numbers
        if active:
            if task_status == "blocked" and record.get("auto_blocked"):
                if config.get("reconciliation", {}).get("auto_unblock_sync_blocked", True):
                    if not args.dry_run:
                        unblock(config, str(task["id"]), "[forgejo-sync] source is actionable again")
                    changes.append(f"unblocked {task['id']} after source reopened")
                    record["auto_blocked"] = False
        else:
            inactive.append(number)
            reason = f"[forgejo-sync:auto-block] Forgejo source {key} is {issue.get('state')} with labels={','.join(sorted(issue_labels(issue)))}; execution is paused until the source is reopened or human disposition is recorded."
            if task_status in QUEUE_STATUSES and config.get("reconciliation", {}).get("auto_block_queued_source_inactive", True):
                if not record.get("auto_blocked"):
                    if not args.dry_run:
                        block(config, str(task["id"]), reason)
                    changes.append(f"blocked {task['id']} because source is inactive")
                    record["auto_blocked"] = True
            elif task_status in ACTIVE_REVIEW_STATUSES and config.get("reconciliation", {}).get("comment_running_source_inactive", True):
                if record.get("inactive_notice_fingerprint") != current_fingerprint:
                    if not args.dry_run:
                        comment(config, str(task["id"]), reason + " Active work was not interrupted automatically.")
                    changes.append(f"notified active task {task['id']} about inactive source")
                    record["inactive_notice_fingerprint"] = current_fingerprint

        if not args.dry_run:
            reconcile_links(config, issue, task, task_by_source, changes)
        record.update({
            "task_id": task.get("id"),
            "issue_number": number,
            "fingerprint": current_fingerprint,
            "state": issue.get("state"),
            "labels": sorted(issue_labels(issue)),
            "grouping_parent": issue_grouping_parent(issue),
            "execution_dependencies": issue_dependencies(issue),
            "last_assignee": desired,
        })

    orphaned = sorted(number for number in task_by_source if number not in issues_by_number)
    if orphaned:
        changes.append("orphaned Kanban source tasks: " + ",".join(f"#{n}" for n in orphaned))
    state["last_run"] = {
        "forgejo_issue_count": len(issues),
        "actionable_issue_count": len(actionable_numbers),
        "inactive_issue_count": len(inactive),
        "orphaned_task_count": len(orphaned),
    }
    if not args.dry_run:
        atomic_json_write(state_path, state)
    if changes or created or deferred or (args.dry_run and inactive):
        report = {
            "board": config["board"],
            "forgejo_issues": len(issues),
            "actionable": len(actionable_numbers),
            "created": created,
            "deferred": deferred,
            "inactive": inactive,
            "closed_sources": closed_sources,
            "excluded_sources": excluded_sources,
            "orphaned": orphaned,
            "changes": changes,
            "dry_run": args.dry_run,
        }
        if args.json:
            print(json.dumps(report, sort_keys=True))
        else:
            print(human_report(report))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Forgejo-Kanban reconciler ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
