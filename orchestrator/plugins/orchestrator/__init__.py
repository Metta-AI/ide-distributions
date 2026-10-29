"""Hermes plugin entry of the Orchestrator profile. Runs inside the Hermes process: stdlib only.

prepare.py records the release's core source path in ``local/core-path.txt``; this shim puts it
on ``sys.path`` and delegates to ``research_core``. If ``research_core`` is already importable
(installed into Hermes's interpreter), that copy is used instead.
"""

import os
import sys
from pathlib import Path

ROLE = "orchestrator"


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except ImportError:
        env_home = os.environ.get("HERMES_HOME")
        return Path(env_home) if env_home else Path(__file__).resolve().parents[2]


def _add_core_path(home: Path) -> None:
    marker = home / "local" / "core-path.txt"
    try:
        path = marker.read_text().strip()
    except OSError:
        return
    if path and path not in sys.path:
        sys.path.insert(0, path)


_add_core_path(_home())
from research_core.plugin.register import make_register  # noqa: E402

register = make_register(ROLE)

__all__ = ["register"]
