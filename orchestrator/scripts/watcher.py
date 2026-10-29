"""The watcher cron job of the Orchestrator profile (a Hermes no_agent script; stdlib only).

Hermes runs this file with its own Python every minute. It locates the workspace environment
from the profile's environment (IDE_WORKSPACE / IDE_WORKSPACE_ENV, already loaded from the
profile .env) and runs one watcher tick there. Printing ``{"wakeAgent": false}`` as the last
line keeps Hermes quiet; a non-zero exit raises a Hermes failure notice.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def workspace_python() -> Path:
    env = os.environ
    if env.get("IDE_WORKSPACE_ENV"):
        return Path(env["IDE_WORKSPACE_ENV"]).expanduser() / "bin" / "python"
    if env.get("IDE_WORKSPACE"):
        root = Path(env["IDE_WORKSPACE"]).expanduser()
    elif env.get("HERMES_REAL_HOME"):
        root = Path(env["HERMES_REAL_HOME"]).expanduser() / "workspace"
    else:
        root = Path.home() / ".hermes" / "workspace"
    return root / "tools" / "venv" / "bin" / "python"


def main() -> int:
    python = workspace_python()
    if not python.exists():
        error = f"workspace environment missing at {python}; run scripts/prepare.py"
        print(json.dumps({"wakeAgent": False, "error": error}))
        return 1
    completed = subprocess.run(
        [str(python), "-m", "research_core.cli", "watcher", "tick"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.stderr.strip():
        print(completed.stderr.strip()[-4000:], file=sys.stderr)
    tail = completed.stdout.strip().splitlines()[-1:] if completed.stdout.strip() else []
    print(tail[0] if tail else json.dumps({"wakeAgent": False}))
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
