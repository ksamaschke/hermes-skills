#!/usr/bin/env python3
"""In-directory cron entrypoint for the repository-backed recovery script.

Hermes cron validates the resolved script path and rejects symlinks that leave
~/.hermes/scripts. Keep this shim as a regular local file; it delegates to the
canonical script in the factory-skills checkout.
"""

from __future__ import annotations

import os
import runpy
from pathlib import Path


TARGET_VALUE = os.environ.get("HERMES_FACTORY_RECOVERY_SCRIPT")
if not TARGET_VALUE:
    raise SystemExit("HERMES_FACTORY_RECOVERY_SCRIPT is required")
TARGET = Path(TARGET_VALUE).expanduser()

BOARD = os.environ.get("HERMES_FACTORY_BOARD")
if not BOARD:
    raise SystemExit("HERMES_FACTORY_BOARD is required")

if not TARGET.is_file():
    raise SystemExit(f"canonical factory recovery script not found: {TARGET}")

runpy.run_path(str(TARGET), run_name="__main__")
