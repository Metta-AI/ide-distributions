#!/usr/bin/env python3
"""Extract one subject seat's metrics from one completed episode results.json."""
import argparse
import json
import math
from pathlib import Path


def metrics(data: dict, seat: int) -> dict:
    if not isinstance(data, dict):
        raise TypeError('results.json must contain an object')
    ticks = data.get('ticks')
    if isinstance(ticks, bool) or not isinstance(ticks, int) or ticks <= 0:
        raise ValueError('ticks must be a positive integer')
    # Native Polyworld 5287c437 sim.nim:3853-3859 outcome values.
    outcome = data.get('outcome')
    if outcome not in ('RedTeam', 'BlueTeam', 'draw', 'time_limit'):
        raise ValueError('outcome must be RedTeam, BlueTeam, draw, or time_limit')
    for field in ('scores', 'total_xp'):
        values = data.get(field)
        if not isinstance(values, list) or len(values) != 10:
            raise ValueError(f'{field} must contain all ten seat values')
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or value < 0 for value in values):
            raise ValueError(f'{field} values must be finite nonnegative numbers')
    won = outcome == ('RedTeam' if seat < 5 else 'BlueTeam')
    if any(score != 0 for index, score in enumerate(data['scores'])
           if outcome != ('RedTeam' if index < 5 else 'BlueTeam')):
        raise ValueError('scores must be zero for losses, draws, and timeouts')
    xp = data['total_xp']
    teammates = range(5) if seat < 5 else range(5, 10)
    return {'glory': data['scores'][seat], 'win_rate': int(won),
            'xp_per_1k_ticks': xp[seat] * 1000 / ticks,
            'team_xp_lead': int(all(xp[seat] > xp[other] for other in teammates if other != seat))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--seat', required=True, type=int, choices=range(10))
    args = parser.parse_args()
    try:
        values = metrics(json.loads(args.results.read_text()), args.seat)
    except (OSError, TypeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(values, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
