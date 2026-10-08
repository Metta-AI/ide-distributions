#!/usr/bin/env python3
"""Extract one subject seat's metrics from one completed episode results.json."""
import argparse
import json
import math
from pathlib import Path


def metrics(data: dict, seat: int) -> dict:
    if not isinstance(data, dict):
        raise TypeError('results.json must contain an object')
    if isinstance(seat, bool) or not isinstance(seat, int) or seat not in range(10):
        raise ValueError('seat must be an integer in 0..9')
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
    parser.add_argument('results', type=Path, nargs='?')
    parser.add_argument('--results', dest='results_flag', type=Path)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--seat', type=int, choices=range(10))
    args = parser.parse_args()
    try:
        path = args.results_flag or args.results
        if path is None or (args.results_flag and args.results):
            raise ValueError('supply exactly one results path')
        data = json.loads(path.read_text())
        if args.results_flag:
            seed = data.get('seed')
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise ValueError('hosted results must carry their real integer seed')
            values = {'ok': True, 'data': {'episodes': [
                {'episode': 0, 'seat': seat, 'seed': seed, 'status': 'completed',
                 'metrics': metrics(data, seat)} for seat in range(10)]}}
        else:
            if args.seat is None:
                raise ValueError('positional results requires --seat N')
            values = metrics(data, args.seat)
    except (OSError, TypeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(values, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
