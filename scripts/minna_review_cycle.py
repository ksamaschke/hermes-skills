#!/usr/bin/env python3
"""Minna PR review cycle: gate -> bounded review -> merge or return.

Deterministic, no LLM. One tick does at most one state transition per PR, so a
stuck tick can never fan out. Every subprocess has a hard timeout, every created
card carries a runtime cap and a single-retry circuit breaker, and `--reap`
blocks cards that outlive their budget. No zombie lanes.

States per open PR:
  needs_gate     -> run the repository gate from a FRESH CLONE of the PR head
  gate_red       -> post the failure on the PR, file an implementer card, stop
  needs_review   -> create a bounded read-only reviewer leaf (exact scope)
  in_review      -> waiting on the leaf; enforce the budget
  changes        -> post the finding on the PR, file an implementer card
  approved       -> merge, then close the source issue
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_packet_integrity as rpi  # noqa: E402  (path set above)

CONFIG_PATH = Path(
    os.environ.get(
        "MINNA_REVIEW_CYCLE_CONFIG",
        str(Path.home() / ".hermes" / "scripts" / "minna-review-cycle.json"),
    )
).expanduser()


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


CFG = load_config()
API = CFG["api"]
REPO = CFG["repo"]
BOARD = CFG["board"]
BASE = CFG["base_branch"]
BASE_BRANCH = str(BASE)
for _prefix in ("refs/remotes/origin/", "refs/heads/", "origin/"):
    if BASE_BRANCH.startswith(_prefix):
        BASE_BRANCH = BASE_BRANCH[len(_prefix):]
        break
BASE_REMOTE_REF = f"refs/remotes/origin/{BASE_BRANCH}"
REPO_DIR = Path(CFG["repo_dir"]).expanduser()
STATE_PATH = Path(CFG["state_path"]).expanduser()
LEAF_RUNTIME = CFG["leaf_runtime"]
FAN_IN_RUNTIME = CFG.get("fan_in_runtime", "10m")
GATE_TIMEOUT = int(CFG["gate_timeout_seconds"])
MAX_IN_FLIGHT = int(CFG["max_leaves_in_flight"])
MAX_MERGES_PER_TICK = int(CFG["max_merges_per_tick"])
MAX_GATES_PER_TICK = int(CFG.get("max_gates_per_tick", 1))
MAX_FILES_PER_LEAF = int(CFG.get("max_files_per_leaf", 5))
MAX_REVIEW_ROUNDS = int(CFG.get("max_review_rounds", 3))
PRIORITY_PRS = [int(n) for n in CFG.get("priority_prs", [])]
MERGE_ORDER = [int(n) for n in CFG.get("merge_order", [])]
REVIEW_PROVIDER = str(CFG.get("review_provider") or "")
REVIEW_MODEL = str(CFG.get("review_model") or "")
REVIEW_VENDOR = str(CFG.get("review_vendor_family") or "unknown")
IMPLEMENTATION_VENDOR = str(CFG.get("implementation_vendor_family") or "unknown")
CYCLE_LOCK = Path(
    CFG.get("lock_path")
    or (str(STATE_PATH) + ".lock")
).expanduser()


def parse_duration(value: str | int) -> int:
    """`25m` / `2h` / `1500` -> seconds."""
    text = str(value).strip().lower()
    if text.isdigit():
        return int(text)
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(text[-1:], 1)
    try:
        return int(float(text[:-1]) * mult)
    except ValueError:
        return 1800


@contextlib.contextmanager
def exclusive_cycle_lock() -> Iterator[bool]:
    """Fail closed when another cron/manual tick already owns the cycle."""
    CYCLE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with CYCLE_LOCK.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()} started={int(time.time())}\n")
            handle.flush()
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------- forgejo ---
_HEADER_CACHE: dict[str, str] | None = None
CRED_CACHE = Path(
    CFG.get("credential_cache_path")
    or (str(STATE_PATH) + ".credentials.json")
).expanduser()


def _write_private_json(path: Path, value: Any) -> None:
    """Atomically write sensitive runtime material with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        path.chmod(0o600)
    finally:
        tmp_path.unlink(missing_ok=True)


def _headers() -> dict[str, str]:
    """Resolve Forgejo auth headers, with an on-disk fallback.

    Under cron there is no TTY and no Keychain prompt, so `git credential fill`
    can fail even though it works interactively. Cache the resolved header the
    first time it succeeds and reuse it; a stale cache surfaces as an HTTP 401,
    which is a far clearer failure than a subprocess traceback.
    """
    global _HEADER_CACHE
    if _HEADER_CACHE is not None:
        return dict(_HEADER_CACHE)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import forgejo_kanban_sync as sync

        h = dict(sync.credential_headers({"credential_host": CFG["credential_host"]}))
        h["Accept"] = "application/json"
        _write_private_json(CRED_CACHE, h)
        _HEADER_CACHE = h
        return dict(h)
    except Exception as exc:
        if CRED_CACHE.is_file():
            h = json.loads(CRED_CACHE.read_text())
            h["Accept"] = "application/json"
            _HEADER_CACHE = h
            return dict(h)
        raise RuntimeError(
            f"cannot resolve Forgejo credentials for {CFG['credential_host']}: {exc}. "
            f"Run one tick interactively to populate {CRED_CACHE}."
        ) from exc


def api(path: str, method: str = "GET", payload: dict | None = None) -> Any:
    body = json.dumps(payload).encode() if payload is not None else None
    h = _headers()
    if payload is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API}{path}", data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode()
            return json.loads(text) if text.strip() else None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {exc.read().decode()[:300]}") from exc


def pulls(state: str) -> list[dict]:
    """Read a complete bounded Forgejo pull inventory for one lifecycle state."""
    out: list[dict] = []
    for page in range(1, 21):
        rows = api(f"/repos/{REPO}/pulls?state={state}&limit=50&page={page}")
        if not isinstance(rows, list):
            raise RuntimeError(f"Forgejo returned a non-list pull inventory for state={state}")
        out.extend(rows)
        if len(rows) < 50:
            return out
    raise RuntimeError(f"Forgejo pull inventory for state={state} exceeded 1000 rows")


def open_prs() -> list[dict]:
    return pulls("open")


SOURCE_ISSUE_RE = re.compile(r"\(#(\d+)\)\s*$")
CANDIDATE_RE = re.compile(r"(?im)^\s*candidate_commit\s*:\s*([0-9a-f]{40})\s*$")


def source_issue_number(pr: dict) -> int | None:
    """Return the source issue named by the PR title's final ``(#N)``."""
    match = SOURCE_ISSUE_RE.search(str(pr.get("title") or ""))
    return int(match.group(1)) if match else None


def pr_rank(pr: dict) -> tuple[int, int]:
    """Infrastructure PRs first, then product PRs in configured dependency order."""
    number = int(pr.get("number") or 0)
    if number in PRIORITY_PRS:
        return (0, PRIORITY_PRS.index(number))
    source = source_issue_number(pr)
    if source in MERGE_ORDER:
        return (1, MERGE_ORDER.index(source))
    return (2, number)


def unresolved_predecessors(source: int | None, merged_sources: set[int]) -> list[int]:
    """Return ordered product predecessors without a verified merged PR."""
    if source not in MERGE_ORDER:
        return []
    return [n for n in MERGE_ORDER[: MERGE_ORDER.index(source)] if n not in merged_sources]


def task_candidate(task: dict) -> str:
    match = CANDIDATE_RE.search(str(task.get("body") or ""))
    return match.group(1).lower() if match else ""


def task_matches_candidate(task: dict, head_sha: str) -> bool:
    candidate = task_candidate(task)
    return bool(candidate and candidate == str(head_sha).lower())


def split_manifest(manifest: list[str], max_files: int = 5) -> list[list[str]]:
    """Partition an exact manifest without dropping or duplicating a changed path."""
    if max_files < 1:
        raise ValueError("max_files must be positive")
    return [manifest[i : i + max_files] for i in range(0, len(manifest), max_files)]


def _runtime_layer(entry: str) -> str:
    path = _manifest_path(entry) if "_manifest_path" in globals() else entry.split(":", 1)[0]
    if path.startswith("crates/minna-server/"):
        return "server"
    if path.startswith("src-tauri/"):
        return "desktop-shell"
    if path.startswith(("apps/desktop/", "app/")):
        return "desktop-web"
    if path.startswith("crates/"):
        return "rust-library"
    return "shared"


def split_manifest_by_layer(manifest: list[str], max_files: int = 5) -> list[list[str]]:
    """Keep runtime layers isolated, then enforce the five-file leaf ceiling."""
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for entry in manifest:
        layer = _runtime_layer(entry)
        if layer not in groups:
            groups[layer] = []
            order.append(layer)
        groups[layer].append(entry)
    return [chunk for layer in order for chunk in split_manifest(groups[layer], max_files)]


def gate_evidence_valid(record: dict, candidate: str) -> bool:
    if record.get("gate") != "green":
        return False
    evidence = deserialize_evidence(record.get("gate_evidence"))
    expected = [
        (str(command), f"{label} pass {pass_number}/2")
        for pass_number in (1, 2)
        for label, command in CFG["gate_commands"]
    ]
    if len(evidence) != len(expected):
        return False
    candidate = str(candidate).lower()
    return all(
        int(item.exit_code) == 0
        and str(item.command) == expected_command
        and str(item.commit).lower() == candidate
        and str(item.detail).startswith(expected_detail)
        and bool(str(item.run_reference).strip())
        for item, (expected_command, expected_detail) in zip(evidence, expected)
    )


def fan_in_verdict(expected: list[str], leaves: Iterable[dict]) -> dict:
    """Reconcile leaf reports without rescanning source or inventing a verdict."""
    rows = list(leaves)
    covered = [item for row in rows for item in (row.get("scope") or [])]
    duplicates = sorted({item for item in covered if covered.count(item) > 1})
    missing = [item for item in expected if item not in covered]
    unexpected = [item for item in covered if item not in expected]
    verdicts = [str(row.get("verdict") or "REVIEW-INCOMPLETE").upper() for row in rows]
    if "CHANGES_REQUESTED" in verdicts:
        verdict = "CHANGES_REQUESTED"
    elif missing or duplicates or unexpected or len(rows) == 0 or any(
        value != "APPROVED" for value in verdicts
    ):
        verdict = "REVIEW-INCOMPLETE"
    else:
        verdict = "APPROVED"
    return {
        "verdict": verdict,
        "covered": covered,
        "missing": missing,
        "duplicates": duplicates,
        "unexpected": unexpected,
        "leaf_tasks": [row.get("task_id") for row in rows],
    }


def _acceptance_section(body: str) -> str:
    """Extract acceptance criteria without copying issue execution instructions."""
    text = str(body or "")
    match = re.search(
        r"(?ims)^#{1,4}\s*(?:acceptance criteria|definition of done)\s*$\n"
        r"(.*?)(?=^#{1,4}\s|\Z)",
        text,
    )
    if not match:
        return ""
    forbidden = re.compile(
        r"(?i)\b(?:tdd first|write the fix|make the production change|implement this)\b"
    )
    return "\n".join(
        line for line in match.group(1).strip().splitlines()
        if not forbidden.search(line)
    ).strip()


def source_acceptance(pr: dict) -> str:
    source = source_issue_number(pr)
    if source is not None:
        issue = api(f"/repos/{REPO}/issues/{source}")
        section = _acceptance_section(str(issue.get("body") or ""))
        if section:
            return section[:4000]
        return str(issue.get("title") or pr.get("title") or "")[:1000]
    section = _acceptance_section(str(pr.get("body") or ""))
    return (section or str(pr.get("title") or ""))[:4000]


def close_source_issue(
    pr: dict,
    *,
    api_fn: Callable[[str, str, dict | None], Any] = api,
) -> tuple[bool, str]:
    """Close a merged PR's source issue and verify the exact target by readback."""
    source = source_issue_number(pr)
    if not pr.get("merged") or source is None:
        return False, "PR is not merged or has no source issue"
    path = f"/repos/{REPO}/issues/{source}"
    before = api_fn(path, "GET", None)
    if str(before.get("state") or "").lower() == "closed":
        return True, f"issue #{source} already closed"
    api_fn(path, "PATCH", {"state": "closed"})
    back = api_fn(path, "GET", None)
    if str(back.get("state") or "").lower() != "closed":
        return False, f"issue #{source} close write did not survive readback"
    return True, f"issue #{source} closed"


# ----------------------------------------------------------------- hermes ---
def _clean_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)
    return env


def hermes(args: list[str], *, as_json: bool = False, timeout: int = 120) -> Any:
    env = _clean_subprocess_env()
    cmd = [shutil.which("hermes") or "hermes", *args]
    if as_json:
        cmd.append("--json")
    r = subprocess.run(cmd, text=True, capture_output=True, env=env, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"hermes {' '.join(args[:4])} failed: {(r.stderr or r.stdout)[:300]}")
    return json.loads(r.stdout) if as_json else r.stdout


def board_tasks() -> list[dict]:
    return hermes(["kanban", "--board", BOARD, "list"], as_json=True)


# ------------------------------------------------------------------ state ---
def load_state() -> dict:
    if STATE_PATH.is_file():
        return json.loads(STATE_PATH.read_text())
    return {"prs": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(
        prefix=f".{STATE_PATH.name}.", suffix=".tmp", dir=STATE_PATH.parent
    )
    tmp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, STATE_PATH)
    finally:
        tmp_path.unlink(missing_ok=True)


# ------------------------------------------------------------------- gate ---
# Failures that describe the machine, not the change under test. A gate command
# that fails only this way is retried once before it is allowed to mark a PR red,
# because host load is not a property of the candidate commit.
INFRA_FAILURE_RE = re.compile(
    r"source: TimedOut|"
    r"Connection refused \(os error 61\)|"
    r"Resource temporarily unavailable \(os error 35\)|"
    r"Too many open files|"
    r"error sending request for url|"
    r"deadline has elapsed",
    re.I,
)


def looks_like_infra_failure(log_path: Path, marker: int) -> str:
    """Return the matched infra signature from the tail written after `marker`."""
    try:
        with log_path.open("r", errors="replace") as fh:
            fh.seek(marker)
            tail = fh.read()
    except OSError:
        return ""
    # A real assertion failure and an infra timeout can co-occur; only treat the
    # run as infra-flaky when no test actually asserted false.
    if re.search(r"assertion (`|failed)|panicked at .*assert", tail, re.I):
        return ""
    m = INFRA_FAILURE_RE.search(tail)
    return m.group(0) if m else ""


def run_gate_command(
    cmd: str, label: str, clone: Path, env: dict, log, deadline: float
) -> tuple:
    """Run one command within the gate-wide deadline, with one infra retry."""
    attempts = 2
    result = None
    flaky = ""
    for attempt in range(1, attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(cmd, GATE_TIMEOUT)
        log.write(f"\n===== {label} (attempt {attempt}): {cmd}\n")
        log.flush()
        marker = log.tell()
        result = subprocess.run(cmd, shell=True, cwd=clone, env=env,
                                stdout=log, stderr=subprocess.STDOUT,
                                timeout=max(1, int(remaining)))
        log.flush()
        if result.returncode == 0:
            return result, flaky
        signature = looks_like_infra_failure(Path(log.name), marker)
        if not signature or attempt == attempts:
            return result, flaky
        flaky = signature
        log.write(f"\n===== {label}: infra-style failure ({signature}); retrying once\n")
        log.flush()
        # Let whatever saturated the host drain before the retry.
        time.sleep(min(45, max(0, deadline - time.monotonic())))
    return result, flaky


def run_gate(head_sha: str, branch: str, log_path: Path) -> tuple[bool, list]:
    """Clone the PR head into a throwaway dir and run the gate there.

    A fresh clone is the point: the candidate worktree carries untracked files
    and stale build artifacts that hide packaging defects.

    Returns (ok, evidence) where evidence is a list of ``rpi.GateEvidence`` —
    command, exit code, and the commit it actually ran against. It deliberately
    never returns a prose claim: a citation such as "all gate commands green"
    names no command, no exit code and no commit, so a reviewer cannot verify it
    and must return REVIEW-INCOMPLETE. Measured evidence, or nothing.
    """
    tmp = tempfile.mkdtemp(prefix="minna-gate-")
    evidence: list = []
    try:
        # Refresh the local mirror first. The rework loop pushes new commits to
        # the PR branch, and cloning from a stale local repo silently gates the
        # PREVIOUS head — which reads as "clone HEAD != PR head" and marks the PR
        # red for a reason that has nothing to do with its code.
        subprocess.run(["git", "-C", str(REPO_DIR), "fetch", "--quiet", "origin",
                        f"+refs/heads/{branch}:refs/remotes/origin/{branch}"],
                       check=True, capture_output=True, timeout=300)
        remote_head = subprocess.run(
            ["git", "-C", str(REPO_DIR), "rev-parse", f"refs/remotes/origin/{branch}"],
            text=True, capture_output=True, timeout=60,
        ).stdout.strip()
        if remote_head != head_sha:
            return False, [rpi.GateEvidence(
                f"git fetch origin {branch}", 1, head_sha,
                f"remote branch {remote_head[:12]} != PR head {head_sha[:12]}")]
        clone = Path(tmp) / "c"
        subprocess.run(
            ["git", "clone", "--no-hardlinks", "--quiet", "--no-checkout",
             str(REPO_DIR), str(clone)],
            check=True, capture_output=True, timeout=300,
        )
        # The PR ref may exist only as a remote-tracking ref in the local mirror;
        # a local clone does not advertise those refs unless they are requested.
        subprocess.run([
            "git", "-C", str(clone), "fetch", "--quiet", str(REPO_DIR),
            f"refs/remotes/origin/{branch}",
        ], check=True, capture_output=True, timeout=300)
        subprocess.run(
            ["git", "-C", str(clone), "checkout", "--quiet", "--detach", head_sha],
            check=True, capture_output=True, timeout=120,
        )
        head = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"],
                              text=True, capture_output=True, timeout=60).stdout.strip()
        if head != head_sha:
            return False, [rpi.GateEvidence(
                f"git clone --branch {branch}", 1, head_sha,
                f"clone HEAD {head[:12]} != PR head {head_sha[:12]}")]

        env = os.environ.copy()
        env["PATH"] = f"{Path.home()}/.cargo/bin:{env.get('PATH','')}"
        env["CI"] = "1"
        env.pop("VITE_MINNA_VAULT", None)  # poisons App.openVault tests

        deadline = time.monotonic() + GATE_TIMEOUT
        with log_path.open("w") as log:
            for pass_number in (1, 2):
                for label, cmd in CFG["gate_commands"]:
                    qualified = f"{label} pass {pass_number}/2"
                    r, flaky = run_gate_command(
                        cmd, qualified, clone, env, log, deadline
                    )
                    evidence.append(rpi.GateEvidence(
                        command=cmd, exit_code=r.returncode, commit=head_sha,
                        detail=(
                            f"{qualified} (retried: {flaky})" if flaky else qualified
                        ),
                        run_reference=str(log_path)))
                    if r.returncode != 0:
                        return False, evidence
        return True, evidence
    except subprocess.TimeoutExpired:
        evidence.append(rpi.GateEvidence(
            "gate", 124, head_sha, f"exceeded {GATE_TIMEOUT}s budget"))
        return False, evidence
    except subprocess.CalledProcessError as exc:
        evidence.append(rpi.GateEvidence(
            "git clone", exc.returncode or 1, head_sha, str(exc.stderr)[:200]))
        return False, evidence
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def gate_summary(evidence: list) -> str:
    """One-line human summary of measured evidence. Never a bare green claim."""
    if not evidence:
        return "no gate evidence measured"
    failed = [e for e in evidence if e.exit_code != 0]
    if failed:
        f = failed[0]
        return f"{f.detail or f.command} exit {f.exit_code} @ {f.commit[:12]}"
    return "; ".join(f"{e.detail or e.command} exit {e.exit_code}" for e in evidence)


def serialize_evidence(evidence: list) -> list:
    return [{"command": e.command, "exit_code": e.exit_code, "commit": e.commit,
             "detail": e.detail, "run_reference": e.run_reference} for e in evidence]


def deserialize_evidence(rows) -> list:
    out = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("command"):
            out.append(rpi.GateEvidence(
                command=str(row.get("command")),
                exit_code=int(row.get("exit_code") or 0),
                commit=str(row.get("commit") or ""),
                detail=str(row.get("detail") or ""),
                run_reference=str(row.get("run_reference") or "")))
    return out


def changed_files(base: str, head_sha: str) -> list[str]:
    """Changed paths WITH real per-path hunk ranges (`path:1-20,40-55`).

    A manifest of bare paths is not a change set: it lets a review drift into
    surrounding code and a strict validator rejects it as a non-file path. The
    ranges come straight out of `git diff -U0`, so they are measured, not
    asserted.
    """
    merge_base = subprocess.run(
        ["git", "-C", str(REPO_DIR), "merge-base", base, head_sha],
        text=True, capture_output=True, timeout=90).stdout.strip() or base
    paths = rpi.changed_paths_with_hunks(REPO_DIR, merge_base, head_sha)
    return [p.render() for p in paths]



# ------------------------------------------------------- duplicate review ---
def _board_db() -> Path:
    out = hermes(["kanban", "boards", "list"], as_json=True)
    for entry in out or []:
        if isinstance(entry, dict) and entry.get("slug") == BOARD:
            return Path(str(entry.get("db_path"))).expanduser()
    raise RuntimeError(f"no board database for {BOARD}")


def _board_rows_and_runs():
    """Read tasks and their runs directly, read-only.

    The CLI list does not carry run outcomes/metadata, and the approving verdict
    lives in the run — so this reads the board database directly.

    A `mode=ro` URI is deliberately NOT used: the board is in WAL mode, and a
    read-only handle cannot create the `-shm` file it needs when no writer
    currently holds the database open, which fails with "unable to open database
    file". `immutable=1` would paper over that but is unsafe against a live
    database being written by the dispatcher. A normal handle with
    `PRAGMA query_only=1` is engine-enforced read-only and WAL-safe.
    """
    import sqlite3

    conn = sqlite3.connect(str(_board_db()), timeout=10)
    try:
        conn.execute("PRAGMA query_only=1")
        conn.row_factory = sqlite3.Row
        tasks = [dict(r) for r in conn.execute(
            "SELECT id, title, body, result, assignee, status, workspace_path FROM tasks")]
        runs: dict[str, list] = {}
        for r in conn.execute(
            "SELECT id, task_id, profile, status, outcome, summary, metadata "
            "FROM task_runs ORDER BY id"
        ):
            runs.setdefault(r["task_id"], []).append(dict(r))
    finally:
        conn.close()
    return tasks, runs


def existing_approval(head_sha: str):
    """Return an ApprovingReview when this candidate's tree is already approved.

    Identity is the *tree*, not the commit sha. The observed failure: an
    implementation card was reviewed and APPROVED while its work was still
    uncommitted, so no run recorded a sha; a later "preserve uncommitted work"
    recovery commit gave that identical content a new sha, which then looked
    unreviewed. Matching on tree hash makes the duplicate visible.

    Returns None on any error — a lookup failure must never suppress a review.
    """
    try:
        tasks, runs = _board_rows_and_runs()
        return rpi.approving_review_for_tree(
            repo=REPO_DIR,
            candidate_rev=head_sha,
            tasks=tasks,
            runs_for_task=lambda tid: runs.get(tid, []),
            implementer_profiles=(CFG["implementation_profile"], "default"),
        )
    except Exception as exc:  # fail open: never skip a review because of a bug here
        print(f"  (duplicate-approval check unavailable: {type(exc).__name__}: {exc})")
        return None


# ------------------------------------------------------------------ cards ---
def _manifest_path(entry: str) -> str:
    return re.sub(r":\d+-\d+(?:,\d+-\d+)*$", "", entry)


def focused_checks_for_scope(base_commit: str, head_sha: str, scope: list[str]) -> list[str]:
    paths = [_manifest_path(entry) for entry in scope]
    quoted = " ".join(shlex.quote(path) for path in paths)
    checks = [
        f"git diff --check {base_commit}..{head_sha}",
        f"git diff --unified=80 {base_commit}..{head_sha} -- {quoted}",
    ]
    frontend_tests = [
        path for path in paths
        if re.search(r"(?:\.test|\.spec)\.[cm]?[jt]sx?$", path)
    ]
    if frontend_tests:
        tests = " ".join(shlex.quote(path) for path in frontend_tests)
        checks.append(f"pnpm --filter app exec vitest run {tests}")
    for path in paths:
        match = re.match(r"crates/([^/]+)/tests/([^/]+)\.rs$", path)
        if match:
            checks.append(f"cargo test -p {match.group(1)} --test {match.group(2)}")
    return checks


def render_review_packet(
    *,
    pr: dict,
    implementation_task: str,
    review_path: str,
    base_commit: str,
    scope: list[str],
    gate_evidence: str,
    acceptance: str,
    focused_checks: list[str],
    review_round: int,
    scope_index: int,
    scope_total: int,
    preflight: str,
) -> str:
    num = int(pr["number"])
    head_sha = str(pr["head"]["sha"])
    source = source_issue_number(pr)
    independent = (
        REVIEW_VENDOR != "unknown"
        and IMPLEMENTATION_VENDOR != "unknown"
        and REVIEW_VENDOR.lower() != IMPLEMENTATION_VENDOR.lower()
    )
    scope_text = "\n".join(f"- `{entry}`" for entry in scope)
    checks_text = "\n".join(f"- `{command}`" for command in focused_checks)
    dispatch_cap = parse_duration(LEAF_RUNTIME)
    return f"""Adversarial review of PR #{num} (Forgejo `{REPO}`).

implementation_task: {implementation_task}
source_pr: {num}
source_issue: {source if source is not None else 'none'}
review_kind: pre_merge
target_repository: {REPO}
worktree_path: {review_path}
branch: {pr['head']['ref']}
base_reference: {BASE}
base_commit: {base_commit}
candidate_commit: {head_sha}
pr_url: {pr['html_url']}
implementer_profile: {CFG['implementation_profile']}
implementer_vendor_family: {IMPLEMENTATION_VENDOR}
reviewer_profile: {CFG['review_profile']}
reviewer_provider: {REVIEW_PROVIDER or 'profile-default'}
reviewer_model: {REVIEW_MODEL or 'profile-default'}
reviewer_vendor_family: {REVIEW_VENDOR}
vendor_family_independent: {str(independent).lower()}
review_lens: correctness-security-parity
read_only_source: true
review_round: {review_round}
scope_index: {scope_index}/{scope_total}

## Exact scope

Review ONLY these merge-base hunks. Context reads must trace to one of these
hunks. Do not review a whole file, module, or repository.

{scope_text}

## Acceptance question

For only the listed hunks, does this change satisfy the original acceptance
criteria without a correctness or security regression and without divergent
semantics between server and desktop adapters for the same user action?

## Original acceptance criteria

{acceptance}

## Diff-targeted checks

Run only these bounded checks. Do not run the repository gate or a full build.

{checks_text}

## Gate evidence (already run by the orchestrator, do NOT re-run)

{gate_evidence}

## Environment provenance

{preflight}

At review start, independently read back effective cwd `{review_path}`, candidate
`{head_sha}`, profile `{CFG['review_profile']}`, command paths, and dependency
activation. A mismatch is REVIEW-INCOMPLETE, not a product finding.

## Budget

dispatch_hard_cap_seconds: {dispatch_cap}
evidence_budget_seconds: {CFG['leaf_evidence_budget_seconds']}
command_timeout_seconds: {CFG['leaf_command_timeout_seconds']}
max_retries: 1

At 50% stop opening new context; at 70% stop starting commands; at 100% return.

## Stop condition

Stop when every listed hunk has been checked against the single acceptance
question, or when the evidence budget expires. Never expand scope. Return one
structured report containing candidate, profiles, exact scope, acceptance
coverage, checks with exit codes, findings with file:line evidence, gaps,
mutations, environment provenance, next action, and exactly one final line:

`VERDICT: <APPROVED|CHANGES_REQUESTED|REVIEW-INCOMPLETE>`

## Non-goals and live-system boundary

Do not write production code or modify the source worktree. Do not commit, push,
merge, rebase, file or edit tracker items, create Kanban tasks, deploy, access
live systems, or run the full project gate. Scratch work stays outside the
source worktree.
"""


def prepare_review_checkout(pr: dict, *, review_round: int = 1) -> Path:
    """Create or verify an exact detached worktree for read-only review."""
    head_sha = str(pr["head"]["sha"])
    root = Path(
        CFG.get("review_checkout_root")
        or (STATE_PATH.parent / "minna-review-checkouts")
    ).expanduser()
    target = root / f"pr-{pr['number']}-{head_sha[:12]}-r{review_round}"
    if target.exists():
        head = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "HEAD"],
            text=True, capture_output=True, timeout=60,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(target), "status", "--porcelain"],
            text=True, capture_output=True, timeout=60,
        ).stdout.strip()
        if head != head_sha or dirty:
            raise RuntimeError(
                f"review checkout {target} is not exact and clean; preserve it before replacement"
            )
        return target
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(REPO_DIR), "fetch", "--quiet", "origin",
         f"+refs/heads/{pr['head']['ref']}:refs/remotes/origin/{pr['head']['ref']}"],
        check=True, capture_output=True, timeout=300,
    )
    subprocess.run(
        ["git", "-C", str(REPO_DIR), "worktree", "add", "--detach", str(target), head_sha],
        check=True, capture_output=True, timeout=300,
    )
    back = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"],
        text=True, capture_output=True, timeout=60,
    ).stdout.strip()
    if back != head_sha:
        raise RuntimeError(f"review checkout readback {back[:12]} != {head_sha[:12]}")
    return target


def review_preflight(review_path: Path, head_sha: str) -> str:
    """Bounded controller preflight for the same local profile/runtime boundary."""
    profile_env = _clean_subprocess_env()
    # Rich truncates skill names to the detected terminal width. Captured cron
    # subprocesses have no useful terminal geometry, which previously rendered
    # ``kanban-reviewer-con…`` and caused a false missing-skill failure.
    profile_env.update({"COLUMNS": "240", "LINES": "60", "NO_COLOR": "1"})
    profile = subprocess.run(
        [
            shutil.which("hermes") or "hermes",
            "-p", CFG["review_profile"],
            "skills", "list", "--enabled-only",
        ],
        text=True, capture_output=True, timeout=120, env=profile_env,
    )
    if profile.returncode != 0 or "kanban-reviewer-contract" not in profile.stdout:
        detail = " ".join((profile.stderr or profile.stdout).split())[-300:]
        raise RuntimeError(
            "reviewer profile cannot resolve kanban-reviewer-contract"
            + (f": {detail}" if detail else "")
        )
    required = ["git", "cargo", "node", "pnpm"]
    resolved = {name: shutil.which(name) or "" for name in required}
    missing = [name for name, path in resolved.items() if not path]
    if missing:
        raise RuntimeError("review preflight missing commands: " + ", ".join(missing))
    head = subprocess.run(
        [resolved["git"], "-C", str(review_path), "rev-parse", "HEAD"],
        text=True, capture_output=True, timeout=60,
    ).stdout.strip()
    if head != head_sha:
        raise RuntimeError(f"review preflight candidate {head[:12]} != {head_sha[:12]}")
    return (
        "PASS (controller-side profile preflight): "
        f"profile={CFG['review_profile']}; cwd={review_path}; candidate={head_sha}; "
        + "; ".join(f"{name}={path}" for name, path in resolved.items())
    )


def reviewer_worker_preflight(review_path: Path, head_sha: str) -> str:
    """Probe the actual reviewer profile, model route, tools and target cwd."""
    prompt = f"""Environment preflight only. Do not review code and do not mutate files.
Use the terminal tool to run: pwd; git rev-parse HEAD; git status --porcelain;
command -v git cargo node pnpm python3; and print one-line versions for those
commands. The required cwd is {review_path}; the required candidate is
{head_sha}. Return a compact provenance record and make the final line exactly
PREFLIGHT: PASS only when cwd/candidate/cleanliness/commands all match; otherwise
make it PREFLIGHT: FAIL.
"""
    args = [
        shutil.which("hermes") or "hermes",
        "-p", CFG["review_profile"],
        "chat", "-Q", "--max-turns", "4", "-t", "terminal",
    ]
    if REVIEW_MODEL:
        args.extend(["-m", REVIEW_MODEL])
        if REVIEW_PROVIDER:
            args.extend(["--provider", REVIEW_PROVIDER])
    args.extend(["-q", prompt])
    proc = subprocess.run(
        args,
        cwd=str(review_path),
        text=True,
        capture_output=True,
        timeout=int(CFG.get("review_preflight_timeout_seconds", 300)),
        env=_clean_subprocess_env(),
    )
    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
    if proc.returncode != 0 or not re.search(r"(?m)^PREFLIGHT:\s*PASS\s*$", output):
        raise RuntimeError(
            "reviewer worker preflight failed: " + " ".join(output.split())[-800:]
        )
    return "PASS (reviewer-profile worker preflight): " + " ".join(output.split())[-1600:]


def _task_row(task_id: str) -> dict:
    import sqlite3

    conn = sqlite3.connect(str(_board_db()), timeout=10)
    try:
        conn.execute("PRAGMA query_only=1")
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _task_parents(task_id: str) -> list[str]:
    import sqlite3

    conn = sqlite3.connect(str(_board_db()), timeout=10)
    try:
        conn.execute("PRAGMA query_only=1")
        return [
            str(row[0]) for row in conn.execute(
                "SELECT parent_id FROM task_links WHERE child_id = ? ORDER BY parent_id",
                (task_id,),
            )
        ]
    finally:
        conn.close()


def validate_review_task_record(
    task_id: str,
    *,
    head_sha: str,
    review_path: str,
    expected_parents: Iterable[str] = (),
    max_runtime: str = LEAF_RUNTIME,
) -> dict:
    row = _task_row(task_id)
    errors: list[str] = []
    if not row:
        errors.append("task row missing")
    if row.get("assignee") != CFG["review_profile"]:
        errors.append("reviewer assignee mismatch")
    if not task_matches_candidate(row, head_sha):
        errors.append("candidate mismatch")
    if str(row.get("workspace_path") or "") != str(review_path):
        errors.append("worktree mismatch")
    if int(row.get("max_runtime_seconds") or 0) != parse_duration(max_runtime):
        errors.append("durable max_runtime_seconds mismatch")
    if int(row.get("max_retries") or 0) != 1:
        errors.append("durable max_retries mismatch")
    if REVIEW_MODEL and str(row.get("model_override") or "") != REVIEW_MODEL:
        errors.append("review model override mismatch")
    if REVIEW_PROVIDER and str(row.get("provider_override") or "") != REVIEW_PROVIDER:
        errors.append("review provider override mismatch")
    parents = set(_task_parents(task_id))
    wanted = {str(parent) for parent in expected_parents if str(parent).startswith("t_")}
    if parents != wanted:
        errors.append("review dependency graph mismatch")
    if errors:
        reason = "invalid review task record: " + "; ".join(errors)
        if row:
            hermes(["kanban", "--board", BOARD, "block", task_id, reason], timeout=60)
            archive_cycle_task(task_id, reason)
        raise RuntimeError("; ".join(errors))
    return row


def implementation_task_for_pr(pr: dict, tasks: list[dict], rec: dict) -> str:
    if rec.get("rework_card"):
        return str(rec["rework_card"])
    source = source_issue_number(pr)
    if source is not None:
        needle = f"/issues/{source}"
        matches = [
            task for task in tasks
            if needle in str(task.get("body") or "")
            or re.search(rf"\b#{source}\b", str(task.get("title") or ""))
        ]
        if matches:
            matches.sort(key=lambda row: int(row.get("created_at") or 0))
            return str(matches[-1].get("id"))
    return f"forgejo-pr-{pr['number']}-recovered-artifact"


def create_review_leaf(
    *,
    pr: dict,
    implementation_task: str,
    review_path: str,
    base_commit: str,
    scope: list[str],
    evidence: list,
    acceptance: str,
    focused_checks: list[str],
    review_round: int,
    scope_index: int,
    scope_total: int,
    preflight: str,
) -> str:
    num = int(pr["number"])
    head_sha = str(pr["head"]["sha"])
    implementation_key = re.sub(
        r"[^a-zA-Z0-9]+", "-", implementation_task
    ).strip("-")[:32]
    key = (
        f"minna-pr-{num}-review-{head_sha[:12]}-base-{base_commit[:12]}-impl-{implementation_key}-"
        f"round-{review_round}-scope-{scope_index}"
    )
    gate_note = rpi.render_gate_evidence(
        evidence,
        gate_declared=rpi.repo_declares_gate(REPO_DIR, head_sha),
        candidate=head_sha,
    )
    body = render_review_packet(
        pr=pr,
        implementation_task=implementation_task,
        review_path=review_path,
        base_commit=base_commit,
        scope=scope,
        gate_evidence=gate_note,
        acceptance=acceptance,
        focused_checks=focused_checks,
        review_round=review_round,
        scope_index=scope_index,
        scope_total=scope_total,
        preflight=preflight,
    )
    args = [
        "kanban", "--board", BOARD, "create",
        f"Review PR #{num} r{review_round}/{scope_index}: {pr['title'][:60]}",
        "--body", body,
        "--assignee", CFG["review_profile"],
        "--workspace", f"dir:{review_path}",
        "--priority", "70",
        "--max-runtime", LEAF_RUNTIME,
        "--max-retries", "1",
        "--initial-status", "blocked",
        "--idempotency-key", key,
        "--created-by", "minna-review-cycle",
        "--skill", "kanban-reviewer-contract",
    ]
    if REVIEW_MODEL:
        args.extend(["--model", REVIEW_MODEL])
        if REVIEW_PROVIDER:
            args.extend(["--provider", REVIEW_PROVIDER])
    if implementation_task.startswith("t_") and _task_row(implementation_task):
        args.extend(["--parent", implementation_task])
    out = hermes(args, as_json=True)
    task = out.get("task", out)
    tid = task.get("id", "")
    if not tid:
        raise RuntimeError("Kanban create returned no review task id")
    row = validate_review_task_record(
        tid,
        head_sha=head_sha,
        review_path=review_path,
        expected_parents=[implementation_task],
    )
    if row.get("status") == "blocked" and not row.get("block_kind"):
        hermes(["kanban", "--board", BOARD, "unblock", tid], timeout=60)
        back = _task_row(tid)
        if back and back.get("status") == "blocked":
            raise RuntimeError(f"review task {tid} remained blocked after unblock readback")
    return tid


def create_fan_in_card(
    *,
    pr: dict,
    implementation_task: str,
    base_commit: str,
    expected_scope: list[str],
    leaf_reports: list[dict],
    review_round: int,
) -> str:
    """Create the report-only fan-in after every required leaf is terminal."""
    num = int(pr["number"])
    head_sha = str(pr["head"]["sha"])
    leaf_ids = [str(row["task_id"]) for row in leaf_reports]
    reconciliation = fan_in_verdict(expected_scope, leaf_reports)
    implementation_key = re.sub(
        r"[^a-zA-Z0-9]+", "-", implementation_task
    ).strip("-")[:32]
    key = (
        f"minna-pr-{num}-fan-in-{head_sha[:12]}-base-{base_commit[:12]}-"
        f"impl-{implementation_key}-"
        f"round-{review_round}"
    )
    expected = "\n".join(f"- `{entry}`" for entry in expected_scope)
    reports = "\n\n".join(
        f"### {row['task_id']}\n"
        f"scope: {json.dumps(row.get('scope') or [])}\n"
        f"verdict: {row.get('verdict')}\n"
        f"report excerpt:\n{str(row.get('evidence') or '')[-3000:]}"
        for row in leaf_reports
    )
    body = f"""Deterministic coverage reconciliation for PR #{num}.

implementation_task: {implementation_task}
source_pr: {num}
candidate_commit: {head_sha}
base_commit: {base_commit}
role: completion-verifier
read_only_source: true
repository_access: prohibited
review_round: {review_round}

## Expected exact scope

{expected}

## Leaf reports

{reports}

## Controller reconciliation

{json.dumps(reconciliation, sort_keys=True)}

Read only the parent reports above. Do not inspect the repository, rerun checks,
change source/tracker/board state, or invent findings. Confirm that every
expected hunk appears exactly once and every leaf verdict is APPROVED. Any
missing, duplicated, unexpected, incomplete, or changes-requested coverage must
return the corresponding non-approval verdict.

Dispatch hard cap: {parse_duration(FAN_IN_RUNTIME)}s
Evidence budget: {min(300, parse_duration(FAN_IN_RUNTIME))}s
Per-command timeout: 60s
max_retries: 1

Return candidate, leaf task ids, exact coverage, gaps, and exactly one final line:
`VERDICT: <APPROVED|CHANGES_REQUESTED|REVIEW-INCOMPLETE>`
"""
    args = [
        "kanban", "--board", BOARD, "create",
        f"Fan-in PR #{num} r{review_round}",
        "--body", body,
        "--assignee", CFG["review_profile"],
        "--priority", "69",
        "--max-runtime", FAN_IN_RUNTIME,
        "--max-retries", "1",
        "--initial-status", "blocked",
        "--idempotency-key", key,
        "--created-by", "minna-review-cycle",
    ]
    for leaf_id in leaf_ids:
        args.extend(["--parent", leaf_id])
    if REVIEW_MODEL:
        args.extend(["--model", REVIEW_MODEL])
        if REVIEW_PROVIDER:
            args.extend(["--provider", REVIEW_PROVIDER])
    out = hermes(args, as_json=True)
    task = out.get("task", out)
    tid = str(task.get("id") or "")
    if not tid:
        raise RuntimeError("Kanban create returned no fan-in task id")
    row = _task_row(tid)
    errors = []
    if row.get("assignee") != CFG["review_profile"]:
        errors.append("fan-in assignee mismatch")
    if not task_matches_candidate(row, head_sha):
        errors.append("fan-in candidate mismatch")
    if int(row.get("max_runtime_seconds") or 0) != parse_duration(FAN_IN_RUNTIME):
        errors.append("fan-in max_runtime_seconds mismatch")
    if int(row.get("max_retries") or 0) != 1:
        errors.append("fan-in max_retries mismatch")
    if set(_task_parents(tid)) != set(leaf_ids):
        errors.append("fan-in dependency graph mismatch")
    if errors:
        reason = "invalid fan-in record: " + "; ".join(errors)
        if row:
            hermes(["kanban", "--board", BOARD, "block", tid, reason], timeout=60)
            archive_cycle_task(tid, reason)
        raise RuntimeError("; ".join(errors))
    if row.get("status") == "blocked" and not row.get("block_kind"):
        hermes(["kanban", "--board", BOARD, "unblock", tid], timeout=60)
        back = _task_row(tid)
        if back and back.get("status") == "blocked":
            raise RuntimeError(f"fan-in task {tid} remained blocked after unblock readback")
    return tid


def create_rework_card(pr: dict, reason: str, detail: str) -> str:
    num = pr["number"]
    key = f"minna-pr-{num}-rework-{pr['head']['sha'][:12]}"
    body = f"""PR #{num} was returned by the review cycle.

pr_url: {pr['html_url']}
branch: {pr['head']['ref']}
reason: {reason}

## What must change

{detail}

## Definition of done

- The stated blockers are fixed on branch `{pr['head']['ref']}`.
- The repository gate passes from a fresh clone.
- Changes are committed AND pushed. An uncommitted worktree is not delivery.
"""
    out = hermes([
        "kanban", "--board", BOARD, "create",
        f"Rework PR #{num}: {reason[:60]}",
        "--body", body,
        "--assignee", CFG["implementation_profile"],
        "--priority", "75",
        "--max-runtime", CFG["rework_runtime"],
        "--max-retries", "1",
        "--initial-status", "blocked",
        "--idempotency-key", key,
        "--created-by", "minna-review-cycle",
    ], as_json=True)
    task = out.get("task", out)
    tid = str(task.get("id") or "")
    if not tid:
        raise RuntimeError("Kanban create returned no rework task id")
    row = _task_row(tid)
    errors = []
    if row.get("assignee") != CFG["implementation_profile"]:
        errors.append("rework assignee mismatch")
    if int(row.get("max_runtime_seconds") or 0) != parse_duration(CFG["rework_runtime"]):
        errors.append("rework max_runtime_seconds mismatch")
    if int(row.get("max_retries") or 0) != 1:
        errors.append("rework max_retries mismatch")
    if errors:
        reason = "invalid rework record: " + "; ".join(errors)
        if row:
            hermes(["kanban", "--board", BOARD, "block", tid, reason], timeout=60)
            archive_cycle_task(tid, reason)
        raise RuntimeError("; ".join(errors))
    if row.get("status") == "blocked" and not row.get("block_kind"):
        hermes(["kanban", "--board", BOARD, "unblock", tid], timeout=60)
        back = _task_row(tid)
        if back and back.get("status") == "blocked":
            raise RuntimeError(f"rework task {tid} remained blocked after unblock readback")
    return tid


VERDICT_RE = re.compile(r"VERDICT:\s*(APPROVED|CHANGES_REQUESTED|REVIEW-INCOMPLETE)", re.I)


def leaf_verdict(task: dict) -> tuple[str | None, str]:
    """Read the verdict out of a finished leaf's comments/results/runs."""
    parts = [str(task.get("result") or "")]
    verdict_parts = list(parts)
    structured_verdicts: list[str] = []
    try:
        detail = hermes(["kanban", "--board", BOARD, "show", task["id"]], as_json=True)
        latest_summary = str(detail.get("latest_summary") or "")
        parts.append(latest_summary)
        verdict_parts.append(latest_summary)
        for comment in detail.get("comments") or []:
            body = str(comment.get("body") or "")
            parts.append(body)
            verdict_parts.append(body)
        for run in detail.get("runs") or []:
            summary = str(run.get("summary") or "")
            parts.append(summary)
            verdict_parts.append(summary)
            metadata = run.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            if isinstance(metadata, dict):
                parts.append(json.dumps(metadata, sort_keys=True))
                structured = str(metadata.get("verdict") or "").upper()
                if structured in {"APPROVED", "CHANGES_REQUESTED", "REVIEW-INCOMPLETE"}:
                    # ``kanban_complete`` stores the terminal verdict in run
                    # metadata even when its human summary omits the packet's
                    # final ``VERDICT:`` line. Treat that structured terminal
                    # field as the machine-readable marker, not as prose.
                    structured_verdicts.append(structured)
    except Exception as exc:
        diagnostic = f"task detail read failed: {type(exc).__name__}: {exc}"
        parts.append(diagnostic)
        verdict_parts.append(diagnostic)
    blob = "\n".join(part for part in parts if part)
    if structured_verdicts:
        return structured_verdicts[-1], blob[-12000:]
    # Raw metadata is evidence, not a verdict surface. In particular, arbitrary
    # metadata keys/values containing ``VERDICT: APPROVED`` must not authorize a
    # merge; only the allowlisted ``metadata.verdict`` field above may do that.
    hits = VERDICT_RE.findall("\n".join(part for part in verdict_parts if part))
    if not hits:
        return None, blob[-12000:]
    return hits[-1].upper(), blob[-12000:]


def pr_comment(num: int, text: str) -> None:
    api(f"/repos/{REPO}/issues/{num}/comments", "POST", {"body": text})


def verify_merged_readback(pr: dict) -> tuple[bool, str]:
    """Verify Forgejo's merged state against the remote target branch."""
    if not pr.get("merged"):
        return False, "pull request is not merged on API readback"
    merged_sha = str(pr.get("merged_commit_sha") or "")
    try:
        remote = subprocess.run(
            ["git", "-C", str(REPO_DIR), "ls-remote", "origin", f"refs/heads/{BASE_BRANCH}"],
            text=True, capture_output=True, timeout=120,
        )
        if remote.returncode != 0 or not remote.stdout.strip():
            return False, "PR merged in API but target branch remote readback failed"
        remote_sha = remote.stdout.split()[0]
        if not merged_sha:
            merged_sha = remote_sha
        if remote_sha != merged_sha:
            subprocess.run(
                ["git", "-C", str(REPO_DIR), "fetch", "--quiet", "origin",
                 f"+refs/heads/{BASE_BRANCH}:{BASE_REMOTE_REF}"],
                check=True, capture_output=True, timeout=180,
            )
            ancestor = subprocess.run(
                ["git", "-C", str(REPO_DIR), "merge-base", "--is-ancestor",
                 merged_sha, BASE_REMOTE_REF],
                capture_output=True, timeout=60,
            )
            if ancestor.returncode != 0:
                return False, (
                    f"API reports merged {merged_sha[:12]} but remote target is {remote_sha[:12]} "
                    "and does not contain it"
                )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"PR merged in API but git readback failed: {type(exc).__name__}: {exc}"
    return True, merged_sha


def merge_pr(pr: dict) -> tuple[bool, str]:
    """Request an exact-head merge and immediately attempt external readback."""
    num = pr["number"]
    expected_head = str(pr.get("head", {}).get("sha") or "")
    try:
        api(f"/repos/{REPO}/pulls/{num}/merge", "POST", {
            "Do": CFG["merge_method"],
            "MergeTitleField": f"{pr['title']}",
            "delete_branch_after_merge": False,
            "head_commit_id": expected_head,
        })
        back = api(f"/repos/{REPO}/pulls/{num}")
    except RuntimeError as exc:
        return False, str(exc)[:250]
    return verify_merged_readback(back)


def current_base_commit() -> str:
    subprocess.run(
        ["git", "-C", str(REPO_DIR), "fetch", "--quiet", "origin",
         f"+refs/heads/{BASE_BRANCH}:{BASE_REMOTE_REF}"],
        check=True, capture_output=True, timeout=180,
    )
    return subprocess.run(
        ["git", "-C", str(REPO_DIR), "rev-parse", BASE_REMOTE_REF],
        check=True, text=True, capture_output=True, timeout=60,
    ).stdout.strip()


def forgejo_ci_state(head_sha: str) -> tuple[bool, str]:
    """Require Forgejo CI success when the candidate declares a workflow."""
    try:
        combined = api(f"/repos/{REPO}/commits/{head_sha}/status")
    except Exception as exc:
        return False, f"CI status readback failed: {type(exc).__name__}"
    statuses = combined.get("statuses") if isinstance(combined, dict) else None
    total = int(combined.get("total_count") or 0) if isinstance(combined, dict) else 0
    state = str(combined.get("state") or "").lower() if isinstance(combined, dict) else ""
    if total == 0 and not statuses:
        if rpi.repo_declares_gate(REPO_DIR, head_sha):
            return False, "candidate declares CI but Forgejo has no commit status"
        return True, "no declared Forgejo CI workflow"
    if state not in {"success", "successful"}:
        return False, f"Forgejo CI state is {state or 'unknown'}"
    return True, f"Forgejo CI success ({total or len(statuses or [])} statuses)"


def merged_source_issues() -> set[int]:
    """Source issues whose associated PR is verified merged, independent of issue state."""
    merged: set[int] = set()
    for row in pulls("closed"):
        source = source_issue_number(row)
        if source not in MERGE_ORDER:
            continue
        detail = row
        if "merged" not in detail:
            detail = api(f"/repos/{REPO}/pulls/{row['number']}")
        if detail.get("merged"):
            merged.add(int(source))
    return merged


def merged_priority_prs() -> set[int]:
    merged: set[int] = set()
    for number in PRIORITY_PRS:
        try:
            row = api(f"/repos/{REPO}/pulls/{number}")
        except Exception:
            continue
        if row.get("merged"):
            merged.add(number)
    return merged


def _review_worktree_clean(path: str, head_sha: str) -> tuple[bool, str]:
    head = subprocess.run(
        ["git", "-C", path, "rev-parse", "HEAD"],
        text=True, capture_output=True, timeout=60,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", path, "status", "--porcelain"],
        text=True, capture_output=True, timeout=60,
    ).stdout.strip()
    if head != head_sha:
        return False, f"review target moved to {head[:12]}"
    if dirty:
        return False, "review source worktree was mutated"
    return True, "clean"


def review_leaf_report(task: dict, scope: list[str], head_sha: str, review_path: str) -> dict:
    verdict, evidence = leaf_verdict(task)
    verdict = verdict or "REVIEW-INCOMPLETE"
    if not task_matches_candidate(task, head_sha):
        verdict = "REVIEW-INCOMPLETE"
        evidence += "\ncontract gap: candidate mismatch"
    if verdict == "APPROVED":
        absent = [entry for entry in scope if entry not in evidence]
        if absent:
            verdict = "REVIEW-INCOMPLETE"
            evidence += "\ncontract gap: report omitted exact scope " + ", ".join(absent)
    clean, clean_note = _review_worktree_clean(review_path, head_sha)
    if not clean:
        verdict = "REVIEW-INCOMPLETE"
        evidence += "\ncontract gap: " + clean_note
    return {
        "task_id": task.get("id"),
        "scope": scope,
        "verdict": verdict,
        "evidence": evidence[-5000:],
    }


def archive_cycle_task(task_id: str, reason: str) -> None:
    hermes(["kanban", "--board", BOARD, "comment", task_id, reason], timeout=60)
    hermes(["kanban", "--board", BOARD, "archive", task_id], timeout=60)
    back = _task_row(task_id)
    if back and back.get("status") != "archived":
        raise RuntimeError(f"task {task_id} archive did not survive readback")


def retire_review_graph(rec: dict, reason: str) -> None:
    """Stop and archive only stale active tasks owned by this controller record."""
    ids = [str(spec.get("id") or "") for spec in rec.get("leaf_ids") or []]
    if rec.get("fan_in"):
        ids.append(str(rec["fan_in"]))
    for task_id in dict.fromkeys(task_id for task_id in ids if task_id):
        row = _task_row(task_id)
        if not row or row.get("status") in {"done", "archived"}:
            continue
        if row.get("status") in {"ready", "running", "review", "todo"}:
            hermes([
                "kanban", "--board", BOARD, "block", task_id, reason,
                "--kind", "transient",
            ], timeout=60)
        archive_cycle_task(task_id, reason)


def reconcile_pending_merges(state: dict, apply: bool, lines: list[str]) -> None:
    """Recover a merge that may have succeeded after the controller lost readback."""
    for number, rec in state.get("prs", {}).items():
        requested_head = str(rec.get("merge_requested_head") or "")
        if not requested_head or rec.get("merged"):
            continue
        pr = api(f"/repos/{REPO}/pulls/{number}")
        if not pr.get("merged"):
            lines.append(f"PR #{number}: prior merge request is not merged; safe to retry")
            continue
        actual_head = str(pr.get("head", {}).get("sha") or "")
        if actual_head and actual_head != requested_head:
            lines.append(
                f"PR #{number}: merged head {actual_head[:12]} differs from requested "
                f"{requested_head[:12]}; manual factory audit required"
            )
            continue
        ok, detail = verify_merged_readback(pr)
        if not ok:
            lines.append(f"PR #{number}: merge remains unverified — {detail}")
            continue
        if not apply:
            lines.append(f"PR #{number}: WOULD record recovered merge {detail[:12]}")
            continue
        rec["merged"] = detail
        rec.pop("merge_requested_head", None)
        lines.append(f"PR #{number}: recovered and read back merge {detail[:12]}")


def close_pending_sources(state: dict, apply: bool, lines: list[str]) -> None:
    for number, rec in state.get("prs", {}).items():
        source = rec.get("source_issue")
        if not rec.get("merged") or source is None or rec.get("source_closed"):
            continue
        pr = api(f"/repos/{REPO}/pulls/{number}")
        if not apply:
            lines.append(f"PR #{number}: WOULD close source issue #{source}")
            continue
        ok, note = close_source_issue(pr)
        lines.append(f"PR #{number}: {note}")
        if ok:
            rec["source_closed"] = True


# ------------------------------------------------------------------- reap ---
# Only cards this cycle actually created may be reaped. `created_by` is set on
# every card the script creates; anything else on the board belongs to another
# lane (orchestrator work, factory-defect cards, hand-filed tasks) and reaping
# it is out of scope for a PR-review cron.
REAPABLE_CREATORS = {"minna-review-cycle"}


def _durable_runtime_fields() -> dict:
    """Read per-task liveness/budget fields the CLI does not expose.

    `hermes kanban list --json` omits `last_heartbeat_at` and
    `max_runtime_seconds`, so reaping decisions based on the CLI alone cannot
    see whether a worker is still alive. Returns {} on any error, which makes
    reap fall back to wall-clock behaviour rather than crash the tick.
    """
    import sqlite3

    try:
        conn = sqlite3.connect(str(_board_db()), timeout=10)
    except Exception:
        return {}
    try:
        conn.execute("PRAGMA query_only=1")
        conn.row_factory = sqlite3.Row
        return {
            r["id"]: {
                "last_heartbeat_at": r["last_heartbeat_at"],
                "max_runtime_seconds": r["max_runtime_seconds"],
            }
            for r in conn.execute(
                "SELECT id, last_heartbeat_at, max_runtime_seconds FROM tasks"
            )
        }
    except Exception:
        return {}
    finally:
        conn.close()


def reap(apply: bool) -> list[str]:
    """Block cards THIS cycle created that outlived their budget.

    Durable runtime fields are read back after creation and again here. The
    reaper remains a second backstop for stale runs if the dispatcher misses a
    timeout or a legacy card predates durable runtime persistence.

    Two hard rules, both learned from reaping a healthy worker:

    * **Ownership.** Only `created_by` in ``REAPABLE_CREATORS`` is eligible. An
      earlier version reaped every running card on the board and killed an
      unrelated orchestrator task that had heartbeated 48 times.
    * **Liveness beats wall clock.** A card with a recent heartbeat is alive and
      is never reaped, regardless of age. Wall-clock age alone cannot distinguish
      a wedged worker from a long, healthy one; the heartbeat can.
    """
    notes = []
    now = time.time()
    ceilings = {
        CFG["review_profile"]: parse_duration(LEAF_RUNTIME),
        CFG["implementation_profile"]: parse_duration(CFG["rework_runtime"]),
    }
    default_cap = max(ceilings.values()) if ceilings else 3600
    stale_after = int(CFG.get("heartbeat_stale_seconds", 600))
    # `hermes kanban list --json` does NOT expose last_heartbeat_at or
    # max_runtime_seconds (verified against the live CLI), so a heartbeat guard
    # built on the list output would silently never fire. Read them from the
    # board database instead.
    live = _durable_runtime_fields()
    for t in board_tasks():
        if t.get("status") != "running":
            continue
        creator = str(t.get("created_by") or "")
        if creator not in REAPABLE_CREATORS:
            continue  # not our lane; never touch another owner's card
        started = t.get("started_at") or t.get("claimed_at") or t.get("updated_at")
        if not started:
            continue
        try:
            age = now - float(started)
        except (TypeError, ValueError):
            continue
        durable = live.get(str(t.get("id") or ""), {})
        beat = durable.get("last_heartbeat_at")
        if beat:
            try:
                if now - float(beat) < stale_after:
                    continue  # alive and reporting; not a zombie
            except (TypeError, ValueError):
                pass
        cap = int(durable.get("max_runtime_seconds") or 0) or ceilings.get(
            str(t.get("assignee") or ""), default_cap
        )
        if age > cap * 1.5:
            notes.append(f"reap {t['id']} running {int(age)}s > cap {cap}s")
            if apply:
                hermes([
                    "kanban", "--board", BOARD, "block", t["id"],
                    f"exceeded runtime budget ({int(age)}s > {cap}s)",
                    "--kind", "transient",
                ], timeout=60)
                back = _task_row(str(t["id"]))
                if back and back.get("status") != "blocked":
                    raise RuntimeError(f"task {t['id']} reap block did not survive readback")
    return notes


# ------------------------------------------------------------------- tick ---
def tick(apply: bool) -> int:
    state = load_state()
    state.setdefault("prs", {})
    lines: list[str] = []
    reconcile_pending_merges(state, apply, lines)
    close_pending_sources(state, apply, lines)
    if apply:
        save_state(state)

    pr_rows = open_prs()
    tasks = board_tasks()
    in_flight = sum(
        1 for task in tasks
        if task.get("created_by") == "minna-review-cycle"
        and task.get("status") in {"ready", "running", "review", "todo"}
        and re.search(r"(?:Review|Fan-in) PR #", str(task.get("title") or ""))
    )
    merges = 0
    gates_run = 0
    base_tip = current_base_commit()
    merged_sources = merged_source_issues()
    merged_priorities = merged_priority_prs()
    force_fresh = {
        int(value) for value in (CFG.get("force_fresh_review_prs") or PRIORITY_PRS)
    }

    def scope_key(scope: list[str]) -> str:
        return json.dumps(scope, separators=(",", ":"), sort_keys=False)

    for pr in sorted(pr_rows, key=pr_rank):
        num = int(pr["number"])
        head = str(pr["head"]["sha"])
        source = source_issue_number(pr)
        rec = state["prs"].setdefault(str(num), {})
        if rec.get("head") != head:
            if apply and rec:
                retire_review_graph(rec, f"PR #{num} candidate head changed")
            rec.clear()
            rec.update({"head": head, "source_issue": source, "review_round": 1})
        else:
            rec["source_issue"] = source

        route_fingerprint = f"{CFG['review_profile']}:{REVIEW_PROVIDER}:{REVIEW_MODEL}"
        if rec.get("review_route_failed"):
            if rec.get("review_route_failure_fingerprint") == route_fingerprint:
                lines.append(
                    f"PR #{num}: reviewer route failed after {MAX_REVIEW_ROUNDS} rounds; "
                    "factory-owned route repair required"
                )
                continue
            rec.pop("review_route_failed", None)
            rec.pop("review_route_failure_fingerprint", None)

        if rec.get("gate") == "red":
            lines.append(f"PR #{num}: held for rework after measured red gate")
            continue

        # A green claim without command/exit/commit evidence, or one measured
        # against an older target tip, is not reusable gate evidence.
        if not gate_evidence_valid(rec, head) or rec.get("gate_base_commit") != base_tip:
            had_review_graph = any(
                rec.get(key) for key in (
                    "leaf_ids", "approved_reports", "fan_in", "review_approved"
                )
            )
            if apply and had_review_graph:
                retire_review_graph(rec, f"PR #{num} gate or target base changed")
            if had_review_graph:
                rec["review_round"] = int(rec.get("review_round") or 1) + 1
            for key in (
                "leaf_ids", "approved_reports", "fan_in", "fan_in_attempts",
                "fan_in_evidence", "manifest", "scopes", "review_path",
                "preflight", "preflight_fingerprint", "review_approved",
                "duplicate_of", "approved_by_run", "implementation_task",
                "review_route_failed", "review_route_failure_fingerprint",
            ):
                rec.pop(key, None)
            if not apply:
                lines.append(f"PR #{num}: WOULD run the fresh-clone gate twice")
                continue
            if gates_run >= MAX_GATES_PER_TICK:
                lines.append(f"PR #{num}: gate deferred (tick cap {MAX_GATES_PER_TICK})")
                continue
            gates_run += 1
            log = Path(f"/tmp/minna-gate-pr{num}.log")
            ok, evidence = run_gate(head, str(pr["head"]["ref"]), log)
            note = gate_summary(evidence)
            rec.update({
                "gate": "green" if ok else "red",
                "gate_note": note,
                "gate_evidence": serialize_evidence(evidence),
                "gate_base_commit": base_tip,
            })
            lines.append(f"PR #{num}: gate {'GREEN' if ok else 'RED'} — {note}")
            if not ok:
                pr_comment(
                    num,
                    f"**Review cycle: measured gate is red.**\n\n```\n{note}\n```\n\n"
                    "Returned for rework. Nothing merges on a red gate.",
                )
                rec["rework_card"] = create_rework_card(
                    pr, "repository gate is red", note
                )
                save_state(state)
                continue
            save_state(state)

        merge_base = subprocess.run(
            ["git", "-C", str(REPO_DIR), "merge-base", base_tip, head],
            check=True, text=True, capture_output=True, timeout=90,
        ).stdout.strip()
        manifest = changed_files(base_tip, head)
        if not manifest:
            lines.append(f"PR #{num}: no merge-base delta; merge is not authorized")
            if apply and not rec.get("rework_card"):
                rec["rework_card"] = create_rework_card(
                    pr,
                    "pull request has no merge-base delta",
                    "Push the intended committed change or close the empty PR.",
                )
                save_state(state)
            continue

        if rec.get("manifest") != manifest or rec.get("review_base_commit") != merge_base:
            rec["manifest"] = manifest
            rec["review_base_commit"] = merge_base
            rec["scopes"] = split_manifest_by_layer(manifest, MAX_FILES_PER_LEAF)
            rec["approved_reports"] = []
            rec["leaf_ids"] = []
            rec.pop("fan_in", None)
            rec.pop("review_approved", None)
        scopes = [list(scope) for scope in rec["scopes"]]
        partitioned = [entry for scope in scopes for entry in scope]
        if sorted(partitioned) != sorted(manifest) or len(partitioned) != len(manifest):
            raise RuntimeError(f"PR #{num}: scope partition lost or duplicated manifest entries")

        approved_reports = [
            report for report in rec.get("approved_reports", [])
            if report.get("verdict") == "APPROVED"
            and report.get("scope") in scopes
        ]
        rec["approved_reports"] = approved_reports

        # Reuse an earlier exact-tree approval only when its original packet
        # itself proves current exact-scope coverage and vendor independence.
        if not approved_reports and not rec.get("leaf_ids") and num not in force_fresh:
            prior = existing_approval(head)
            if prior is not None:
                prior_row = _task_row(prior.task_id)
                prior_body = str(prior_row.get("body") or "")
                if (
                    "vendor_family_independent: true" in prior_body.lower()
                    and f"base_commit: {merge_base}" in prior_body
                    and all(entry in prior_body for entry in manifest)
                ):
                    approved_reports = [{
                        "task_id": prior.task_id,
                        "scope": manifest,
                        "verdict": "APPROVED",
                        "evidence": prior.as_note(),
                    }]
                    rec["approved_reports"] = approved_reports
                    rec["duplicate_of"] = prior.task_id
                    rec["approved_by_run"] = prior.run_id

        leaf_specs = list(rec.get("leaf_ids") or [])
        if leaf_specs:
            terminal_reports: list[dict] = []
            active_specs: list[dict] = []
            for spec in leaf_specs:
                row = _task_row(str(spec["id"]))
                if not row:
                    terminal_reports.append({
                        "task_id": spec["id"], "scope": spec["scope"],
                        "verdict": "REVIEW-INCOMPLETE",
                        "evidence": "review task row disappeared",
                    })
                    continue
                status = str(row.get("status") or "")
                verdict, _ = leaf_verdict(row) if not row.get("current_run_id") else (None, "")
                terminal = status in {"done", "archived", "blocked", "triage"} or (
                    verdict is not None and not row.get("current_run_id")
                )
                if not terminal:
                    active_specs.append(spec)
                    continue
                terminal_reports.append(review_leaf_report(
                    row,
                    list(spec["scope"]),
                    head,
                    str(spec["review_path"]),
                ))
            if active_specs:
                shown = ", ".join(
                    f"{spec['id']}:{_task_row(str(spec['id'])).get('status')}"
                    for spec in active_specs
                )
                lines.append(f"PR #{num}: independent review running ({shown})")
                continue

            rec["leaf_ids"] = []
            requested = [
                report for report in terminal_reports
                if report["verdict"] == "CHANGES_REQUESTED"
            ]
            incomplete = [
                report for report in terminal_reports
                if report["verdict"] == "REVIEW-INCOMPLETE"
            ]
            approved_reports.extend(
                report for report in terminal_reports
                if report["verdict"] == "APPROVED"
            )
            rec["approved_reports"] = approved_reports
            for report in terminal_reports:
                row = _task_row(str(report["task_id"]))
                if row and row.get("status") not in {"done", "archived"}:
                    archive_cycle_task(
                        str(report["task_id"]),
                        f"Preserved terminal review outcome: {report['verdict']}",
                    )
            if requested:
                evidence = "\n\n".join(str(row["evidence"]) for row in requested)
                if apply and not rec.get("rework_card"):
                    pr_comment(num, f"**Review cycle: changes requested.**\n\n{evidence[:6000]}")
                    rec["rework_card"] = create_rework_card(
                        pr, "reviewer requested changes", evidence[:5000]
                    )
                    save_state(state)
                lines.append(f"PR #{num}: reviewer requested changes; rework queued")
                continue
            if incomplete:
                incomplete_keys = {scope_key(list(row["scope"])) for row in incomplete}
                repaired_scopes: list[list[str]] = []
                for scope in scopes:
                    report = next(
                        (row for row in incomplete if list(row["scope"]) == scope), None
                    )
                    infrastructure_fault = bool(report and re.search(
                        r"(?i)429|provider|route|preflight|invalid review packet|capability",
                        str(report.get("evidence") or ""),
                    ))
                    if scope_key(scope) in incomplete_keys and len(scope) > 1 and not infrastructure_fault:
                        midpoint = max(1, len(scope) // 2)
                        repaired_scopes.extend([scope[:midpoint], scope[midpoint:]])
                    else:
                        repaired_scopes.append(scope)
                rec["scopes"] = repaired_scopes
                next_round = int(rec.get("review_round") or 1) + 1
                rec["review_round"] = next_round
                rec.pop("preflight", None)
                rec.pop("review_path", None)
                if next_round > MAX_REVIEW_ROUNDS:
                    rec["review_route_failed"] = True
                    rec["review_route_failure_fingerprint"] = route_fingerprint
                    lines.append(
                        f"PR #{num}: REVIEW-INCOMPLETE after {MAX_REVIEW_ROUNDS} bounded "
                        "rounds; factory-owned reviewer route repair required"
                    )
                    if apply:
                        save_state(state)
                    continue
                lines.append(
                    f"PR #{num}: review incomplete; preserved evidence and queued fresh round "
                    f"{rec['review_round']}"
                )
                if apply:
                    save_state(state)
                continue

        covered = {scope_key(list(row["scope"])) for row in approved_reports}
        pending = [scope for scope in scopes if scope_key(scope) not in covered]
        if pending:
            if in_flight >= MAX_IN_FLIGHT:
                lines.append(f"PR #{num}: review queued behind in-flight cap {MAX_IN_FLIGHT}")
                continue
            if not apply:
                lines.append(
                    f"PR #{num}: WOULD create exact-scope review "
                    f"({len(pending[0])} files, round {rec.get('review_round', 1)})"
                )
                continue
            if REVIEW_VENDOR == "unknown" or IMPLEMENTATION_VENDOR == "unknown" or (
                REVIEW_VENDOR.lower() == IMPLEMENTATION_VENDOR.lower()
            ):
                lines.append(f"PR #{num}: independent vendor-family review route unavailable")
                continue
            review_round = int(rec.get("review_round") or 1)
            review_path = prepare_review_checkout(pr, review_round=review_round)
            fingerprint = f"{CFG['review_profile']}:{REVIEW_PROVIDER}:{REVIEW_MODEL}:{head}:{review_round}"
            if rec.get("preflight_fingerprint") != fingerprint:
                controller = review_preflight(review_path, head)
                worker = reviewer_worker_preflight(review_path, head)
                rec["preflight"] = worker + "\n" + controller
                rec["preflight_fingerprint"] = fingerprint
            implementation_task = implementation_task_for_pr(pr, tasks, rec)
            scope = pending[0]
            scope_index = scopes.index(scope) + 1
            task_id = create_review_leaf(
                pr=pr,
                implementation_task=implementation_task,
                review_path=str(review_path),
                base_commit=merge_base,
                scope=scope,
                evidence=deserialize_evidence(rec.get("gate_evidence")),
                acceptance=source_acceptance(pr),
                focused_checks=focused_checks_for_scope(merge_base, head, scope),
                review_round=review_round,
                scope_index=scope_index,
                scope_total=len(scopes),
                preflight=str(rec["preflight"]),
            )
            rec["leaf_ids"] = [{
                "id": task_id,
                "scope": scope,
                "review_path": str(review_path),
            }]
            rec["implementation_task"] = implementation_task
            in_flight += 1
            lines.append(
                f"PR #{num}: fresh independent review {task_id} created "
                f"({scope_index}/{len(scopes)})"
            )
            save_state(state)
            continue

        reconciliation = fan_in_verdict(manifest, approved_reports)
        if reconciliation["verdict"] != "APPROVED":
            lines.append(f"PR #{num}: exact review coverage is incomplete")
            continue

        if len(scopes) > 1:
            fan_in_id = str(rec.get("fan_in") or "")
            if not fan_in_id:
                if int(rec.get("fan_in_attempts") or 0) >= 2:
                    lines.append(
                        f"PR #{num}: fan-in failed twice; factory-owned route repair required"
                    )
                    continue
                if in_flight >= MAX_IN_FLIGHT:
                    lines.append(
                        f"PR #{num}: fan-in queued behind in-flight cap {MAX_IN_FLIGHT}"
                    )
                    continue
                if not apply:
                    lines.append(f"PR #{num}: WOULD create bounded report-only fan-in")
                    continue
                fan_in_id = create_fan_in_card(
                    pr=pr,
                    implementation_task=str(rec.get("implementation_task") or f"forgejo-pr-{num}"),
                    base_commit=merge_base,
                    expected_scope=manifest,
                    leaf_reports=approved_reports,
                    review_round=int(rec.get("review_round") or 1),
                )
                rec["fan_in"] = fan_in_id
                rec["fan_in_attempts"] = int(rec.get("fan_in_attempts") or 0) + 1
                in_flight += 1
                save_state(state)
                lines.append(f"PR #{num}: fan-in {fan_in_id} created")
                continue
            fan_row = _task_row(fan_in_id)
            fan_verdict, fan_evidence = leaf_verdict(fan_row) if fan_row else (None, "")
            if fan_row and fan_row.get("status") not in {"done", "archived", "blocked", "triage"} and not fan_verdict:
                lines.append(f"PR #{num}: report-only fan-in running ({fan_in_id})")
                continue
            if fan_verdict != "APPROVED":
                if apply and fan_row and fan_row.get("status") != "archived":
                    archive_cycle_task(fan_in_id, "Fan-in did not produce exact APPROVED coverage")
                rec.pop("fan_in", None)
                if int(rec.get("fan_in_attempts") or 0) >= 2:
                    lines.append(f"PR #{num}: fan-in route failed twice; factory-owned repair required")
                else:
                    lines.append(f"PR #{num}: fan-in incomplete; same bounded reconciliation will retry")
                if apply:
                    save_state(state)
                continue
            rec["fan_in_evidence"] = fan_evidence[-5000:]

        rec["review_approved"] = True
        rec["review_candidate"] = head

        priority_blockers: list[int] = []
        if num in PRIORITY_PRS:
            priority_blockers = [
                predecessor for predecessor in PRIORITY_PRS[: PRIORITY_PRS.index(num)]
                if predecessor not in merged_priorities
            ]
        product_blockers = unresolved_predecessors(source, merged_sources)
        if priority_blockers or product_blockers:
            waiting = priority_blockers or product_blockers
            lines.append(f"PR #{num}: approved; waiting for predecessors {waiting}")
            continue
        if merges >= MAX_MERGES_PER_TICK:
            lines.append(f"PR #{num}: approved; merge deferred by tick cap")
            continue

        ci_ok, ci_note = forgejo_ci_state(head)
        if not ci_ok:
            lines.append(f"PR #{num}: approved but not mergeable yet — {ci_note}")
            continue
        fresh = api(f"/repos/{REPO}/pulls/{num}")
        if str(fresh.get("head", {}).get("sha") or "") != head:
            lines.append(f"PR #{num}: head changed during cycle; restarting on next tick")
            continue
        if fresh.get("mergeable") is False:
            if apply and not rec.get("rework_card"):
                rec["rework_card"] = create_rework_card(
                    pr, "pull request is not mergeable", "Resolve the current target-branch conflict."
                )
                save_state(state)
            lines.append(f"PR #{num}: approved but target-branch conflict requires rework")
            continue
        if not apply:
            lines.append(f"PR #{num}: WOULD merge; {ci_note}")
            continue

        rec["merge_requested_head"] = head
        save_state(state)
        ok, detail = merge_pr(fresh)
        merges += 1
        if not ok:
            lines.append(f"PR #{num}: merge unverified; reconciliation retained — {detail}")
            save_state(state)
            continue
        rec["merged"] = detail
        rec.pop("merge_requested_head", None)
        merged_priorities.add(num)
        if source is not None:
            merged_sources.add(source)
        lines.append(f"PR #{num}: MERGED {detail[:12]} and read back on {BASE_BRANCH}")
        if source is not None:
            merged_back = api(f"/repos/{REPO}/pulls/{num}")
            closed, close_note = close_source_issue(merged_back)
            lines.append(f"PR #{num}: {close_note}")
            if closed:
                rec["source_closed"] = True
        save_state(state)
        break  # target tip changed; every later candidate must be re-gated

    for note in reap(apply):
        lines.append(note)
    if apply:
        save_state(state)
    print("\n".join(lines) if lines else "no open PRs")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--reap-only", action="store_true")
    args = ap.parse_args()
    with exclusive_cycle_lock() as acquired:
        if not acquired:
            print("cycle already running; skipped")
            return 0
        if args.reap_only:
            print("\n".join(reap(args.apply)) or "nothing to reap")
            return 0
        return tick(args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
