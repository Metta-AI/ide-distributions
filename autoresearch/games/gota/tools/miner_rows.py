#!/usr/bin/env python3
# Source: Metta-AI/personal_labs @ 07dc6f94760fae3cf2de36877e0b6186cfd1dc63; path: gods_of_the_arena_lab/tools/miner_rows.py
"""Build the JSONL rows the `coworld-hypothesis-miner` engine mines for Gods of the Arena.

One row per (episode, own seat) of one policy version, read from downloaded experience
requests with `gota_episodes.py`. The miner then asks: what do this policy's high-scoring
seats do that its low-scoring seats do not? `features.py` is the matching adapter.

Score choice (`--score`) decides the question and which features are excluded as
arithmetic components of the score (25 XP per last hit, 150 per hero kill, 100 per
building kill, so XP-derived scores must not be "explained" by those counts):

  xp          the seat's total XP (default; the league's objective is a time-penalized XP)
  xp_rate     total XP per 1,000 ticks, for batches whose game lengths vary widely
  last_hits   the seat's footman last hits (the current module's objective)
  win         the seat's team won (0/1); a team outcome, confounded when the policy
              holds seats on both teams

Seats are class-fixed, so mine one class or one role tier at a time (`--class`, `--role`)
when the corpus is large enough; a mixed corpus attributes class identity, not behavior.

Usage:
  uv run python gods_of_the_arena_lab/tools/miner_rows.py EPISODES_DIR [...] \
      --policy james-botts-gota:v1 --score xp --out /tmp/mine/rows.jsonl
  MINER=.claude/skills/coworld-hypothesis-miner/scripts
  uv run python $MINER/mine_hypotheses.py --rows /tmp/mine/rows.jsonl \
      --adapter gods_of_the_arena_lab/tools/features.py --top 5
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gota_episodes import (
    CLASS_NAMES,
    ROLE_NAMES,
    ROLE_OF_CLASS,
    load_batch,
    own_seats,
    parse_spec,
)

SCORES = ("xp", "xp_rate", "last_hits", "win")


def seat_row(seat, record, score_kind: str) -> dict | None:
    """Flatten one seat into a miner row; None when the score is not computable."""
    telemetry = seat.telemetry
    combat = seat.combat or telemetry
    if score_kind == "xp":
        score = seat.total_xp
    elif score_kind == "xp_rate":
        score = None if seat.total_xp is None or not record.ticks else 1000.0 * seat.total_xp / record.ticks
    elif score_kind == "last_hits":
        score = None if combat is None else combat.last_hits
    else:
        score = float(seat.won)
    if score is None:
        return None
    row = {
        "episode_id": record.episode_id, "position": seat.position,
        "hero_class": seat.hero_class, "role": ROLE_OF_CLASS[seat.hero_class], "team": seat.team,
        "score_kind": score_kind, "score": float(score),
        "ticks": record.ticks, "won": seat.won, "draw": record.draw,
        "total_xp": seat.total_xp, "level": seat.level, "vm_error": seat.vm_error,
        "telemetry": None if telemetry is None else asdict(telemetry),
        "combat": None if seat.combat is None else asdict(seat.combat),
    }
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("roots", nargs="+", type=Path, help="Downloaded episode directories.")
    parser.add_argument("--policy", required=True, help="Policy as NAME:vN (exact version).")
    parser.add_argument("--score", choices=SCORES, default="xp")
    parser.add_argument("--class", dest="classes", action="append", choices=CLASS_NAMES,
                        help="Keep only these hero classes (repeatable).")
    parser.add_argument("--role", dest="roles", action="append", choices=ROLE_NAMES,
                        help="Keep only these role tiers (repeatable).")
    parser.add_argument("--out", type=Path, required=True, help="JSONL output path.")
    parser.add_argument("--replay-stats", type=Path, help="Verified replay_stats.py JSON; exact combat counts override LH estimates.")
    args = parser.parse_args()

    name, version = parse_spec(args.policy)
    if version is None:
        parser.error("--policy needs an exact version: name:vN")
    rows: list[dict] = []
    dropped: Counter = Counter()
    seen: set[str] = set()
    for root in args.roots:
        records, excluded = load_batch(root, args.replay_stats)
        dropped.update(excluded)
        for record in records:
            if record.episode_id in seen:
                raise SystemExit(f"episode {record.episode_id} appears under two roots")
            seen.add(record.episode_id)
            if record.ops_fail or not record.known:
                dropped["failed_or_unknown_episode"] += 1
                continue
            for seat in own_seats(record, name, version):
                if args.replay_stats is not None and seat.combat is None:
                    dropped["seat_missing_exact_replay"] += 1
                    continue
                if args.classes and seat.hero_class not in args.classes:
                    continue
                if args.roles and ROLE_OF_CLASS[seat.hero_class] not in args.roles:
                    continue
                row = seat_row(seat, record, args.score)
                if row is None:
                    dropped["score_not_computable"] += 1
                    continue
                rows.append(row)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with_telemetry = sum(1 for row in rows if row["telemetry"])
    print(f"wrote {len(rows)} rows ({with_telemetry} with LH telemetry) from {len(seen)} episodes "
          f"to {args.out}; dropped {dict(dropped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
