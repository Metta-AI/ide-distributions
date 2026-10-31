"""Regenerate the agent-owned application artifact, or check that it is current."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "image"))
from contract import openapi  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
path = ROOT / "contract" / "openapi.json"
expected = json.dumps(openapi(), indent=2, sort_keys=True) + "\n"
if args.check:
    assert path.read_text() == expected, (
        "Run python tools/export_contract.py and commit the artifact"
    )
else:
    path.parent.mkdir(exist_ok=True)
    path.write_text(expected)
