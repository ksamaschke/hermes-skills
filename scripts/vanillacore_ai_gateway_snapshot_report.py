"""Plain-English classification and rendering for the release snapshot."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional


INDEPENDENT_PROFILES = {"reviewer", "vanillacore-reviewer", "code-reviewer"}
VALID_VERDICTS = {"APPROVED", "CHANGES_REQUESTED", "REVIEW-INCOMPLETE"}


def _metadata(run: Dict[str, Any]) -> Dict[str, Any]:
    value = run.get("metadata")
    if isinstance(value, dict):
        return value
    if value:
        try:
            import json

            parsed = json.loads(str(value))
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _normalise_verdict(value: Any) -> Optional[str]:
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return text if text in VALID_VERDICTS else None


def _text_verdict(text: str) -> Optional[str]:
    if re.search(r"\bCHANGES[_ ]REQUESTED\b", text, re.IGNORECASE):
        return "CHANGES_REQUESTED"
    if re.search(r"\bREVIEW[- ]INCOMPLETE\b", text, re.IGNORECASE):
        return "REVIEW-INCOMPLETE"
    if re.search(
        r"\b(?:no|not|never|without|un)\s+approval|not\s+approved|unapproved",
        text,
        re.IGNORECASE,
    ):
        return None
    if re.search(r"\bAPPROVED\b", text, re.IGNORECASE):
        return "APPROVED"
    return None


def _event_time(value: Dict[str, Any]) -> float:
    for key in ("ended_at", "created_at", "started_at"):
        raw = value.get(key)
        if isinstance(raw, (int, float)):
            return float(raw)
    return 0.0


def _review_events(detail: Dict[str, Any]) -> List[Dict[str, Any]]:
    task = detail.get("task") or {}
    body = str(task.get("body") or "").lower()
    review_task = "review" in str(task.get("title") or "").lower() or "review_type:" in body
    events: List[Dict[str, Any]] = []
    for run in detail.get("runs") or []:
        if not isinstance(run, dict):
            continue
        profile = str(run.get("profile") or "")
        if profile not in INDEPENDENT_PROFILES and not review_task:
            continue
        metadata = _metadata(run)
        verdict = next(
            (
                _normalise_verdict(metadata.get(key))
                for key in ("verdict", "review_outcome", "overall_verdict")
                if _normalise_verdict(metadata.get(key))
            ),
            None,
        )
        if verdict is None:
            verdict = _text_verdict(str(run.get("summary") or ""))
        if verdict is None and str(run.get("status") or run.get("outcome") or "").lower() in {"timed_out", "gave_up", "crashed", "failed"}:
            verdict = "REVIEW-INCOMPLETE"
        if verdict:
            events.append({"time": _event_time(run), "verdict": verdict, "source": f"run {run.get('id')}"})
    for comment in detail.get("comments") or []:
        if not isinstance(comment, dict):
            continue
        author = str(comment.get("author") or "")
        if author not in INDEPENDENT_PROFILES:
            continue
        verdict = _text_verdict(str(comment.get("body") or ""))
        if verdict:
            events.append({"time": _event_time(comment), "verdict": verdict, "source": "review comment"})
    return sorted(events, key=lambda event: (event["time"], event["source"]))


def _is_review_projection(task: Dict[str, Any], detail: Dict[str, Any]) -> bool:
    """Identify review/fan-in cards so their history is not counted as coding work."""
    task_row = detail.get("task") or task
    title = str(task_row.get("title") or task.get("title") or "").lower()
    body = str(task_row.get("body") or task.get("body") or "").lower()
    return (
        title.startswith("review ")
        or "review leaf" in title
        or "review synthesis" in title
        or "review continuation" in title
        or "review_type:" in body
        or "review synthesis / bounded fan-in" in body
        or (
            ("fresh review" in title or "fresh independent review" in title)
            and ("reviewer profile:" in body or "reviewer_profile:" in body)
            and ("read_only_source: true" in body or "read-only" in body)
        )
    )


def _status_counts(rows: Iterable[Dict[str, Any]]) -> str:
    labels = {
        "running": "running",
        "ready": "queued",
        "todo": "waiting for a dependency",
        "blocked": "blocked",
        "review": "awaiting review",
        "scheduled": "scheduled",
    }
    counts: Dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown").lower()
        label = labels.get(status, status or "unknown")
        counts[label] = counts.get(label, 0) + 1
    return ", ".join(
        f"{count} {label}" + (" task" if count == 1 else " tasks")
        for label, count in sorted(counts.items())
    )


def _implementation_task_id(detail: Dict[str, Any]) -> Optional[str]:
    task = detail.get("task") or {}
    body = str(task.get("body") or "")
    match = re.search(
        r"(?im)^\s*implementation(?:_| )task\s*:\s*(t_[a-z0-9_-]+)",
        body,
    )
    return match.group(1) if match else None


def _current_implementation_ids(entries: List[Dict[str, Any]]) -> set[str]:
    implementation = [entry for entry in entries if not entry["is_review"]]
    if not implementation:
        return set()
    newest = max(float(entry["task"].get("created_at") or 0) for entry in implementation)
    return {
        str(entry["task"].get("id") or "")
        for entry in implementation
        if float(entry["task"].get("created_at") or 0) == newest
    }


def _belongs_to_generation(entry: Dict[str, Any], implementation_ids: set[str]) -> bool:
    if not entry["is_review"]:
        return str(entry["task"].get("id") or "") in implementation_ids
    if not implementation_ids:
        return True
    detail = entry["detail"]
    declared = _implementation_task_id(detail)
    if declared in implementation_ids:
        return True
    parents = detail.get("parents") or []
    return any(str(parent) in implementation_ids for parent in parents)


def _superseded_review_ids(entries: Iterable[Dict[str, Any]]) -> set[str]:
    superseded: set[str] = set()
    pattern = re.compile(
        r"(?im)^\s*(?:continuation_of|preserves_timed_out_leaf|replaces_review|supersedes_review)\s*:\s*(t_[a-z0-9_-]+)",
    )
    for entry in entries:
        task = entry["detail"].get("task") or entry["task"]
        for match in pattern.finditer(str(task.get("body") or "")):
            superseded.add(match.group(1))
    return superseded


def classify_issue(
    number: int,
    projections: Iterable[Dict[str, Any]],
    details: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    projection_list = list(projections)
    detail_list = list(details)
    statuses = [
        {"id": str(task.get("id") or "unknown"), "status": str(task.get("status") or "unknown")}
        for task in projection_list
    ]
    entries = []
    for index, task in enumerate(projection_list):
        detail = detail_list[index] if index < len(detail_list) else {"task": task}
        status = str(task.get("status") or "unknown").lower()
        entries.append({
            "task": task,
            "detail": detail,
            "status": status,
            "is_review": _is_review_projection(task, detail),
        })
    implementation_ids = _current_implementation_ids(entries)
    current_entries = [entry for entry in entries if _belongs_to_generation(entry, implementation_ids)]
    superseded = _superseded_review_ids(current_entries)
    current_review_entries = [entry for entry in current_entries if entry["is_review"]]

    events: List[Dict[str, Any]] = []
    for entry in current_review_entries:
        events.extend(_review_events(entry["detail"]))
    events.sort(key=lambda event: (event["time"], event["source"]))
    latest = events[-1] if events else None
    verdict = latest["verdict"] if latest else None

    active_implementation = [
        {"status": entry["status"]}
        for entry in current_entries
        if not entry["is_review"] and entry["status"] != "done"
    ]
    active_review = [
        {"status": entry["status"]}
        for entry in current_review_entries
        if entry["is_review"] and entry["status"] in {"running", "ready", "review"}
    ]
    waiting_review = [
        {"status": entry["status"]}
        for entry in current_review_entries
        if entry["is_review"] and entry["status"] == "todo"
    ]
    unresolved_review = [
        entry
        for entry in current_review_entries
        if entry["is_review"]
        and entry["status"] == "blocked"
        and str(entry["task"].get("id") or "") not in superseded
    ]

    if active_implementation:
        status_text = _status_counts(active_implementation)
        reason = f"implementation rework is active ({status_text})"
        if verdict == "CHANGES_REQUESTED":
            reason += "; independent review found changes that are required before closure"
        elif verdict == "REVIEW-INCOMPLETE":
            reason += "; independent review remains incomplete"
        else:
            reason += "; independent review remains required"
    elif active_review:
        reason = f"independent review is active ({_status_counts(active_review)})"
    elif waiting_review:
        reason = f"independent review is waiting for dependencies ({_status_counts(waiting_review)})"
    elif unresolved_review:
        reason = "local work is finished, but independent review is incomplete"
    elif verdict == "CHANGES_REQUESTED":
        reason = "local work is finished, but independent review found changes that are required before closure"
    elif verdict == "REVIEW-INCOMPLETE":
        reason = "local work is finished, but independent review is incomplete"
    elif verdict != "APPROVED":
        reason = "local tasks finished, but no independent approval was recorded"
    else:
        reason = "local tasks finished and independent review approved the result"

    effective_non_done = active_implementation + active_review + waiting_review + [
        {"status": "blocked"} for _ in unresolved_review
    ]

    return {
        "number": number,
        "projection_count": len(projection_list),
        "statuses": statuses,
        "eligible": not effective_non_done and verdict == "APPROVED",
        "review_verdict": verdict,
        "review_evidence": latest["source"] if latest else None,
        "reason": reason,
    }


def render_report(
    milestone_issues: Iterable[Dict[str, Any]],
    labeled_results: Iterable[Dict[str, Any]],
) -> str:
    issues = list(milestone_issues)
    results = sorted(labeled_results, key=lambda result: int(result["number"]))
    open_count = sum(str(issue.get("state") or "").lower() == "open" for issue in issues)
    closed_count = sum(str(issue.get("state") or "").lower() == "closed" for issue in issues)
    labeled_numbers = ", ".join(f"#{result['number']}" for result in results) or "none"

    lines = [
        "VanillaCore AI Gateway progress",
        f"Forgejo milestone: {len(issues)} issues total ({open_count} open, {closed_count} closed).",
    ]
    if results:
        lines.append(f"Forgejo currently marks these open issues as release blockers: {labeled_numbers}.")
        lines.extend(["", "Release-blocker status:"])
        for result in results:
            title = str(result.get("title") or "Untitled issue")
            lines.append(f"- #{result['number']} — {title}: {result['reason']}.")
    else:
        lines.append("Forgejo currently has no open issue marked as a release blocker.")

    lines.extend(
        [
            "",
            "How to read this:",
            "- The release-blocker label is only a Forgejo tag; it does not close or approve an issue.",
            "- “Local tasks finished” means the Kanban execution cards reached done. It does not mean the Forgejo issue is closed.",
            "- An issue is closed only after all local work is complete and an independent review approves it.",
            "- The closure process then closes Forgejo and confirms the result.",
        ]
    )
    return "\n".join(lines)
