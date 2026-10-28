"""Prepare this profile: sync its venv, create or clone its canonical workspace, write the
``bin/research`` CLI shim and ``RESEARCH_CLI``. Idempotent. Hosting's prepare step and a
manual install run it once after ``hermes profile install``:

    HERMES_HOME=<profile dir> python3 <profile dir>/scripts/bootstrap.py
"""

import os
import sys
from pathlib import Path

home = Path(os.environ.get("HERMES_HOME") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(home / "src"))

from ide_research_core.launch import main

raise SystemExit(main(["workspace", "bootstrap"]))
