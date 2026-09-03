"""Tests for the human-readable release snapshot classification."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "vanillacore_ai_gateway_snapshot_report.py"
spec = importlib.util.spec_from_file_location("snapshot_report", SCRIPT)
assert spec is not None and spec.loader is not None
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Verdict: APPROVED", "APPROVED"),
        ("Overall Verdict=APPROVED", "APPROVED"),
        ("Review Verdict - APPROVED", "APPROVED"),
        ("# Verdict: APPROVED", "APPROVED"),
        ("## Overall Verdict = APPROVED", "APPROVED"),
        ("### Review Verdict - APPROVED", "APPROVED"),
        ("Verdict: CHANGES REQUESTED", "CHANGES_REQUESTED"),
        ("Overall Verdict=CHANGES-REQUESTED", "CHANGES_REQUESTED"),
        ("Review Verdict - REVIEW_INCOMPLETE", "REVIEW-INCOMPLETE"),
        ("CHANGES_REQUESTED", "CHANGES_REQUESTED"),
        ("CHANGES-REQUESTED", "CHANGES_REQUESTED"),
        ("CHANGES REQUESTED", "CHANGES_REQUESTED"),
        ("REVIEW-INCOMPLETE", "REVIEW-INCOMPLETE"),
        ("REVIEW_INCOMPLETE", "REVIEW-INCOMPLETE"),
        ("REVIEW INCOMPLETE", "REVIEW-INCOMPLETE"),
        ("APPROVED", "APPROVED"),
        ("APPROVED.", "APPROVED"),
        ("APPROVED: all checks passed", "APPROVED"),
        ("APPROVED: candidate is ready", "APPROVED"),
        ("APPROVED - all checks passed", "APPROVED"),
        ("APPROVED\nAll checks passed", "APPROVED"),
        ("Verdict: APPROVED\nThe candidate is ready", "APPROVED"),
    ],
)
def test_text_verdict_accepts_supported_top_level_terminal_forms(text, expected):
    assert report._text_verdict(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Verdict: APPROVED\nCHANGES_REQUESTED: current review",
            "CHANGES_REQUESTED",
        ),
        (
            "CHANGES_REQUESTED: current review\nVerdict: APPROVED",
            "CHANGES_REQUESTED",
        ),
        (
            "Verdict: APPROVED\nREVIEW-INCOMPLETE: timeout",
            "REVIEW-INCOMPLETE",
        ),
        (
            "REVIEW_INCOMPLETE: timeout\nVerdict: APPROVED",
            "REVIEW-INCOMPLETE",
        ),
        ("Verdict: APPROVED\nThis result is not approved", None),
        ("This result is not approved\nVerdict: APPROVED", None),
        (
            "REVIEW-INCOMPLETE: timeout\nCHANGES REQUESTED: follow-up\nVerdict: APPROVED",
            "CHANGES_REQUESTED",
        ),
        (
            "CHANGES_REQUESTED: follow-up\nREVIEW-INCOMPLETE: timeout\nVerdict: APPROVED",
            "CHANGES_REQUESTED",
        ),
    ],
)
def test_text_verdict_fail_closed_precedence_and_negation(text, expected):
    assert report._text_verdict(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        'Quoted report says "Verdict: APPROVED"',
        '"Verdict: APPROVED"',
        "Historical verdict was APPROVED",
        "APPROVED was discussed as a possibility",
        "APPROVED? maybe not",
        "Is this APPROVED?",
        "Verdict: APPROVED (unconfirmed)",
        "Overall Verdict: APPROVED if checks pass",
        "No approval was recorded",
        "The review is not approved",
        "Review completed; no decision recorded.",
    ],
)
def test_text_verdict_rejects_incidental_uncertain_or_missing_approval(text):
    assert report._text_verdict(text) is None


def test_classify_issue_rejects_conflicting_approval_summary():
    projections = [
        {"id": "t_impl", "status": "done", "created_at": 10},
        {"id": "t_review", "status": "done", "created_at": 11},
    ]
    details = [
        {
            "task": {"id": "t_impl", "title": "Implementation", "body": ""},
            "runs": [],
            "comments": [],
        },
        {
            "task": {
                "id": "t_review",
                "title": "Review synthesis #42",
                "body": "review_type: bounded fan-in\nimplementation_task: t_impl",
            },
            "runs": [
                {
                    "id": 99,
                    "profile": "reviewer",
                    "status": "done",
                    "outcome": "completed",
                    "summary": "Verdict: APPROVED\nCHANGES_REQUESTED: current review",
                    "ended_at": 20,
                }
            ],
            "comments": [],
        },
    ]

    result = report.classify_issue(42, projections, details)

    assert result["eligible"] is False
    assert result["review_verdict"] == "CHANGES_REQUESTED"



def test_done_kanban_projections_with_changes_requested_are_not_closure_ready():
    projections = [
        {"id": "t_impl", "status": "done"},
        {"id": "t_review", "status": "done"},
    ]
    details = [
        {
            "task": {"id": "t_impl", "title": "Forgejo #8 implementation", "body": ""},
            "runs": [],
            "comments": [],
        },
        {
            "task": {
                "id": "t_review",
                "title": "Review synthesis Forgejo #8",
                "body": "review_type: bounded fan-in\nimplementation_task: t_impl",
            },
            "runs": [
                {
                    "id": 7,
                    "profile": "reviewer",
                    "status": "done",
                    "outcome": "completed",
                    "summary": "CHANGES_REQUESTED: DNS rebinding remains possible",
                    "ended_at": 20,
                }
            ],
            "comments": [],
        },
    ]

    result = report.classify_issue(8, projections, details)

    assert result["eligible"] is False
    assert result["review_verdict"] == "CHANGES_REQUESTED"
    assert result["reason"] == "local work is finished, but independent review found changes that are required before closure"


def test_open_issue_with_unfinished_projection_is_not_closure_ready():
    projections = [{"id": "t_review", "status": "blocked"}]
    details = [
        {
            "task": {"id": "t_review", "title": "Review leaf Forgejo #42", "body": "review leaf"},
            "runs": [
                {
                    "id": 8,
                    "profile": "reviewer",
                    "status": "timed_out",
                    "outcome": "timed_out",
                    "summary": "",
                    "ended_at": 30,
                }
            ],
            "comments": [],
        }
    ]

    result = report.classify_issue(42, projections, details)

    assert result["eligible"] is False
    assert result["review_verdict"] == "REVIEW-INCOMPLETE"
    assert result["reason"] == "local work is finished, but independent review is incomplete"


def test_metadata_review_incomplete_verdict_stays_fail_closed():
    projections = [{"id": "t_review", "status": "done"}]
    details = [
        {
            "task": {
                "id": "t_review",
                "title": "Review synthesis Forgejo #42",
                "body": "review_type: bounded fan-in",
            },
            "runs": [
                {
                    "id": 12,
                    "profile": "reviewer",
                    "status": "done",
                    "outcome": "completed",
                    "summary": "Terminal review result recorded in structured metadata.",
                    "metadata": {"verdict": "REVIEW-INCOMPLETE"},
                    "ended_at": 40,
                }
            ],
            "comments": [],
        }
    ]

    result = report.classify_issue(42, projections, details)

    assert result["eligible"] is False
    assert result["review_verdict"] == "REVIEW-INCOMPLETE"
    assert result["reason"] == "local work is finished, but independent review is incomplete"


def test_review_rework_title_is_not_misclassified_as_review_work():
    task = {
        "id": "t_rework",
        "title": "Review rework Forgejo #42 clean-checkout gate",
        "body": "implementation_task: t_impl",
    }

    assert report._is_review_projection(task, {"task": task}) is False


def test_active_rework_does_not_report_historical_review_timeouts_as_current_work():
    projections = [
        {"id": "t_impl", "status": "done"},
        {"id": "t_timeout", "status": "blocked"},
        {"id": "t_rework", "status": "ready"},
        {"id": "t_fanin", "status": "todo"},
    ]
    details = [
        {
            "task": {"id": "t_impl", "title": "Forgejo #42 implementation", "body": ""},
            "runs": [],
            "comments": [],
        },
        {
            "task": {"id": "t_timeout", "title": "Review leaf Forgejo #42", "body": "review_type: read-only\nimplementation_task: t_impl"},
            "runs": [
                {
                    "id": 9,
                    "profile": "reviewer",
                    "status": "timed_out",
                    "outcome": "timed_out",
                    "summary": "",
                    "ended_at": 30,
                }
            ],
            "comments": [],
        },
        {
            "task": {
                "id": "t_rework",
                "title": "Rework Forgejo #42 clean-checkout gate",
                "body": "implementation_task: t_impl",
            },
            "runs": [],
            "comments": [],
        },
        {
            "task": {
                "id": "t_fanin",
                "title": "Review synthesis Forgejo #42",
                "body": "review_type: bounded fan-in",
            },
            "runs": [],
            "comments": [],
        },
    ]

    result = report.classify_issue(42, projections, details)

    assert result["eligible"] is False
    assert result["review_verdict"] == "REVIEW-INCOMPLETE"
    assert result["reason"] == "implementation rework is active (1 queued task); independent review remains incomplete"


def test_superseded_review_timeout_does_not_block_later_approved_fan_in():
    projections = [
        {"id": "t_impl", "status": "done", "created_at": 10},
        {"id": "t_timeout", "status": "blocked", "created_at": 11},
        {"id": "t_fanin", "status": "done", "created_at": 12},
    ]
    details = [
        {
            "task": {"id": "t_impl", "title": "Forgejo #8 implementation", "body": ""},
            "runs": [],
            "comments": [],
        },
        {
            "task": {
                "id": "t_timeout",
                "title": "Fresh independent review: Forgejo #8",
                "body": "reviewer_profile: reviewer\nimplementation_task: t_impl\nread_only_source: true",
            },
            "runs": [
                {
                    "id": 10,
                    "profile": "reviewer",
                    "status": "timed_out",
                    "outcome": "timed_out",
                    "ended_at": 20,
                }
            ],
            "comments": [],
        },
        {
            "task": {
                "id": "t_fanin",
                "title": "Review synthesis Forgejo #8",
                "body": "review_type: bounded fan-in\nimplementation_task: t_impl\npreserves_timed_out_leaf: t_timeout",
            },
            "runs": [
                {
                    "id": 11,
                    "profile": "default",
                    "status": "done",
                    "outcome": "completed",
                    "summary": "APPROVED: all replacement leaves are covered",
                    "ended_at": 30,
                }
            ],
            "comments": [],
        },
    ]

    result = report.classify_issue(8, projections, details)

    assert result["eligible"] is True
    assert result["review_verdict"] == "APPROVED"


def test_plain_report_explains_source_labels_and_closure_gate():
    issues = [
        {"number": 8, "state": "open", "title": "Trusted endpoints"},
        {"number": 42, "state": "open", "title": "Release gate"},
    ]
    results = [
        {
            "number": 8,
            "title": "Trusted endpoints",
            "eligible": False,
            "reason": "local work is finished, but independent review found changes that are required before closure",
            "review_verdict": "CHANGES_REQUESTED",
        },
        {
            "number": 42,
            "title": "Release gate",
            "eligible": False,
            "reason": "local work is unfinished (1 blocked task)",
            "review_verdict": "REVIEW-INCOMPLETE",
        },
    ]

    text = report.render_report(issues, results)

    assert "Forgejo currently marks these open issues as release blockers: #8, #42." in text
    assert "#8 — Trusted endpoints: local work is finished, but independent review found changes that are required before closure." in text
    assert "#42 — Release gate: local work is unfinished (1 blocked task)." in text
    assert "An issue is closed only after all local work is complete and an independent review approves it." in text
    assert "The closure process then closes Forgejo and confirms the result." in text
