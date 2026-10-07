#!/usr/bin/env python3
"""Fake evaluate: deterministic per-episode metrics from a hash of (policy, seed). Stdlib only.

``policy`` is an artifact path (policy.json from fake_build) or any id string. Each policy
gets a fixed mean shift in score (-2..+2) so a statistical test can tell policies apart.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _unit(*parts: str) -> float:
    """Deterministic float in [0, 1) from the parts."""
    digest = hashlib.sha256("|".join(parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def policy_id(policy: str) -> str:
    path = Path(policy)
    if path.is_file():
        return json.loads(path.read_text()).get("policy", policy)
    return policy


def evaluate(policy: str, seed: int) -> dict:
    pid = policy_id(policy)
    shift = (_unit(pid, "shift") - 0.5) * 4
    score = 50 + shift + (_unit(pid, str(seed), "score") - 0.5) * 10
    errors = int(_unit(pid, str(seed), "errors") * 5)
    return {
        "policy": pid,
        "seed": seed,
        "metrics": {"score": round(score, 4), "errors": errors},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    print(json.dumps({"ok": True, "data": evaluate(args.policy, args.seed)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
