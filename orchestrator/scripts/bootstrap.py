"""Prepare the orchestrator: sync its venv, clone the canonical workspace (a researcher's
bootstrap creates and seeds it first; the orchestrator ships no game seed), write the
``bin/research`` CLI shim and ``RESEARCH_CLI``. Idempotent. Run once after
``hermes profile install`` and after each update:

    HERMES_HOME=<profile dir> python3 <profile dir>/scripts/bootstrap.py
"""

import os
import sys
from pathlib import Path

home = Path(os.environ.get("HERMES_HOME") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(home / "src"))

from ide_research_core.launch import main

raise SystemExit(main(["workspace", "bootstrap"]))
