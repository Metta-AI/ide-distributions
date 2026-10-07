#!/usr/bin/env python3
"""Fake build, local check and upload. Stdlib only. Always prints {"ok", "data"|"error"}.

  fake_build.py <candidate-dir> --out <build-dir> --json   build: hash STRATEGY.md
  fake_build.py --check <candidate-dir> --json             STRATEGY.md exists and has content
  fake_build.py --upload <build-dir> --json                fake policy_version_id (no network)
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path


class ToolError(Exception):
    pass


def _strategy_path(candidate: Path) -> Path:
    strategy = candidate / "strategy" / "STRATEGY.md"
    if not strategy.is_file():
        raise ToolError(f"{strategy} not found")
    return strategy


def build(candidate: Path, out: Path) -> dict:
    artifact_hash = hashlib.sha256(_strategy_path(candidate).read_bytes()).hexdigest()
    out.mkdir(parents=True, exist_ok=True)
    (out / "policy.json").write_text(json.dumps({"policy": artifact_hash}, sort_keys=True) + "\n")
    # The version.json contract read by `softmax upload` (softmax_tool/upload.py).
    version = {
        "artifact_hash": artifact_hash,
        "artifact": "policy.json",
        "strategy_sha256": artifact_hash,
        "gates": {"local_check": {"passed": True}},
    }
    (out / "version.json").write_text(json.dumps(version, indent=2, sort_keys=True) + "\n")
    return version


def check(candidate: Path) -> dict:
    text = _strategy_path(candidate).read_text(encoding="utf-8")
    if not text.strip():
        raise ToolError("STRATEGY.md is empty")
    return {"strategy_bytes": len(text.encode("utf-8"))}


def upload(build_dir: Path) -> dict:
    try:
        version = json.loads((build_dir / "version.json").read_text(encoding="utf-8"))
        artifact_hash = version["artifact_hash"]
    except (OSError, ValueError, KeyError) as error:
        raise ToolError(f"{build_dir} is not a build directory: {error!r}") from error
    return {"policy_version_id": f"fake-pv-{artifact_hash[:12]}", "artifact_hash": artifact_hash}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="candidate dir (build, --check) or build dir")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if args.check:
            data = check(args.path)
        elif args.upload:
            data = upload(args.path)
        elif args.out is None:
            raise ToolError("--out is required to build")
        else:
            data = build(args.path, args.out)
    except (ToolError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    print(json.dumps({"ok": True, "data": data}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
