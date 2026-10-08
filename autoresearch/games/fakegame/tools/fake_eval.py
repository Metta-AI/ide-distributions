#!/usr/bin/env python3
"""Fake evaluate: deterministic per-episode metrics from a hash of (policy, seed). Stdlib only.

``policy`` is an artifact path (policy.json from fake_build) or any id string. Each policy
gets a fixed mean shift in score (-2..+2) so a statistical test can tell policies apart.

``--results <results.json>`` is the evaluate binding's hosted-XP mode (xp-poll): it reads one
XP request's ``results.json`` (the fake platform's format: ``{seed?, episodes: [{episode,
score, errors, seed?, seat?}]}``) and prints each episode's outcome metrics. A league
episode's results (``softmax league-episodes``) have one entry per seat, with ``seat``.
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
        try:
            return json.loads(path.read_text())["policy"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(f"{policy} is not a valid policy artifact: {error!r}") from error
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


def replay(policy: str, seed: int, ticks: int = 5) -> dict:
    """Small deterministic per-tick summary of one fake episode."""
    pid = policy_id(policy)
    summary = [
        {"tick": tick, "score": round(_unit(pid, str(seed), "tick", str(tick)) * 10, 4)}
        for tick in range(ticks)
    ]
    return {"policy": pid, "seed": seed, "ticks": summary}


METRICS = ("score", "errors")


def results_metrics(path: str) -> dict:
    """Per-episode metrics of one XP request's results.json."""
    results = json.loads(Path(path).read_text())
    episodes = []
    for episode in results.get("episodes") or []:
        missing = [name for name in METRICS if name not in episode]
        if missing:
            raise ValueError(f"{path}: episode {episode.get('episode')} lacks {missing}")
        entry = {
            "episode": episode.get("episode"),
            "status": "failed" if episode.get("failed") else "completed",
            "metrics": {name: episode[name] for name in METRICS},
        }
        for key in ("seed", "seat"):
            if key in episode:
                entry[key] = episode[key]
        episodes.append(entry)
    return {"episodes": episodes}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--results", help="metrics of one XP request's results.json")
    parser.add_argument("--replay", action="store_true", help="per-tick summary, not metrics")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.results is None and (args.policy is None or args.seed is None):
        parser.error("--policy and --seed are required without --results")
    try:
        if args.results is not None:
            data = results_metrics(args.results)
        else:
            data = (replay if args.replay else evaluate)(args.policy, args.seed)
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    print(json.dumps({"ok": True, "data": data}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
