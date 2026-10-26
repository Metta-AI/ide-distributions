"""Hermes plugin entry. STDLIB ONLY: this runs inside the Hermes process.

It puts `<distribution root>/src` on sys.path and registers the shared `capabilities` tool
(`ide_research_core.session_catalog`, also registered by the orchestrator) in the `researcher`
toolset.
"""

import os
import sys
from pathlib import Path


def _distribution_root() -> Path:
    # Inside the gateway, os.environ["HERMES_HOME"] is the host root, not this profile, so
    # prefer Hermes' own resolver (as the orchestrator's plugin does).
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except ImportError:
        pass
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        return Path(env_home)
    return Path(__file__).resolve().parents[2]


_src = str(_distribution_root() / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from ide_research_core import session_catalog


def register(ctx) -> None:
    session_catalog.register(ctx, "researcher")
