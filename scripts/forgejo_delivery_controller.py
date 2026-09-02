#!/usr/bin/env python3
"""Read-only Forgejo delivery telemetry for a Hermes software factory.

The controller reads one project-owned JSON overlay, observes Forgejo through
GET requests, and emits one bounded JSON document. It never writes Forgejo,
Git, Kanban, CI, issue, merge, release, or deployment state. The project-level
LLM supervisor owns any successor task creation and must read its mutations
back separately.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
CLASSIFICATIONS = {"ACTIVE", "IDLE-BY-GATING", "STALLED"}
REVIEW_POLICIES = {"required", "preferred", "not_required"}
TERMINAL_CI_SUCCESS = {"success"}
TERMINAL_CI_FAILURE = {"error", "failure", "cancelled", "canceled"}
AVAILABLE_RUNNER_STATES = {"idle", "online"}
BUSY_RUNNER_STATES = {"active", "busy"}
OFFLINE_RUNNER_STATES = {"offline"}


class ConfigurationError(ValueError):
    """The non-secret project overlay is incomplete or unsafe."""


class InventoryError(RuntimeError):
    """A complete bounded remote inventory could not be obtained."""


class CredentialResolutionError(RuntimeError):
    """The configured credential helper did not yield a usable credential."""


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ConfigurationError(f"configuration must be a JSON object: {path}")
    return value


def _mapping(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{name} must be an object")
    return value


def _nonempty(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ConfigurationError(f"{name} must be a non-empty string")
    return text


def _string_list(value: Any, name: str, *, allow_empty: bool = False) -> List[str]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{name} must be an array")
    result = [_nonempty(item, f"{name}[]") for item in value]
    if not allow_empty and not result:
        raise ConfigurationError(f"{name} must not be empty")
    if len(set(result)) != len(result):
        raise ConfigurationError(f"{name} contains duplicate values")
    return result


def _bounded_int(
    value: Any,
    name: str,
    *,
    minimum: int,
    maximum: Optional[int] = None,
) -> int:
    if isinstance(value, bool):
        raise ConfigurationError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if number < minimum or (maximum is not None and number > maximum):
        suffix = f" and at most {maximum}" if maximum is not None else ""
        raise ConfigurationError(f"{name} must be at least {minimum}{suffix}")
    return number


def repository_parts(config: Mapping[str, Any]) -> Tuple[str, str]:
    forgejo = _mapping(config.get("forgejo"), "forgejo")
    repository = _nonempty(forgejo.get("repository"), "forgejo.repository")
    parts = repository.split("/")
    if len(parts) != 2 or not all(parts):
        raise ConfigurationError("forgejo.repository must be owner/repository")
    return parts[0], parts[1]


def _format_endpoint(endpoint: str, owner: str, repo: str) -> str:
    values = {
        "owner": urllib.parse.quote(owner, safe=""),
        "repo": urllib.parse.quote(repo, safe=""),
        "repository": (
            urllib.parse.quote(owner, safe="")
            + "/"
            + urllib.parse.quote(repo, safe="")
        ),
    }
    try:
        rendered = endpoint.format(**values)
    except (KeyError, ValueError) as exc:
        raise ConfigurationError(f"invalid runner endpoint template: {endpoint}") from exc
    if not rendered.startswith("/") or "://" in rendered or "?" in rendered:
        raise ConfigurationError(
            "runner endpoints must be API-relative paths without query strings"
        )
    return rendered


def validate_config(config: Mapping[str, Any]) -> None:
    if config.get("version") != 1:
        raise ConfigurationError("version must be 1")

    project = _mapping(config.get("project"), "project")
    _nonempty(project.get("board"), "project.board")
    _nonempty(project.get("project"), "project.project")
    profiles = _mapping(project.get("profiles"), "project.profiles")
    for name in (
        "integration_operator",
        "release_operator",
        "infrastructure_recovery_operator",
    ):
        _nonempty(profiles.get(name), f"project.profiles.{name}")

    forgejo = _mapping(config.get("forgejo"), "forgejo")
    base_url = _nonempty(forgejo.get("base_url"), "forgejo.base_url")
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError("forgejo.base_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(
            "forgejo.base_url must not contain credentials, a query, or a fragment"
        )
    repository_parts(config)
    _nonempty(forgejo.get("user_agent"), "forgejo.user_agent")
    _nonempty(forgejo.get("accept"), "forgejo.accept")
    helper = _mapping(forgejo.get("credential_helper"), "forgejo.credential_helper")
    protocol = _nonempty(helper.get("protocol"), "forgejo.credential_helper.protocol")
    if protocol not in {"http", "https"}:
        raise ConfigurationError(
            "forgejo.credential_helper.protocol must be http or https"
        )
    helper_host = _nonempty(
        helper.get("host"), "forgejo.credential_helper.host"
    )
    if helper_host.casefold() != parsed.netloc.casefold():
        raise ConfigurationError(
            "forgejo.credential_helper.host must match forgejo.base_url authority"
        )
    for field_name in ("protocol", "host", "path"):
        field_value = helper.get(field_name)
        if field_value is not None and any(
            marker in str(field_value) for marker in ("\r", "\n")
        ):
            raise ConfigurationError(
                f"forgejo.credential_helper.{field_name} must be one line"
            )
    if helper.get("path") is not None:
        _nonempty(helper.get("path"), "forgejo.credential_helper.path")
    _nonempty(helper.get("cache_path"), "forgejo.credential_helper.cache_path")

    branches = _mapping(config.get("branches"), "branches")
    _string_list(branches.get("target_branches"), "branches.target_branches")
    _string_list(
        branches.get("source_prefixes"),
        "branches.source_prefixes",
        allow_empty=True,
    )
    _string_list(
        branches.get("excluded_branches", []),
        "branches.excluded_branches",
        allow_empty=True,
    )
    if "pushed_candidates" in branches:
        raise ConfigurationError(
            "branches.pushed_candidates is unsupported; candidates are discovered "
            "from the live Forgejo branch inventory"
        )

    review = _mapping(config.get("review"), "review")
    policy = _nonempty(
        review.get("vendor_family_separation"),
        "review.vendor_family_separation",
    )
    if policy not in REVIEW_POLICIES:
        raise ConfigurationError(
            "review.vendor_family_separation must be required, preferred, or "
            "not_required"
        )
    for role in ("implementer", "reviewer"):
        route = _mapping(review.get(role), f"review.{role}")
        _nonempty(route.get("profile"), f"review.{role}.profile")
        family = route.get("vendor_family")
        if family is not None:
            _nonempty(family, f"review.{role}.vendor_family")

    owner, repo = repository_parts(config)
    runners = _mapping(config.get("runners"), "runners")
    _string_list(runners.get("required_labels"), "runners.required_labels")
    scopes = runners.get("scopes")
    if not isinstance(scopes, list) or not scopes:
        raise ConfigurationError("runners.scopes must be a non-empty array")
    names: List[str] = []
    repository_scopes = 0
    expected_repo_endpoint = (
        f"/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(repo, safe='')}/actions/runners"
    )
    for index, raw_scope in enumerate(scopes):
        scope = _mapping(raw_scope, f"runners.scopes[{index}]")
        name = _nonempty(scope.get("name"), f"runners.scopes[{index}].name")
        names.append(name)
        kind = _nonempty(scope.get("kind"), f"runners.scopes[{index}].kind")
        if kind not in {"repository", "diagnostic"}:
            raise ConfigurationError(
                f"runners.scopes[{index}].kind must be repository or diagnostic"
            )
        endpoint = _format_endpoint(
            _nonempty(
                scope.get("endpoint"), f"runners.scopes[{index}].endpoint"
            ),
            owner,
            repo,
        )
        visible = scope.get("visible")
        if visible is not None and not isinstance(visible, bool):
            raise ConfigurationError(
                f"runners.scopes[{index}].visible must be a boolean or null"
            )
        if kind == "repository":
            repository_scopes += 1
            if endpoint != expected_repo_endpoint:
                raise ConfigurationError(
                    "repository runner scope must resolve to the exact configured "
                    "repository actions/runners endpoint"
                )
            if visible is not True:
                raise ConfigurationError(
                    "repository runner scope must query visible=true"
                )
    if len(set(names)) != len(names):
        raise ConfigurationError("runners.scopes names must be unique")
    if repository_scopes < 1:
        raise ConfigurationError(
            "at least one exact repository-visible runner scope is required"
        )

    ci = _mapping(config.get("ci"), "ci")
    _string_list(ci.get("required_contexts"), "ci.required_contexts")

    stale = _mapping(config.get("stale_thresholds"), "stale_thresholds")
    for name in (
        "open_pr_no_completed_ci_seconds",
        "pushed_no_pr_seconds",
        "ready_for_integration_seconds",
    ):
        _bounded_int(stale.get(name), f"stale_thresholds.{name}", minimum=0)

    pulls = _mapping(config.get("pull_requests"), "pull_requests")
    if not isinstance(pulls.get("include_closed"), bool):
        raise ConfigurationError("pull_requests.include_closed must be a boolean")
    _bounded_int(pulls.get("max_open"), "pull_requests.max_open", minimum=1)
    _bounded_int(pulls.get("max_closed"), "pull_requests.max_closed", minimum=1)

    limits = _mapping(config.get("limits"), "limits")
    _bounded_int(limits.get("page_size"), "limits.page_size", minimum=1, maximum=100)
    _bounded_int(limits.get("max_pages"), "limits.max_pages", minimum=1, maximum=1000)
    _bounded_int(limits.get("max_branches"), "limits.max_branches", minimum=1)
    _bounded_int(
        limits.get("max_branch_commit_lookups"),
        "limits.max_branch_commit_lookups",
        minimum=0,
    )
    _bounded_int(limits.get("max_text_chars"), "limits.max_text_chars", minimum=16)
    _bounded_int(limits.get("max_output_bytes"), "limits.max_output_bytes", minimum=512)
    _bounded_int(
        limits.get("max_runners_per_scope", 500),
        "limits.max_runners_per_scope",
        minimum=1,
    )
    _bounded_int(
        limits.get("max_statuses_per_commit", 500),
        "limits.max_statuses_per_commit",
        minimum=1,
    )


def _private_json_write(path: Path, value: Mapping[str, str]) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        stream = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1
        with stream:
            json.dump(dict(value), stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary_path.unlink(missing_ok=True)


def _credential_input(helper: Mapping[str, Any]) -> str:
    fields = [
        ("protocol", _nonempty(helper.get("protocol"), "credential protocol")),
        ("host", _nonempty(helper.get("host"), "credential host")),
    ]
    if helper.get("path"):
        fields.append(("path", _nonempty(helper.get("path"), "credential path")))
    return "".join(f"{key}={value}\n" for key, value in fields) + "\n"


def _credential_headers_from_value(
    value: Any,
    forgejo: Mapping[str, Any],
    cache_path: Path,
) -> Dict[str, str]:
    if not isinstance(value, dict):
        raise CredentialResolutionError(
            f"credential cache is not a JSON object: {cache_path}"
        )
    authorization = str(value.get("Authorization") or "").strip()
    if not authorization:
        raise CredentialResolutionError(
            f"credential cache is missing Authorization: {cache_path}"
        )
    return {
        "Accept": _nonempty(forgejo.get("accept"), "forgejo.accept"),
        "Authorization": authorization,
        "User-Agent": _nonempty(
            forgejo.get("user_agent"), "forgejo.user_agent"
        ),
    }


def credential_headers(
    forgejo: Mapping[str, Any],
    *,
    run: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> Dict[str, str]:
    """Resolve Git-helper credentials with a private cache fallback.

    Helper output and header values are never included in diagnostics. The cache
    is consulted only for expected credential-resolution failures.
    """

    helper = _mapping(forgejo.get("credential_helper"), "forgejo.credential_helper")
    cache_path = Path(
        _nonempty(helper.get("cache_path"), "forgejo.credential_helper.cache_path")
    ).expanduser()
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_ASKPASS"] = "/usr/bin/false"
    try:
        runner = run or subprocess.run
        result = runner(
            ["git", "credential", "fill"],
            input=_credential_input(helper),
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
            env=environment,
        )
        values = {
            key: item
            for key, item in (
                line.split("=", 1)
                for line in str(result.stdout or "").splitlines()
                if "=" in line
            )
        }
        username = values.get("username", "")
        password = values.get("password", "")
        if not password:
            raise CredentialResolutionError(
                "credential helper returned no Forgejo credential"
            )
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode(
            "ascii"
        )
        headers = {
            "Accept": _nonempty(forgejo.get("accept"), "forgejo.accept"),
            "Authorization": f"Basic {token}",
            "User-Agent": _nonempty(
                forgejo.get("user_agent"), "forgejo.user_agent"
            ),
        }
    except (OSError, subprocess.SubprocessError, CredentialResolutionError) as exc:
        if not cache_path.is_file():
            raise CredentialResolutionError(
                "cannot resolve Forgejo credentials and no private cache exists at "
                f"{cache_path}; run one read-only observation interactively"
            ) from exc
        try:
            if os.name == "posix" and cache_path.stat().st_mode & 0o077:
                raise CredentialResolutionError(
                    f"credential cache permissions are not private: {cache_path}"
                )
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            headers = _credential_headers_from_value(cached, forgejo, cache_path)
        except (OSError, ValueError, CredentialResolutionError) as cache_exc:
            raise CredentialResolutionError(
                f"credential cache is unusable at {cache_path}"
            ) from cache_exc
    else:
        _private_json_write(cache_path, headers)
    return dict(headers)


def _encoded_query(query: Optional[Mapping[str, Any]]) -> str:
    if not query:
        return ""
    normalized: List[Tuple[str, Any]] = []
    for key, value in query.items():
        values: Iterable[Any]
        if isinstance(value, (list, tuple)):
            values = value
        else:
            values = [value]
        for item in values:
            if isinstance(item, bool):
                item = "true" if item else "false"
            normalized.append((str(key), item))
    return urllib.parse.urlencode(normalized)


class ForgejoClient:
    """Small GET-only Forgejo API client with bounded pagination."""

    def __init__(
        self,
        base_url: str,
        headers: Mapping[str, str],
        *,
        page_size: int,
        max_pages: int,
        timeout: int = 30,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {str(key): str(value) for key, value in headers.items()}
        for name in ("Authorization", "User-Agent", "Accept"):
            if not self.headers.get(name, "").strip():
                raise ConfigurationError(f"HTTP header {name} must be non-empty")
        self.page_size = _bounded_int(page_size, "page_size", minimum=1, maximum=100)
        self.max_pages = _bounded_int(max_pages, "max_pages", minimum=1)
        self.timeout = timeout
        self._urlopen = urlopen

    def get_json(
        self,
        path: str,
        query: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        if not path.startswith("/") or "://" in path:
            raise ConfigurationError("API path must be relative to forgejo.base_url")
        encoded = _encoded_query(query)
        url = self.base_url + path + ("?" + encoded if encoded else "")
        request = urllib.request.Request(
            url,
            headers=self.headers,
            method="GET",
        )
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Forgejo GET {path} returned HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Forgejo GET {path} failed") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Forgejo GET {path} returned invalid JSON") from exc

    def paginate(
        self,
        path: str,
        query: Optional[Mapping[str, Any]] = None,
        *,
        inventory: str,
        max_items: int,
    ) -> List[Dict[str, Any]]:
        maximum = _bounded_int(max_items, f"{inventory} maximum", minimum=1)
        rows: List[Dict[str, Any]] = []
        for page in range(1, self.max_pages + 1):
            params = dict(query or {})
            params.update({"limit": self.page_size, "page": page})
            payload = self.get_json(path, params)
            if not isinstance(payload, list):
                raise InventoryError(
                    f"Forgejo returned a non-array {inventory} inventory"
                )
            for item in payload:
                if not isinstance(item, dict):
                    raise InventoryError(
                        f"Forgejo returned an unreadable item in {inventory}"
                    )
                rows.append(item)
                if len(rows) > maximum:
                    raise InventoryError(
                        f"complete {inventory} inventory exceeds configured maximum "
                        f"of {maximum}"
                    )
            if len(payload) < self.page_size:
                return rows
        raise InventoryError(
            f"complete {inventory} inventory exceeds {self.max_pages} pages"
        )


def _parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _age_seconds(value: Any, now: datetime) -> Optional[int]:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0, int((now.astimezone(timezone.utc) - parsed).total_seconds()))


def _clip(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def _identifier(value: Any, name: str, *, maximum: int = 1024) -> str:
    """Preserve an external identifier exactly or fail closed."""

    text = str(value or "")
    if len(text) > maximum:
        raise InventoryError(f"{name} exceeds {maximum} characters")
    return text


def _runner_label_names(raw_labels: Any) -> set:
    if not isinstance(raw_labels, list):
        return set()
    names = set()
    for value in raw_labels:
        label = str(value or "").strip()
        if not label:
            continue
        names.add(label)
        names.add(label.split(":", 1)[0])
    return names


def classify_runner_labels(
    required_labels: Sequence[str],
    scope_results: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Classify labels using exact repository-visible scopes only."""

    repository_runners: List[Mapping[str, Any]] = []
    for scope in scope_results:
        if scope.get("kind") != "repository":
            continue
        raw_runners = scope.get("runners")
        if isinstance(raw_runners, list):
            repository_runners.extend(
                row for row in raw_runners if isinstance(row, dict)
            )

    result: Dict[str, Dict[str, Any]] = {}
    for required in required_labels:
        matching = [
            runner
            for runner in repository_runners
            if required in _runner_label_names(runner.get("labels"))
        ]
        states = {str(runner.get("status") or "").strip().lower() for runner in matching}
        if states & AVAILABLE_RUNNER_STATES:
            state = "active"
        elif states & BUSY_RUNNER_STATES:
            state = "busy"
        elif matching:
            state = "offline"
        else:
            state = "missing"
        result[str(required)] = {
            "state": state,
            "runner_ids": [runner.get("id") for runner in matching],
            "runner_names": [str(runner.get("name") or "") for runner in matching],
        }
    return result


def _review_route(config: Mapping[str, Any]) -> Dict[str, Any]:
    review = _mapping(config.get("review"), "review")
    implementer = _mapping(review.get("implementer"), "review.implementer")
    reviewer = _mapping(review.get("reviewer"), "review.reviewer")
    policy = str(review["vendor_family_separation"])
    implementer_family = implementer.get("vendor_family")
    reviewer_family = reviewer.get("vendor_family")
    same_family: Optional[bool]
    if implementer_family and reviewer_family:
        same_family = str(implementer_family).casefold() == str(
            reviewer_family
        ).casefold()
    else:
        same_family = None
    if policy == "required":
        gate = "satisfied" if same_family is False else "blocked"
    elif policy == "preferred":
        gate = "satisfied" if same_family is False else "advisory"
    else:
        gate = "not-required"
    return {
        "vendor_family_separation": policy,
        "implementer": {
            "profile": str(implementer["profile"]),
            "vendor_family": implementer_family,
        },
        "reviewer": {
            "profile": str(reviewer["profile"]),
            "vendor_family": reviewer_family,
        },
        "same_vendor_family": same_family,
        "gate": gate,
    }


def _latest_statuses(statuses: Any) -> Dict[str, Mapping[str, Any]]:
    if not isinstance(statuses, list):
        raise InventoryError("combined commit status has a non-array statuses field")
    latest: Dict[str, Mapping[str, Any]] = {}
    for row in statuses:
        if not isinstance(row, dict):
            raise InventoryError("combined commit status contains an unreadable item")
        context = str(row.get("context") or "").strip()
        if not context:
            continue
        previous = latest.get(context)
        if previous is None:
            latest[context] = row
            continue
        previous_time = _parse_time(
            previous.get("updated_at") or previous.get("created_at")
        )
        current_time = _parse_time(row.get("updated_at") or row.get("created_at"))
        if current_time is not None and (
            previous_time is None or current_time >= previous_time
        ):
            latest[context] = row
    return latest


def classify_ci(
    required_contexts: Sequence[str],
    combined_status: Mapping[str, Any],
) -> Dict[str, Any]:
    latest = _latest_statuses(combined_status.get("statuses", []))
    contexts: Dict[str, str] = {}
    missing: List[str] = []
    pending: List[str] = []
    failed: List[str] = []
    completed: List[str] = []
    for context in required_contexts:
        row = latest.get(context)
        if row is None:
            state = "missing"
            missing.append(context)
        else:
            state = str(row.get("status") or row.get("state") or "unknown").lower()
            if state in TERMINAL_CI_SUCCESS:
                completed.append(context)
            elif state in TERMINAL_CI_FAILURE:
                completed.append(context)
                failed.append(context)
            else:
                pending.append(context)
        contexts[context] = state
    if failed:
        required_state = "failure"
    elif missing or pending:
        required_state = "pending"
    else:
        required_state = "success"
    return {
        "combined_state": str(combined_status.get("state") or "unknown").lower(),
        "required_state": required_state,
        "contexts": contexts,
        "missing_contexts": missing,
        "pending_contexts": pending,
        "failed_contexts": failed,
        "completed_contexts": completed,
    }


def _fetch_combined_status(
    client: Any,
    repository_path: str,
    sha: str,
    *,
    page_size: int,
    max_pages: int,
    max_statuses: int,
) -> Dict[str, Any]:
    statuses: List[Dict[str, Any]] = []
    first: Optional[Dict[str, Any]] = None
    status_path = (
        f"{repository_path}/commits/{urllib.parse.quote(sha, safe='')}/status"
    )
    for page in range(1, max_pages + 1):
        payload = client.get_json(
            status_path,
            {"limit": page_size, "page": page},
        )
        if not isinstance(payload, dict):
            raise InventoryError("Forgejo returned a non-object combined commit status")
        if first is None:
            first = dict(payload)
        page_statuses = payload.get("statuses", [])
        if not isinstance(page_statuses, list):
            raise InventoryError("combined commit status has non-array statuses")
        for row in page_statuses:
            if not isinstance(row, dict):
                raise InventoryError("combined commit status contains an unreadable item")
            statuses.append(row)
            if len(statuses) > max_statuses:
                raise InventoryError(
                    "combined commit status exceeds configured status maximum"
                )
        total = payload.get("total_count")
        try:
            total_count = int(total)
        except (TypeError, ValueError):
            total_count = None
        if total_count is not None and len(statuses) >= total_count:
            break
        if len(page_statuses) < page_size:
            break
    else:
        raise InventoryError(
            f"combined commit status exceeds {max_pages} pages"
        )
    result = first or {}
    result["statuses"] = statuses
    result["total_count"] = len(statuses)
    return result


def _branch_matches(branch: str, prefixes: Sequence[str]) -> bool:
    return not prefixes or any(branch.startswith(prefix) for prefix in prefixes)


def _commit_identity(
    value: Any,
    label: str,
) -> Tuple[str, str]:
    """Return a bounded SHA and normalized timestamp from Forgejo commit shapes."""

    row: Dict[str, Any] = dict(value) if isinstance(value, dict) else {}
    nested_value = row.get("commit")
    nested: Dict[str, Any] = (
        dict(nested_value) if isinstance(nested_value, dict) else {}
    )
    sha = _identifier(
        row.get("sha") or row.get("id") or nested.get("sha") or nested.get("id"),
        f"{label} SHA",
    )

    timestamp_value = (
        row.get("timestamp")
        or row.get("created")
        or nested.get("timestamp")
        or nested.get("created")
    )
    if not timestamp_value:
        for actor_name in ("committer", "author"):
            actor_value = nested.get(actor_name)
            actor: Dict[str, Any] = (
                dict(actor_value) if isinstance(actor_value, dict) else {}
            )
            timestamp_value = actor.get("date") or actor.get("created")
            if timestamp_value:
                break
    parsed = _parse_time(timestamp_value)
    timestamp = _iso_time(parsed) if parsed is not None else ""
    return sha, timestamp


def _pull_request_head_identity(
    detail: Mapping[str, Any],
    label: str,
) -> Tuple[str, str]:
    raw_head = detail.get("head")
    head: Dict[str, Any] = dict(raw_head) if isinstance(raw_head, dict) else {}
    branch = _identifier(head.get("ref"), f"{label} head branch")
    sha = _identifier(head.get("sha"), f"{label} head SHA")
    if not branch and not sha:
        raise InventoryError(f"{label} readback is missing its head branch and SHA")
    return branch, sha


def _blocker(code: str, detail: str, **scope: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {"code": code, "detail": detail}
    if scope:
        row["scope"] = scope
    return row


def _action(
    action: str,
    owner_profile: str,
    scope: Mapping[str, Any],
    readback: Sequence[str],
) -> Dict[str, Any]:
    return {
        "action": action,
        "owner_profile": owner_profile,
        "scope": dict(scope),
        "required_readback": list(readback),
        "controller_mutation": "forbidden",
    }


def _open_pr_record(
    detail: Mapping[str, Any],
    combined_status: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    runner_labels: Mapping[str, Mapping[str, Any]],
    review_route: Mapping[str, Any],
    now: datetime,
) -> Tuple[Dict[str, Any], bool, Optional[Dict[str, Any]]]:
    limits = _mapping(config.get("limits"), "limits")
    text_limit = int(limits["max_text_chars"])
    branches = _mapping(config.get("branches"), "branches")
    stale = _mapping(config.get("stale_thresholds"), "stale_thresholds")
    project = _mapping(config.get("project"), "project")
    profiles = _mapping(project.get("profiles"), "project.profiles")
    owner, repo = repository_parts(config)
    repository = f"{owner}/{repo}"

    number = int(detail.get("number"))
    raw_base = detail.get("base")
    raw_head = detail.get("head")
    base: Dict[str, Any] = dict(raw_base) if isinstance(raw_base, dict) else {}
    head: Dict[str, Any] = dict(raw_head) if isinstance(raw_head, dict) else {}
    base_ref = _identifier(base.get("ref"), f"pull request #{number} base")
    head_ref = _identifier(head.get("ref"), f"pull request #{number} head")
    head_sha = _identifier(head.get("sha"), f"pull request #{number} head SHA")
    if not base_ref or not head_ref or not head_sha:
        raise InventoryError(
            f"pull request #{number} readback is missing base, head, or head SHA"
        )
    created_at = detail.get("created_at")
    age = _age_seconds(created_at, now)
    ci = classify_ci(config["ci"]["required_contexts"], combined_status)
    blockers: List[Dict[str, Any]] = []
    stalled = False

    if age is None:
        blockers.append(
            _blocker(
                "pull-request-age-unavailable",
                "pull request creation time is missing or invalid; stale delivery cannot be evaluated",
            )
        )
        stalled = True

    target_branches = list(branches["target_branches"])
    source_prefixes = list(branches["source_prefixes"])
    if base_ref not in target_branches:
        blockers.append(
            _blocker(
                "target-branch-outside-convention",
                "pull request base is outside configured target branches",
                expected=target_branches,
                observed=base_ref,
            )
        )
        stalled = True
    if not _branch_matches(head_ref, source_prefixes):
        blockers.append(
            _blocker(
                "source-branch-outside-convention",
                "pull request head is outside configured source prefixes",
                expected=source_prefixes,
                observed=head_ref,
            )
        )
        stalled = True
    if detail.get("mergeable") is False:
        blockers.append(
            _blocker(
                "mergeability-blocked",
                "Forgejo reports the pull request is not mergeable",
            )
        )
        stalled = True

    unavailable_labels = [
        label
        for label, row in runner_labels.items()
        if row.get("state") in {"missing", "offline"}
    ]
    busy_labels = [
        label for label, row in runner_labels.items() if row.get("state") == "busy"
    ]
    for label in unavailable_labels:
        blockers.append(
            _blocker(
                f"required-runner-{runner_labels[label]['state']}",
                "required runner label is unavailable in the exact repository-visible scope",
                required_label=label,
            )
        )
    for label in busy_labels:
        blockers.append(
            _blocker(
                "required-runner-busy",
                "required runner label is visible but all matching runners are busy",
                required_label=label,
            )
        )

    if ci["failed_contexts"]:
        blockers.append(
            _blocker(
                "required-CI-failed",
                "one or more required CI contexts failed",
                contexts=ci["failed_contexts"],
            )
        )
        stalled = True
    elif ci["required_state"] == "pending":
        blockers.append(
            _blocker(
                "required-CI-incomplete",
                "required CI contexts are pending or missing",
                missing=ci["missing_contexts"],
                pending=ci["pending_contexts"],
            )
        )
        stale_limit = int(stale["open_pr_no_completed_ci_seconds"])
        if not ci["completed_contexts"] and age is not None and age >= stale_limit:
            blockers.append(
                _blocker(
                    "stale-no-completed-CI",
                    "open pull request exceeded the stale threshold without a completed required CI context",
                    age_seconds=age,
                    threshold_seconds=stale_limit,
                )
            )
            stalled = True

    if review_route.get("gate") == "blocked":
        blockers.append(
            _blocker(
                "required-vendor-family-separation-unavailable",
                "configured review route cannot satisfy required vendor-family separation",
                implementer_profile=review_route["implementer"]["profile"],
                reviewer_profile=review_route["reviewer"]["profile"],
            )
        )
        stalled = True

    branch_blocked = any(
        row["code"]
        in {
            "target-branch-outside-convention",
            "source-branch-outside-convention",
            "mergeability-blocked",
        }
        for row in blockers
    )
    if unavailable_labels or busy_labels:
        stage = "waiting-for-runner"
    elif ci["failed_contexts"]:
        stage = "CI-failed"
    elif ci["required_state"] == "pending":
        stage = "waiting-for-CI"
    elif branch_blocked:
        stage = "integration-blocked"
    elif review_route.get("gate") == "blocked":
        stage = "review-route-blocked"
    else:
        stage = "ready-for-integration"
        ready_limit = int(stale["ready_for_integration_seconds"])
        if age is not None and age >= ready_limit:
            blockers.append(
                _blocker(
                    "stale-ready-for-integration",
                    "CI-ready pull request exceeded the integration stale threshold",
                    age_seconds=age,
                    threshold_seconds=ready_limit,
                )
            )
            stalled = True

    record = {
        "number": number,
        "title": _clip(detail.get("title"), text_limit),
        "url": _clip(detail.get("html_url") or detail.get("url"), text_limit),
        "state": str(detail.get("state") or "open"),
        "base": base_ref,
        "base_sha": _identifier(
            base.get("sha"), f"pull request #{number} base SHA"
        ),
        "head": head_ref,
        "head_sha": head_sha,
        "mergeable": detail.get("mergeable"),
        "created_at": str(created_at or ""),
        "updated_at": str(detail.get("updated_at") or ""),
        "age_seconds": age,
        "ci": ci,
        "stage": stage,
        "blockers": blockers,
    }

    action: Optional[Dict[str, Any]] = None
    scope = {
        "board": project["board"],
        "project": project["project"],
        "repository": repository,
        "pull_request": number,
        "base": base_ref,
        "head": head_ref,
        "head_sha": head_sha,
        "required_ci_contexts": list(config["ci"]["required_contexts"]),
    }
    if stage == "ready-for-integration" and not stalled:
        action = _action(
            "create-integration-task",
            str(profiles["integration_operator"]),
            scope,
            (
                "read back task id, exact scope, assignee, and dependency gates",
                "read back pull request base, head SHA, review evidence, and required CI before merge",
                "read back merged commit if project policy permits merge",
            ),
        )
    elif stalled:
        action_name = (
            "create-review-routing-recovery-task"
            if stage == "review-route-blocked"
            else "create-integration-recovery-task"
        )
        action = _action(
            action_name,
            str(profiles["integration_operator"]),
            scope,
            (
                "read back task id, exact scope, assignee, and dependency gates",
                "re-read repository-visible runners and combined status after recovery",
                "do not merge until review and repository policy gates are independently verified",
            ),
        )
    return record, stalled, action


def _closed_pr_record(
    detail: Mapping[str, Any], config: Mapping[str, Any]
) -> Dict[str, Any]:
    text_limit = int(config["limits"]["max_text_chars"])
    raw_base = detail.get("base")
    raw_head = detail.get("head")
    base: Dict[str, Any] = dict(raw_base) if isinstance(raw_base, dict) else {}
    head: Dict[str, Any] = dict(raw_head) if isinstance(raw_head, dict) else {}
    merged = bool(detail.get("merged") or detail.get("merged_at"))
    merge_commit_sha = _identifier(
        detail.get("merge_commit_sha"), "closed pull request merge SHA"
    )
    if merged and not merge_commit_sha:
        raise InventoryError("merged pull request readback has no merge commit SHA")
    return {
        "number": int(detail.get("number")),
        "title": _clip(detail.get("title"), text_limit),
        "url": _clip(detail.get("html_url") or detail.get("url"), text_limit),
        "state": str(detail.get("state") or "closed"),
        "base": _identifier(base.get("ref"), "closed pull request base"),
        "head": _identifier(head.get("ref"), "closed pull request head"),
        "head_sha": _identifier(
            head.get("sha"), "closed pull request head SHA"
        ),
        "mergeable": detail.get("mergeable"),
        "closed_at": str(detail.get("closed_at") or ""),
        "merged_at": str(detail.get("merged_at") or ""),
        "merge_commit_sha": merge_commit_sha,
        "stage": "merged" if merged else "closed",
        "blockers": [],
    }


def _runner_scope_records(
    config: Mapping[str, Any], client: Any
) -> List[Dict[str, Any]]:
    owner, repo = repository_parts(config)
    runners_config = config["runners"]
    limits = config["limits"]
    text_limit = int(limits["max_text_chars"])
    maximum = int(limits.get("max_runners_per_scope", 500))
    records: List[Dict[str, Any]] = []
    for scope in runners_config["scopes"]:
        endpoint = _format_endpoint(str(scope["endpoint"]), owner, repo)
        visible = scope.get("visible")
        query = {"visible": visible} if visible is not None else {}
        rows = client.paginate(
            endpoint,
            query,
            inventory=f"runners:{scope['name']}",
            max_items=maximum,
        )
        records.append(
            {
                "name": str(scope["name"]),
                "kind": str(scope["kind"]),
                "endpoint": endpoint,
                "visible": visible,
                "count": len(rows),
                "runners": [
                    {
                        "id": row.get("id"),
                        "name": _clip(row.get("name"), text_limit),
                        "status": str(row.get("status") or "unknown").lower(),
                        "labels": [
                            _clip(label, text_limit)
                            for label in row.get("labels", [])
                            if str(label or "").strip()
                        ]
                        if isinstance(row.get("labels"), list)
                        else [],
                    }
                    for row in rows
                ],
            }
        )
    return records


def _candidate_records(
    config: Mapping[str, Any],
    observed_prs: Sequence[Mapping[str, Any]],
    *,
    client: Any,
    repository_path: str,
    now: datetime,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]:
    branches = config["branches"]
    limits = config["limits"]
    stale = config["stale_thresholds"]
    project = config["project"]
    profiles = project["profiles"]
    owner, repo = repository_parts(config)
    known_branches = {str(row.get("head") or "") for row in observed_prs}
    known_shas = {str(row.get("head_sha") or "") for row in observed_prs}
    target_branches = set(branches["target_branches"])
    excluded_branches = set(branches.get("excluded_branches", []))

    branch_inventory = client.paginate(
        repository_path + "/branches",
        inventory="repository branches",
        max_items=int(limits["max_branches"]),
    )
    candidates: List[Dict[str, str]] = []
    for index, row in enumerate(branch_inventory):
        branch = _identifier(
            row.get("name"),
            f"repository branch inventory item {index} name",
        )
        if not branch:
            raise InventoryError(
                "repository branch inventory contains an item without a name"
            )
        if (
            branch in target_branches
            or branch in excluded_branches
            or branch in known_branches
            or not _branch_matches(branch, branches["source_prefixes"])
        ):
            continue
        sha, timestamp = _commit_identity(
            row.get("commit"),
            f"repository branch {branch}",
        )
        if sha and sha in known_shas:
            continue
        candidates.append(
            {
                "branch": branch,
                "sha": sha,
                "timestamp": timestamp,
            }
        )

    candidates.sort(key=lambda row: row["branch"])
    fallback_count = sum(
        1 for row in candidates if not row["sha"] or not row["timestamp"]
    )
    fallback_limit = int(limits["max_branch_commit_lookups"])
    if fallback_count > fallback_limit:
        raise InventoryError(
            f"repository branches require {fallback_count} commit metadata lookups, "
            f"exceeding the configured maximum of {fallback_limit}"
        )

    records: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []
    stalled = False
    threshold = int(stale["pushed_no_pr_seconds"])
    for candidate in candidates:
        branch = str(candidate["branch"])
        sha = str(candidate["sha"])
        timestamp = str(candidate["timestamp"])
        evidence_source = "forgejo-branch-inventory"
        if not sha or not timestamp:
            reference = sha or branch
            payload = client.get_json(
                repository_path + "/commits",
                {"sha": reference, "limit": 1, "page": 1},
            )
            if not isinstance(payload, list) or not payload:
                raise InventoryError(
                    f"Forgejo returned no commit metadata for branch {branch}"
                )
            if not isinstance(payload[0], dict):
                raise InventoryError(
                    f"Forgejo returned unreadable commit metadata for branch {branch}"
                )
            fallback_sha, fallback_timestamp = _commit_identity(
                payload[0],
                f"repository branch {branch} commit metadata",
            )
            sha = sha or fallback_sha
            timestamp = timestamp or fallback_timestamp
            evidence_source = "forgejo-commit-metadata"
        if not sha or not timestamp:
            missing = "SHA and timestamp" if not sha and not timestamp else (
                "SHA" if not sha else "timestamp"
            )
            raise InventoryError(
                f"repository branch {branch} has no usable head commit {missing}"
            )
        if sha in known_shas:
            continue

        age = _age_seconds(timestamp, now)
        if age is None:
            raise InventoryError(
                f"repository branch {branch} has an unusable head commit timestamp"
            )
        blockers: List[Dict[str, Any]] = []
        if age >= threshold:
            blockers.append(
                _blocker(
                    "stale-pushed-no-PR",
                    "live candidate branch exceeded the no-pull-request stale threshold",
                    age_seconds=age,
                    threshold_seconds=threshold,
                )
            )
            stalled = True
        records.append(
            {
                "branch": _identifier(branch, "pushed candidate branch"),
                "sha": _identifier(sha, "pushed candidate SHA"),
                "pushed_at": timestamp,
                "age_seconds": age,
                "timestamp_semantics": "head-commit",
                "evidence_source": evidence_source,
                "stage": "pushed/no-PR",
                "blockers": blockers,
            }
        )
        actions.append(
            _action(
                "create-integration-task",
                str(profiles["integration_operator"]),
                {
                    "board": project["board"],
                    "project": project["project"],
                    "repository": f"{owner}/{repo}",
                    "head": branch,
                    "head_sha": sha,
                    "target_branches": list(branches["target_branches"]),
                },
                (
                    "read back task id, exact branch and SHA, assignee, and dependency gates",
                    "create or locate the pull request, then read back its base, head, and changed files",
                    "do not infer review, merge, release, or source closure from Kanban state",
                ),
            )
        )
    return records, actions, stalled


def observe(
    config: Mapping[str, Any],
    *,
    client: Any,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Collect one deterministic, read-only delivery observation."""

    validate_config(config)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    owner, repo = repository_parts(config)
    repository = f"{owner}/{repo}"
    repository_path = (
        f"/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(repo, safe='')}"
    )
    pulls_config = config["pull_requests"]
    limits = config["limits"]
    review_route = _review_route(config)

    scope_records = _runner_scope_records(config, client)
    runner_labels = classify_runner_labels(
        config["runners"]["required_labels"], scope_records
    )

    open_inventory = client.paginate(
        repository_path + "/pulls",
        {"state": "open"},
        inventory="open pull requests",
        max_items=int(pulls_config["max_open"]),
    )
    open_records: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []
    stalled = False
    for summary in open_inventory:
        try:
            number = int(summary.get("number"))
        except (TypeError, ValueError) as exc:
            raise InventoryError(
                "open pull request inventory contains an item without a number"
            ) from exc
        detail = client.get_json(repository_path + f"/pulls/{number}")
        if not isinstance(detail, dict):
            raise InventoryError(f"pull request #{number} readback is not an object")
        raw_head = detail.get("head")
        head: Dict[str, Any] = dict(raw_head) if isinstance(raw_head, dict) else {}
        sha = _nonempty(head.get("sha"), f"pull request #{number} head SHA")
        combined = _fetch_combined_status(
            client,
            repository_path,
            sha,
            page_size=int(limits["page_size"]),
            max_pages=int(limits["max_pages"]),
            max_statuses=int(limits.get("max_statuses_per_commit", 500)),
        )
        record, record_stalled, action = _open_pr_record(
            detail,
            combined,
            config=config,
            runner_labels=runner_labels,
            review_route=review_route,
            now=current,
        )
        open_records.append(record)
        stalled = stalled or record_stalled
        if action is not None:
            actions.append(action)

    closed_records: List[Dict[str, Any]] = []
    closed_pr_heads: List[Dict[str, str]] = []
    closed_inventory = client.paginate(
        repository_path + "/pulls",
        {"state": "closed"},
        inventory="closed pull requests",
        max_items=int(pulls_config["max_closed"]),
    )
    for summary in closed_inventory:
        try:
            number = int(summary.get("number"))
        except (TypeError, ValueError) as exc:
            raise InventoryError(
                "closed pull request inventory contains an item without a number"
            ) from exc
        detail = client.get_json(repository_path + f"/pulls/{number}")
        if not isinstance(detail, dict):
            raise InventoryError(
                f"closed pull request #{number} readback is not an object"
            )
        head_branch, head_sha = _pull_request_head_identity(
            detail,
            f"closed pull request #{number}",
        )
        closed_pr_heads.append({"head": head_branch, "head_sha": head_sha})
        if pulls_config["include_closed"]:
            record = _closed_pr_record(detail, config)
            closed_records.append(record)
            if record["stage"] == "merged":
                actions.append(
                    _action(
                        "create-release-task",
                        str(config["project"]["profiles"]["release_operator"]),
                        {
                            "board": config["project"]["board"],
                            "project": config["project"]["project"],
                            "repository": repository,
                            "pull_request": number,
                            "merged_commit": record["merge_commit_sha"],
                        },
                        (
                            "read back task id, merged commit, assignee, and policy gate",
                            "verify artifact or GitOps publication through the declared release system",
                            "do not infer deployment or source closure from merge or Kanban state",
                        ),
                    )
                )

    candidate_records, candidate_actions, candidate_stalled = _candidate_records(
        config,
        [*open_records, *closed_pr_heads],
        client=client,
        repository_path=repository_path,
        now=current,
    )
    actions.extend(candidate_actions)
    stalled = stalled or candidate_stalled

    blockers: List[Dict[str, Any]] = []
    for label, row in runner_labels.items():
        if row["state"] in {"missing", "offline"}:
            blockers.append(
                _blocker(
                    f"required-runner-{row['state']}",
                    "required runner label is unavailable in the exact repository-visible scope",
                    repository=repository,
                    required_label=label,
                )
            )
            stalled = True
            actions.append(
                _action(
                    "create-infrastructure-recovery-task",
                    str(
                        config["project"]["profiles"][
                            "infrastructure_recovery_operator"
                        ]
                    ),
                    {
                        "board": config["project"]["board"],
                        "project": config["project"]["project"],
                        "repository": repository,
                        "runner_scope": "repository-visible",
                        "required_label": label,
                        "observed_state": row["state"],
                    },
                    (
                        "read back task id, exact runner scope, assignee, and acceptance gate",
                        "read back the repository runner endpoint with visible=true",
                        "verify a matching runner is active in repository visibility before calling CI ready",
                    ),
                )
            )

    if review_route["gate"] == "blocked":
        blockers.append(
            _blocker(
                "required-vendor-family-separation-unavailable",
                "per-installation review policy requires vendor-family separation, but the configured route cannot prove it",
                implementer_profile=review_route["implementer"]["profile"],
                reviewer_profile=review_route["reviewer"]["profile"],
            )
        )
        stalled = True

        actions.append(
            _action(
                "create-review-routing-recovery-task",
                str(config["project"]["profiles"]["integration_operator"]),
                {
                    "board": config["project"]["board"],
                    "project": config["project"]["project"],
                    "repository": repository,
                    "vendor_family_separation": "required",
                    "implementer_profile": review_route["implementer"]["profile"],
                    "reviewer_profile": review_route["reviewer"]["profile"],
                },
                (
                    "read back one exact-scope task and its assignee",
                    "verify the per-installation review route without changing global profiles",
                    "rerun delivery observation and require the configured route gate to be satisfied",
                ),
            )
        )

    for record in open_records:
        blockers.extend(
            dict(item, scope={**item.get("scope", {}), "pull_request": record["number"]})
            for item in record["blockers"]
            if item["code"]
            in {
                "stale-no-completed-CI",
                "required-CI-failed",
                "stale-ready-for-integration",
                "target-branch-outside-convention",
                "source-branch-outside-convention",
                "mergeability-blocked",
                "pull-request-age-unavailable",
            }
        )
    for record in candidate_records:
        blockers.extend(record["blockers"])

    has_delivery_work = bool(open_records or candidate_records or actions)
    if stalled:
        classification = "STALLED"
    elif has_delivery_work:
        classification = "ACTIVE"
    else:
        classification = "IDLE-BY-GATING"
        blockers.append(
            _blocker(
                "no-open-delivery-items",
                "no open pull request or pushed candidate currently requires delivery work",
                repository=repository,
            )
        )

    report = {
        "schema_version": SCHEMA_VERSION,
        "controller": "forgejo-delivery-observer",
        "observed_at": _iso_time(current),
        "classification": classification,
        "project": {
            "board": config["project"]["board"],
            "project": config["project"]["project"],
            "repository": repository,
            "integration_operator_profile": config["project"]["profiles"][
                "integration_operator"
            ],
            "release_operator_profile": config["project"]["profiles"][
                "release_operator"
            ],
        },
        "review_route": review_route,
        "runner_scopes": scope_records,
        "runner_labels": runner_labels,
        "pull_requests": open_records,
        "closed_pull_requests": closed_records,
        "pushed_candidates": candidate_records,
        "blockers": blockers,
        "actions": actions,
        "mutation_boundary": {
            "controller": "read-only",
            "allowed_http_methods": ["GET"],
            "supervisor_owns_task_creation": True,
            "integration_operator_owns_merge": True,
            "release_operator_owns_release": True,
        },
    }
    if classification not in CLASSIFICATIONS:
        raise AssertionError("invalid internal classification")
    return report


def serialize_report(report: Mapping[str, Any], max_output_bytes: int) -> str:
    """Serialize under a hard byte budget, failing closed if it cannot fit."""

    maximum = _bounded_int(max_output_bytes, "max_output_bytes", minimum=512)
    payload = json.dumps(
        report,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ) + "\n"
    actual = len(payload.encode("utf-8"))
    if actual <= maximum:
        return payload
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "classification": "STALLED",
        "error": {
            "code": "output-limit-exceeded",
            "actual_bytes": actual,
            "max_bytes": maximum,
        },
        "blockers": [
            {
                "code": "output-limit-exceeded",
                "detail": "complete delivery observation did not fit the configured output budget",
            }
        ],
        "actions": [
            {
                "action": "create-observer-capacity-recovery-task",
                "controller_mutation": "forbidden",
                "required_readback": [
                    "increase the project-owned output bound or reduce configured closed-history scope",
                    "rerun and read back one complete bounded observation",
                ],
            }
        ],
    }
    bounded = json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n"
    if len(bounded.encode("utf-8")) > maximum:
        minimal = {
            "schema_version": SCHEMA_VERSION,
            "classification": "STALLED",
            "error": {"code": "output-limit-exceeded"},
        }
        bounded = json.dumps(minimal, sort_keys=True, separators=(",", ":")) + "\n"
    return bounded


def _error_report(exc: Exception, maximum: int) -> str:
    report = {
        "schema_version": SCHEMA_VERSION,
        "controller": "forgejo-delivery-observer",
        "classification": "STALLED",
        "error": {
            "code": "observation-failed",
            "type": type(exc).__name__,
            "message": _clip(exc, 256),
        },
        "mutation_boundary": {"controller": "read-only"},
    }
    return serialize_report(report, max(512, maximum))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit bounded, read-only Forgejo delivery telemetry"
    )
    parser.add_argument("--config", required=True, help="non-secret JSON overlay")
    args = parser.parse_args(argv)
    maximum = 4096
    try:
        config = load_json(Path(args.config))
        validate_config(config)
        maximum = int(config["limits"]["max_output_bytes"])
        headers = credential_headers(config["forgejo"])
        client = ForgejoClient(
            str(config["forgejo"]["base_url"]),
            headers,
            page_size=int(config["limits"]["page_size"]),
            max_pages=int(config["limits"]["max_pages"]),
        )
        report = observe(config, client=client)
        sys.stdout.write(serialize_report(report, maximum))
        return 0
    except Exception as exc:
        sys.stdout.write(_error_report(exc, maximum))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
