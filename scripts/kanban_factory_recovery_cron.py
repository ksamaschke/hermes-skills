#!/usr/bin/env python3
"""In-directory cron entrypoint for the repository-backed recovery script.

Hermes cron validates the resolved script path and rejects symlinks that leave
~/.hermes/scripts. Keep this shim as a regular local file; it delegates to the
canonical script in the hermes-agent-skills checkout.
"""

from __future__ import annotations

import os
import runpy
from pathlib import Path


TARGET = Path(
    os.environ.get(
        "HERMES_FACTORY_RECOVERY_SCRIPT",
        str(
            Path.home()
            / "Work/Development/Samaschke/hermes-agent-skills/scripts/kanban_factory_recovery.py"
        ),
    )
).expanduser()

# This shim is the entrypoint for the Minna factory job. Hermes no-agent script
# execution supplies neither CLI arguments nor per-job environment variables;
# preserve an explicit override but make the job's board unambiguous by default.
if not os.environ.get("HERMES_FACTORY_BOARD"):
    os.environ["HERMES_FACTORY_BOARD"] = "minna"

if not TARGET.is_file():
    raise SystemExit(f"canonical factory recovery script not found: {TARGET}")

runpy.run_path(str(TARGET), run_name="__main__")
