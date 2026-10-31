"""Prepare this profile after an install or an update (run by the host with Hermes's Python).

Usage: prepare.py --source <distribution folder> --hermes <hermes exe> [--activate-supervisor]

It loads research_core from <source>/../core/src, the release checkout, so an agent's edits can
never stop preparation. ``--activate-supervisor`` is the host's name for "enable the watcher job
once" and is kept for compatibility with the hosting contract.
"""

import argparse
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument(
    "--source", required=True, type=Path, help="This distribution's folder in the release checkout"
)
parser.add_argument("--hermes", required=True, help="The Hermes executable")
parser.add_argument(
    "--activate-supervisor", action="store_true", help="Enable the watcher job on first preparation"
)
parser.add_argument("--check", action="store_true", help="Report what would change; write nothing")
args = parser.parse_args()

source = args.source.resolve()
sys.path.insert(0, str(source.parent / "core" / "src"))
from research_core.prepare import HermesConfig, prepare  # noqa: E402

prepare(
    source=source,
    cli=HermesConfig(args.hermes),
    activate_watcher=args.activate_supervisor,
    check=args.check,
)
