"""Run distribution preparation in the profile's locked environment."""

import os
import sys
from pathlib import Path

home = Path(os.environ["HERMES_HOME"])
sys.path.insert(0, str(home / "src"))
from ide_research_core.launch import ensure_venv

ensure_venv(home)
python = str(home / "local/venv/bin/python")
os.execv(python, [python, "-I", "-m", "ide_research_core.prepare", *sys.argv[1:]])
