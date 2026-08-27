#!/usr/bin/env python3
"""Run the tracked review recovery add-on from a no-agent cron job.

Install this wrapper and ``kanban_review_successor_recovery.py`` beside each
other under ``~/.hermes/scripts``.  The board is explicit so a cron job cannot
silently operate on whichever board happens to be current.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


BOARD = os.environ.get("HERMES_FACTORY_BOARD", "").strip()
if not BOARD:
    raise SystemExit("HERMES_FACTORY_BOARD is required")

TARGET = Path(
    os.environ.get(
        "HERMES_REVIEW_SUCCESSOR_SCRIPT",
        str(Path(__file__).with_name("kanban_review_successor_recovery.py")),
    )
).expanduser()
if not TARGET.is_file():
    raise SystemExit(f"review successor recovery script not found: {TARGET}")

sys.argv = [
    str(TARGET),
    "--board",
    BOARD,
    "--apply",
    "--quiet",
]
runpy.run_path(str(TARGET), run_name="__main__")
