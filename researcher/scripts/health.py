import os
import sys
from pathlib import Path

home = Path(os.environ.get("HERMES_HOME") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(home / "src"))

from autoresearch.launch import main

raise SystemExit(main("health"))
