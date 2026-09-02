"""Focused tests for the portable Forgejo delivery observer."""

from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from unittest import mock

import pytest
import yaml


SCRIPT = Path(__file__).parents[1] / "scripts" / "forgejo_delivery_controller.py"
EXAMPLE_OVERLAY = Path(__file__).parents[1] / "examples" / "forgejo-delivery-overlay.json"
CRON_SUPERVISION = Path(__file__).parents[1] / "examples" / "factory-cron-supervision.yaml"
LIFECYCLE = Path(__file__).parents[1] / "docs" / "factory-delivery-lifecycle.md"
SPEC = importlib.util.spec_from_file_location("forgejo_delivery_controller_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
controller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controller)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
SHA = "1" * 40


def _config(*, include_closed: bool = False) -> dict[str, Any]:
    return {
        "version": 1,
        "project": {
            "board": "example-board",
            "project": "example-project",
            "profiles": {
                "integration_operator": "example-integration-operator",
                "release_operator": "example-release-operator",
                "infrastructure_recovery_operator": "example-infrastructure-operator",
            },
        },
        "forgejo": {
            "base_url": "https://forgejo.example.invalid/api/v1",
            "repository": "example/project",
            "user_agent": "hermes-factory-delivery-controller/1.0",
            "accept": "application/json",
            "credential_helper": {
                "protocol": "https",
                "host": "forgejo.example.invalid",
                "path": "example/project",
                "cache_path": "/tmp/example-forgejo-delivery-credentials.json",
            },
        },
        "branches": {
            "target_branches": ["main"],
            "source_prefixes": ["factory/"],
            "excluded_branches": ["factory/excluded"],
        },
        "review": {
            "vendor_family_separation": "preferred",
            "implementer": {
                "profile": "example-implementer",
                "vendor_family": "example-family",
            },
            "reviewer": {
                "profile": "example-reviewer",
                "vendor_family": "example-family",
            },
        },
        "runners": {
            "scopes": [
                {
                    "name": "repository-visible",
                    "kind": "repository",
                    "endpoint": "/repos/{owner}/{repo}/actions/runners",
                    "visible": True,
                }
            ],
            "required_labels": ["ubuntu-latest"],
        },
        "ci": {"required_contexts": ["ci/package"]},
        "stale_thresholds": {
            "open_pr_no_completed_ci_seconds": 3600,
            "pushed_no_pr_seconds": 3600,
            "ready_for_integration_seconds": 7200,
        },
        "pull_requests": {
            "include_closed": include_closed,
            "max_open": 20,
            "max_closed": 20,
        },
        "limits": {
            "page_size": 2,
            "max_pages": 5,
            "max_branches": 100,
            "max_branch_commit_lookups": 20,
            "max_text_chars": 96,
            "max_output_bytes": 16384,
        },
    }


class _Response:
    def __init__(self, payload: Any):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class FakeClient:
    """Read-only fake implementing the controller's client boundary."""

    def __init__(
        self,
        *,
        open_prs: list[dict[str, Any]],
        runners: dict[str, list[dict[str, Any]]],
        details: dict[int, dict[str, Any]],
        statuses: dict[str, dict[str, Any]],
        closed_prs: list[dict[str, Any]] | None = None,
        branches: list[dict[str, Any]] | None = None,
        commit_metadata: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.open_prs = open_prs
        self.closed_prs = closed_prs or []
        self.branches = branches or []
        self.commit_metadata = commit_metadata or {}
        self.runners = runners
        self.details = details
        self.statuses = statuses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def paginate(
        self,
        path: str,
        query: dict[str, Any] | None = None,
        *,
        inventory: str,
        max_items: int,
    ) -> list[dict[str, Any]]:
        params = dict(query or {})
        self.calls.append(("GET-PAGES", path, params))
        if inventory == "open pull requests":
            rows = list(self.open_prs)
        elif inventory == "closed pull requests":
            rows = list(self.closed_prs)
        elif inventory == "repository branches":
            rows = list(self.branches)
        elif inventory.startswith("runners:"):
            rows = list(self.runners[path])
        else:
            raise AssertionError(f"unexpected inventory {inventory}")
        if len(rows) > max_items:
            raise controller.InventoryError(
                f"complete {inventory} inventory exceeds configured maximum of {max_items}"
            )
        return rows

    def get_json(
        self, path: str, query: dict[str, Any] | None = None
    ) -> Any:
        params = dict(query or {})
        self.calls.append(("GET", path, params))
        if "/pulls/" in path:
            return dict(self.details[int(path.rsplit("/", 1)[1])])
        if path.endswith("/status"):
            sha = path.rsplit("/", 2)[1]
            return dict(self.statuses[sha])
        if path.endswith("/commits"):
            return [dict(self.commit_metadata[str(params["sha"])])]
        raise AssertionError(f"unexpected GET {path}")


def _pr(
    number: int = 7,
    *,
    state: str = "open",
    created_at: str = "2026-09-02T11:30:00Z",
    merged: bool = False,
    mergeable: bool = True,
    head: str = "factory/change-7",
    sha: str = SHA,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": "Portable delivery observation",
        "html_url": f"https://forgejo.example.invalid/example/project/pulls/{number}",
        "state": state,
        "created_at": created_at,
        "updated_at": created_at,
        "closed_at": created_at if state == "closed" else None,
        "merged_at": created_at if merged else None,
        "merged": merged,
        "mergeable": mergeable,
        "merge_commit_sha": "2" * 40 if merged else None,
        "base": {"ref": "main", "sha": "0" * 40},
        "head": {"ref": head, "sha": sha},
    }


def _status(value: str, *, context: str = "ci/package") -> dict[str, Any]:
    return {
        "sha": SHA,
        "state": value,
        "total_count": 1,
        "statuses": [
            {
                "context": context,
                "status": value,
                "updated_at": "2026-09-02T11:45:00Z",
            }
        ],
    }


def _branch(
    name: str,
    *,
    sha: str | None = "8" * 40,
    timestamp: str | None = "2026-09-02T11:50:00Z",
) -> dict[str, Any]:
    commit: dict[str, Any] = {}
    if sha is not None:
        commit["id"] = sha
    if timestamp is not None:
        commit["timestamp"] = timestamp
    return {"name": name, "commit": commit}


def _observe(
    status: dict[str, Any],
    *,
    pr: dict[str, Any] | None = None,
    runners: list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
    closed_prs: list[dict[str, Any]] | None = None,
    branches: list[dict[str, Any]] | None = None,
    commit_metadata: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], FakeClient]:
    cfg = config or _config()
    detail = pr or _pr()
    scope = "/repos/example/project/actions/runners"
    client = FakeClient(
        open_prs=[{"number": detail["number"]}],
        closed_prs=closed_prs,
        runners={
            scope: runners
            if runners is not None
            else [{"id": 1, "name": "repo-runner", "status": "idle", "labels": ["ubuntu-latest:host"]}]
        },
        details={int(detail["number"]): detail, **{int(row["number"]): row for row in (closed_prs or [])}},
        statuses={str(detail["head"]["sha"]): status},
        branches=branches,
        commit_metadata=commit_metadata,
    )
    return controller.observe(cfg, client=client, now=NOW), client


def test_http_client_paginates_with_explicit_headers_and_visible_true() -> None:
    requests = []
    pages = {
        1: [
            {"id": 1, "name": "one", "status": "idle", "labels": ["linux"]},
            {"id": 2, "name": "two", "status": "active", "labels": ["linux"]},
        ],
        2: [{"id": 3, "name": "three", "status": "offline", "labels": ["macos"]}],
    }

    def open_url(request: Any, timeout: int) -> _Response:
        requests.append((request, timeout))
        page = int(parse_qs(urlsplit(request.full_url).query)["page"][0])
        return _Response(pages[page])

    client = controller.ForgejoClient(
        "https://forgejo.example.invalid/api/v1",
        {
            "Authorization": "Basic test-only",
            "User-Agent": "hermes-factory-delivery-controller/1.0",
            "Accept": "application/json",
        },
        page_size=2,
        max_pages=5,
        urlopen=open_url,
    )
    rows = client.paginate(
        "/repos/example/project/actions/runners",
        {"visible": True},
        inventory="runners:repository-visible",
        max_items=10,
    )

    assert [row["id"] for row in rows] == [1, 2, 3]
    assert len(requests) == 2
    for request, timeout in requests:
        headers = {key.lower(): value for key, value in request.header_items()}
        query = parse_qs(urlsplit(request.full_url).query)
        assert request.get_method() == "GET"
        assert headers["user-agent"] == "hermes-factory-delivery-controller/1.0"
        assert headers["accept"] == "application/json"
        assert headers["authorization"] == "Basic test-only"
        assert query["visible"] == ["true"]
        assert query["limit"] == ["2"]
        assert timeout == 30


def test_live_branch_inventory_paginates_all_pages_with_get_only() -> None:
    requests = []
    pages = {
        1: [_branch("main"), _branch("factory/change-8")],
        2: [_branch("factory/change-9", sha="9" * 40)],
    }

    def open_url(request: Any, timeout: int) -> _Response:
        requests.append((request, timeout))
        page = int(parse_qs(urlsplit(request.full_url).query)["page"][0])
        return _Response(pages[page])

    client = controller.ForgejoClient(
        "https://forgejo.example.invalid/api/v1",
        {
            "Authorization": "Basic test-only",
            "User-Agent": "hermes-factory-delivery-controller/1.0",
            "Accept": "application/json",
        },
        page_size=2,
        max_pages=5,
        urlopen=open_url,
    )

    rows = client.paginate(
        "/repos/example/project/branches",
        inventory="repository branches",
        max_items=10,
    )

    assert [row["name"] for row in rows] == [
        "main",
        "factory/change-8",
        "factory/change-9",
    ]
    assert len(requests) == 2
    assert {request.get_method() for request, _timeout in requests} == {"GET"}
    assert [
        parse_qs(urlsplit(request.full_url).query)["page"]
        for request, _timeout in requests
    ] == [["1"], ["2"]]


def test_credential_helper_input_populates_atomic_private_cache(tmp_path: Path) -> None:
    cache = tmp_path / "headers.json"
    cfg = _config()["forgejo"]
    cfg["credential_helper"]["cache_path"] = str(cache)
    completed = subprocess.CompletedProcess(
        ["git", "credential", "fill"],
        0,
        stdout="username=test-user\npassword=test-password\n",
        stderr="",
    )

    with mock.patch.object(controller.subprocess, "run", return_value=completed) as run:
        headers = controller.credential_headers(cfg)

    assert sorted(headers) == ["Accept", "Authorization", "User-Agent"]
    assert headers["User-Agent"] == cfg["user_agent"]
    assert headers["Accept"] == cfg["accept"]
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600
    assert json.loads(cache.read_text(encoding="utf-8")) == headers
    assert run.call_args.args[0] == ["git", "credential", "fill"]
    helper_input = run.call_args.kwargs["input"]
    assert helper_input == (
        "protocol=https\nhost=forgejo.example.invalid\npath=example/project\n\n"
    )
    environment = run.call_args.kwargs["env"]
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_ASKPASS"] == "/usr/bin/false"


def test_credential_helper_failure_uses_only_a_private_validated_cache(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "headers.json"
    cache.write_text(
        json.dumps(
            {
                "Accept": "old-value",
                "Authorization": "Basic cached-test-only",
                "User-Agent": "old-value",
            }
        ),
        encoding="utf-8",
    )
    cache.chmod(0o600)
    cfg = _config()["forgejo"]
    cfg["credential_helper"]["cache_path"] = str(cache)
    failure = subprocess.CalledProcessError(128, ["git", "credential", "fill"])

    with mock.patch.object(controller.subprocess, "run", side_effect=failure):
        headers = controller.credential_headers(cfg)

    assert headers == {
        "Accept": "application/json",
        "Authorization": "Basic cached-test-only",
        "User-Agent": "hermes-factory-delivery-controller/1.0",
    }


def test_required_runner_labels_classify_active_offline_busy_and_missing() -> None:
    result = controller.classify_runner_labels(
        ["active-label", "offline-label", "busy-label", "missing-label"],
        [
            {
                "name": "repository-visible",
                "kind": "repository",
                "runners": [
                    {"id": 1, "name": "shared-name", "status": "idle", "labels": ["active-label:host"]},
                    {"id": 2, "name": "offline", "status": "offline", "labels": ["offline-label"]},
                    {"id": 3, "name": "busy", "status": "active", "labels": ["busy-label"]},
                ],
            },
            {
                "name": "organization-diagnostic",
                "kind": "diagnostic",
                "runners": [
                    {"id": 99, "name": "shared-name", "status": "idle", "labels": ["missing-label"]}
                ],
            },
        ],
    )

    assert {label: row["state"] for label, row in result.items()} == {
        "active-label": "active",
        "offline-label": "offline",
        "busy-label": "busy",
        "missing-label": "missing",
    }
    assert result["missing-label"]["runner_ids"] == []


@pytest.mark.parametrize(
    ("status", "expected_stage", "expected_classification"),
    [
        ("pending", "waiting-for-CI", "ACTIVE"),
        ("success", "ready-for-integration", "ACTIVE"),
        ("failure", "CI-failed", "STALLED"),
    ],
)
def test_pending_success_and_failure_ci_stages(
    status: str, expected_stage: str, expected_classification: str
) -> None:
    report, _client = _observe(_status(status))

    assert report["pull_requests"][0]["stage"] == expected_stage
    assert report["pull_requests"][0]["ci"]["contexts"]["ci/package"] == status
    assert report["classification"] == expected_classification


def test_stale_open_pr_without_completed_ci_is_stalled() -> None:
    report, _client = _observe(
        {
            "sha": SHA,
            "state": "pending",
            "total_count": 1,
            "statuses": [
                {
                    "context": "ci/package",
                    "status": "pending",
                    "updated_at": "2026-09-02T09:00:00Z",
                }
            ],
        },
        pr=_pr(created_at="2026-09-02T09:00:00Z"),
    )

    assert report["classification"] == "STALLED"
    assert report["pull_requests"][0]["stage"] == "waiting-for-CI"
    assert "stale-no-completed-CI" in {
        blocker["code"] for blocker in report["pull_requests"][0]["blockers"]
    }
    assert any(
        action["action"] == "create-integration-recovery-task"
        and action["owner_profile"] == "example-integration-operator"
        and action["scope"]["pull_request"] == 7
        for action in report["actions"]
    )


def test_missing_repository_visible_runner_stalls_even_with_external_namesake() -> None:
    config = _config()
    config["runners"]["scopes"].append(
        {
            "name": "organization-diagnostic",
            "kind": "diagnostic",
            "endpoint": "/orgs/{owner}/actions/runners",
            "visible": True,
        }
    )
    client = FakeClient(
        open_prs=[{"number": 7}],
        runners={
            "/repos/example/project/actions/runners": [],
            "/orgs/example/actions/runners": [
                {
                    "id": 99,
                    "name": "repo-runner",
                    "status": "idle",
                    "labels": ["ubuntu-latest:host"],
                }
            ],
        },
        details={7: _pr()},
        statuses={SHA: _status("pending")},
    )

    report = controller.observe(config, client=client, now=NOW)

    assert report["classification"] == "STALLED"
    assert report["runner_labels"]["ubuntu-latest"]["state"] == "missing"
    assert report["pull_requests"][0]["stage"] == "waiting-for-runner"
    assert any(
        action["action"] == "create-infrastructure-recovery-task"
        and action["owner_profile"] == "example-infrastructure-operator"
        and action["scope"]["required_label"] == "ubuntu-latest"
        for action in report["actions"]
    )
    repository_calls = [
        call for call in client.calls if call[1] == "/repos/example/project/actions/runners"
    ]
    assert repository_calls[0][2]["visible"] is True


@pytest.mark.parametrize(
    ("policy", "expected_gate", "expected_classification"),
    [
        ("required", "blocked", "STALLED"),
        ("preferred", "advisory", "ACTIVE"),
        ("not_required", "not-required", "ACTIVE"),
    ],
)
def test_vendor_family_separation_only_gates_when_required(
    policy: str, expected_gate: str, expected_classification: str
) -> None:
    config = _config()
    config["review"]["vendor_family_separation"] = policy

    report, _client = _observe(_status("success"), config=config)

    assert report["review_route"] == {
        "vendor_family_separation": policy,
        "implementer": {
            "profile": "example-implementer",
            "vendor_family": "example-family",
        },
        "reviewer": {
            "profile": "example-reviewer",
            "vendor_family": "example-family",
        },
        "same_vendor_family": True,
        "gate": expected_gate,
    }
    assert report["classification"] == expected_classification
    codes = {blocker["code"] for blocker in report["blockers"]}
    if policy == "required":
        assert "required-vendor-family-separation-unavailable" in codes
    else:
        assert "required-vendor-family-separation-unavailable" not in codes


def test_live_pushed_no_pr_and_queried_merged_closed_states_are_preserved() -> None:
    config = _config(include_closed=True)
    merged = _pr(
        6,
        state="closed",
        created_at="2026-09-01T12:00:00Z",
        merged=True,
        sha="6" * 40,
    )
    report, _client = _observe(
        _status("success"),
        config=config,
        closed_prs=[merged],
        branches=[_branch("factory/change-8")],
    )

    assert report["pushed_candidates"][0]["stage"] == "pushed/no-PR"
    assert report["pushed_candidates"][0]["evidence_source"] == (
        "forgejo-branch-inventory"
    )
    assert report["closed_pull_requests"][0]["stage"] == "merged"
    assert any(
        action["action"] == "create-integration-task"
        and action["scope"]["head"] == "factory/change-8"
        for action in report["actions"]
    )
    assert any(
        action["action"] == "create-release-task"
        and action["owner_profile"] == "example-release-operator"
        and action["scope"]["pull_request"] == 6
        for action in report["actions"]
    )


def test_live_branch_discovery_excludes_targets_filters_and_all_pr_heads() -> None:
    config = _config(include_closed=False)
    open_pr = _pr(head="factory/open", sha="7" * 40)
    closed_pr = _pr(
        6,
        state="closed",
        head="factory/closed",
        sha="6" * 40,
    )
    client = FakeClient(
        open_prs=[{"number": 7}],
        closed_prs=[{"number": 6}],
        runners={
            "/repos/example/project/actions/runners": [
                {
                    "id": 1,
                    "name": "repo-runner",
                    "status": "idle",
                    "labels": ["ubuntu-latest:host"],
                }
            ]
        },
        details={7: open_pr, 6: closed_pr},
        statuses={"7" * 40: _status("success")},
        branches=[
            _branch("main"),
            _branch("docs/not-a-candidate"),
            _branch("factory/excluded"),
            _branch("factory/open", sha="7" * 40),
            _branch("factory/open-alias", sha="7" * 40),
            _branch("factory/closed", sha="6" * 40),
            _branch("factory/new", sha="9" * 40),
        ],
    )

    report = controller.observe(config, client=client, now=NOW)

    assert [row["branch"] for row in report["pushed_candidates"]] == [
        "factory/new"
    ]
    assert report["closed_pull_requests"] == []
    assert any(
        call[1] == "/repos/example/project/pulls"
        and call[2].get("state") == "closed"
        for call in client.calls
    )


def test_new_matching_branch_appears_on_next_tick_without_overlay_edit() -> None:
    config = _config()
    unchanged = json.loads(json.dumps(config))

    first, _first_client = _observe(
        _status("success"),
        config=config,
        branches=[],
    )
    second, _second_client = _observe(
        _status("success"),
        config=config,
        branches=[_branch("factory/new-on-next-tick", sha="9" * 40)],
    )

    assert first["pushed_candidates"] == []
    assert [row["branch"] for row in second["pushed_candidates"]] == [
        "factory/new-on-next-tick"
    ]
    assert config == unchanged


def test_branch_inventory_timestamp_drives_age_and_stale_no_pr_state() -> None:
    report, client = _observe(
        _status("success"),
        branches=[
            _branch(
                "factory/stale",
                sha="9" * 40,
                timestamp="2026-09-02T10:00:00Z",
            )
        ],
    )

    candidate = report["pushed_candidates"][0]
    assert candidate["pushed_at"] == "2026-09-02T10:00:00Z"
    assert candidate["age_seconds"] == 7200
    assert candidate["evidence_source"] == "forgejo-branch-inventory"
    assert report["classification"] == "STALLED"
    assert {row["code"] for row in candidate["blockers"]} == {
        "stale-pushed-no-PR"
    }
    assert not any(call[1].endswith("/commits") for call in client.calls)


def test_missing_branch_sha_and_timestamp_use_one_bounded_commit_lookup() -> None:
    report, client = _observe(
        _status("success"),
        branches=[_branch("factory/fallback", sha=None, timestamp=None)],
        commit_metadata={
            "factory/fallback": {
                "sha": "9" * 40,
                "created": "2026-09-02T11:50:00Z",
            }
        },
    )

    candidate = report["pushed_candidates"][0]
    assert candidate["sha"] == "9" * 40
    assert candidate["pushed_at"] == "2026-09-02T11:50:00Z"
    assert candidate["age_seconds"] == 600
    assert candidate["evidence_source"] == "forgejo-commit-metadata"
    assert [
        call
        for call in client.calls
        if call[1] == "/repos/example/project/commits"
    ] == [
        (
            "GET",
            "/repos/example/project/commits",
            {"sha": "factory/fallback", "limit": 1, "page": 1},
        )
    ]


def test_branch_inventory_and_commit_fallback_bounds_fail_closed() -> None:
    config = _config()
    config["limits"]["max_branches"] = 1
    with pytest.raises(controller.InventoryError, match="repository branches.*maximum"):
        _observe(
            _status("success"),
            config=config,
            branches=[_branch("factory/one"), _branch("factory/two")],
        )

    config = _config()
    config["limits"]["max_branch_commit_lookups"] = 1
    client = FakeClient(
        open_prs=[{"number": 7}],
        closed_prs=[],
        runners={
            "/repos/example/project/actions/runners": [
                {
                    "id": 1,
                    "name": "repo-runner",
                    "status": "idle",
                    "labels": ["ubuntu-latest:host"],
                }
            ]
        },
        details={7: _pr()},
        statuses={SHA: _status("success")},
        branches=[
            _branch("factory/one", sha=None, timestamp=None),
            _branch("factory/two", sha=None, timestamp=None),
        ],
    )

    with pytest.raises(controller.InventoryError, match="2 commit metadata lookups"):
        controller.observe(config, client=client, now=NOW)
    assert not any(call[1].endswith("/commits") for call in client.calls)


def test_report_is_bounded_and_observation_uses_only_read_operations() -> None:
    config = _config()
    config["limits"]["max_text_chars"] = 24
    long_pr = _pr()
    long_pr["title"] = "x" * 5000
    report, client = _observe(
        _status("success"),
        pr=long_pr,
        config=config,
        branches=[_branch("factory/fallback", sha="9" * 40, timestamp=None)],
        commit_metadata={
            "9" * 40: {
                "sha": "9" * 40,
                "created": "2026-09-02T11:50:00Z",
            }
        },
    )

    payload = controller.serialize_report(report, config["limits"]["max_output_bytes"])

    assert len(payload.encode("utf-8")) <= config["limits"]["max_output_bytes"]
    parsed = json.loads(payload)
    assert len(parsed["pull_requests"][0]["title"]) <= 24
    assert parsed["pull_requests"][0]["head_sha"] == SHA
    assert {method for method, _path, _query in client.calls} <= {"GET", "GET-PAGES"}
    assert all("visible" not in query or query["visible"] is True for _, _, query in client.calls)


def test_serialize_report_fails_closed_inside_a_tiny_output_budget() -> None:
    report = {
        "schema_version": 1,
        "classification": "ACTIVE",
        "pull_requests": [{"title": "x" * 5000}],
    }

    payload = controller.serialize_report(report, 512)
    parsed = json.loads(payload)

    assert len(payload.encode("utf-8")) <= 512
    assert parsed["classification"] == "STALLED"
    assert parsed["error"]["code"] == "output-limit-exceeded"


def test_example_overlay_is_valid_anonymized_and_installation_owned() -> None:
    text = EXAMPLE_OVERLAY.read_text(encoding="utf-8")
    config = json.loads(text)

    controller.validate_config(config)
    assert config["review"]["vendor_family_separation"] in {
        "required",
        "preferred",
        "not_required",
    }
    assert config["review"]["implementer"]["profile"]
    assert config["review"]["reviewer"]["profile"]
    assert config["project"]["profiles"]["integration_operator"]
    assert config["project"]["profiles"]["release_operator"]
    assert "pushed_candidates" not in config["branches"]
    assert set(config["branches"]) == {
        "target_branches",
        "source_prefixes",
        "excluded_branches",
    }
    assert config["limits"]["max_branches"] > 0
    assert config["limits"]["max_branch_commit_lookups"] >= 0
    # Keep historical project names out of the public fixture while still
    # guarding against them in the example.
    for forbidden in (
        "vanilla" + "core",
        "min" + "na",
        "sama" + "schke",
        "/users/",
        "sk-",
    ):
        assert forbidden not in text.lower()


def test_cron_and_lifecycle_wire_delivery_observation_to_one_supervisor() -> None:
    config = yaml.safe_load(CRON_SUPERVISION.read_text(encoding="utf-8"))
    controllers = config["controllers"]
    delivery = [row for row in controllers if row.get("observes") == ["forgejo-delivery-state"]]

    assert len(delivery) == 1
    assert delivery[0]["no_agent"] is True
    assert delivery[0]["deliver"] == "local"
    assert delivery[0]["id"] in config["supervisor"]["context_from"]
    prompt = config["supervisor"]["prompt"]
    assert "exact-scope integration or infrastructure-recovery task" in prompt
    assert "read back its task id, scope, status, and assignee" in prompt
    assert "vendor-family separation" in prompt
    assert "only when the project overlay marks it required" in prompt

    lifecycle = LIFECYCLE.read_text(encoding="utf-8")
    for phrase in (
        "forgejo_delivery_controller.py",
        "pushed/no-PR",
        "waiting-for-runner",
        "waiting-for-CI",
        "CI-failed",
        "ready-for-integration",
        "repository-visible",
        "vendor_family_separation",
        "required`, `preferred`, or `not_required",
        "exact-scope integration or infrastructure-recovery task",
    ):
        assert phrase in lifecycle
