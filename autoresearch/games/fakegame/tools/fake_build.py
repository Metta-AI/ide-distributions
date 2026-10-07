#!/usr/bin/env python3
"""Fake build: hash the candidate STRATEGY.md into an artifact and version.json. Stdlib only."""

import argparse
import hashlib
import json
import sys
from pathlib import Path


def build(candidate: Path, out: Path) -> dict:
    strategy = candidate / "strategy" / "STRATEGY.md"
    if not strategy.exists():
        raise FileNotFoundError(f"{strategy} not found")
    artifact_hash = hashlib.sha256(strategy.read_bytes()).hexdigest()
    out.mkdir(parents=True, exist_ok=True)
    artifact = out / "policy.json"
    artifact.write_text(json.dumps({"policy": artifact_hash}, sort_keys=True) + "\n")
    version = {"artifact_hash": artifact_hash, "artifact": str(artifact)}
    (out / "version.json").write_text(json.dumps(version, indent=2, sort_keys=True) + "\n")
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        data = build(args.candidate, args.out)
    except OSError as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    print(json.dumps({"ok": True, "data": data}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
