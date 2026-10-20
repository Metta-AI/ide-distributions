"""Prepare the play profile: sync its venv from uv.lock and create the program workspace.
Idempotent. Run once after ``hermes profile install`` and after each update:

    HERMES_HOME=<profile dir> PLAY_WORKSPACE=<dir> python3 <profile dir>/scripts/bootstrap.py
"""

import os
import sys
from pathlib import Path

home = Path(os.environ.get("HERMES_HOME") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(home / "src"))

from play.launch import LaunchError, ensure_venv

workspace = os.environ.get("PLAY_WORKSPACE")
if not workspace or not Path(workspace).is_absolute():
    raise SystemExit("PLAY_WORKSPACE must be an absolute path")
Path(workspace).mkdir(parents=True, exist_ok=True)
try:
    ensure_venv(home)
except LaunchError as error:
    raise SystemExit(f"play bootstrap: {error}") from None
