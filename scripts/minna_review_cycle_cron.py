#!/usr/bin/env python3
"""Hermes cron entrypoint for the Minna PR review cycle.

Deterministic, no LLM: stdout is the whole user-facing message, and an empty
stdout sends nothing. Runs one tick, which performs at most one state
transition per PR, then exits — no daemon, no lingering lane.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path.home() / ".hermes" / "scripts" / "minna_review_cycle.py"
# Wall-clock ceiling for the whole tick. The gate itself is capped separately in
# the config; this is the outer stop so a wedged tick can never become a zombie.
TICK_TIMEOUT = 3000


def main() -> int:
    if not SCRIPT.is_file():
        print(f"minna review cycle script missing: {SCRIPT}")
        return 1
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--apply"],
            capture_output=True, text=True, timeout=TICK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # Reap whatever the wedged tick left running, then report honestly.
        subprocess.run([sys.executable, str(SCRIPT), "--reap-only", "--apply"],
                       capture_output=True, timeout=300)
        print(f"Minna review cycle: tick exceeded {TICK_TIMEOUT}s and was killed; "
              f"running cards were reaped. Investigate before the next tick.")
        return 1

    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()

    if result.returncode != 0:
        print(f"Minna review cycle failed (exit {result.returncode}):\n{(err or out)[:1500]}")
        return result.returncode

    # Only speak when something actually changed. Steady-state ticks stay silent
    # so this never becomes another digest that cries "88 done" every 30 minutes.
    interesting = [ln for ln in out.splitlines()
                   if any(k in ln for k in ("MERGED", "gate RED", "returned", "created",
                                            "merge unverified", "reap", "REVIEW-INCOMPLETE",
                                            "factory-owned route repair"))]
    if interesting:
        print("Minna review cycle\n" + "\n".join(interesting))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
