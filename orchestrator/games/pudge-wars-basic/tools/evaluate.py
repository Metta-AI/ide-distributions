#!/usr/bin/env python3
"""Per-seat outcome metrics from one Pudge Wars Basic results.json (hosted or local).

  evaluate.py --results RESULTS --json   the evaluate binding: {"ok": true, "data": {"episodes":
                                         [{episode, seat, seed, status, metrics}, ...]}}, one
                                         entry per seat, opponents included
  evaluate.py RESULTS --seat N           one seat's metrics object

Metrics (one value per seat):
  win               1 if the seat's team won, 0 if it lost, 0.5 for a draw (winner_team "draw")
  team_kill_margin  own team kills minus enemy team kills (west_kills / east_kills)
  kills, deaths     the seat's per_seat counters
  hook_landed_rate  hooks_landed / hooks_thrown; 0.0 when the seat threw no hook (it landed
                    none). Caveat: the host's hooks_landed also counts latching an ally or an
                    allied Hooktrap mine (sim.nim:4371-4373, 4458-4460 @cd8111a5), so this is
                    not enemy accuracy; replay_inspect.py's enemy_hook_accuracy is.

The file is validated against the game's results schema and the host's own formulas
(examples/pudge_wars/coworld_host.nim:1059-1100@cd8111a5) before any metric is computed;
a missing or inconsistent field is an error, never a guessed zero. Hosted mode requires the
real integer `seed` (the platform injects a per-episode seed).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_contract
from cli_contract import Rejected

TEAMS = ('west', 'east')
# coworld_host.nim:1080-1082@cd8111a5: "kill_goal" iff world.gameOver, else "time_limit".
END_REASONS = ('kill_goal', 'time_limit')
WINNERS = ('west', 'east', 'draw')
SEAT_COUNTS = (2, 4, 6, 8, 10)
SEAT_FIELDS = ('slot', 'team', 'kills', 'deaths', 'hooks_thrown', 'hooks_landed')
METRICS = ('win', 'team_kill_margin', 'kills', 'deaths', 'hook_landed_rate')


def _count(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{name} must be a nonnegative integer')
    return value


def validate(data) -> None:
    """Raise ValueError naming the first field that breaks the schema or the host's formulas."""
    if not isinstance(data, dict):
        raise TypeError('results.json must contain an object')
    scores = data.get('scores')
    if not isinstance(scores, list) or len(scores) not in SEAT_COUNTS:
        raise ValueError('scores must list one value per seat (2, 4, 6, 8 or 10 seats)')
    if any(isinstance(score, bool) or not isinstance(score, (int, float))
           or not math.isfinite(score) for score in scores):
        raise ValueError('scores must be finite numbers')
    seats = len(scores)
    kills = {team: _count(data.get(f'{team}_kills'), f'{team}_kills') for team in TEAMS}
    if _count(data.get('end_tick'), 'end_tick') == 0:
        raise ValueError('end_tick must be positive')
    kill_goal = data.get('kill_goal')
    if isinstance(kill_goal, bool) or not isinstance(kill_goal, int) or kill_goal < 1:
        raise ValueError('kill_goal must be a positive integer')
    reason = data.get('end_reason')
    if reason not in END_REASONS:
        raise ValueError(f'end_reason must be one of {", ".join(END_REASONS)}')
    winner = data.get('winner_team')
    if winner not in WINNERS:
        raise ValueError(f'winner_team must be one of {", ".join(WINNERS)}')
    if reason == 'kill_goal':
        if winner == 'draw' or kills[winner] < kill_goal:
            raise ValueError('winner_team must be a team that reached kill_goal when end_reason is kill_goal')
    else:
        if max(kills.values()) >= kill_goal:
            raise ValueError('end_reason time_limit is inconsistent with a team at kill_goal')
        expected = 'draw' if kills['west'] == kills['east'] else max(TEAMS, key=kills.get)
        if winner != expected:
            raise ValueError('winner_team must be the team ahead in kills at the time limit')
    per_seat = data.get('per_seat')
    if not isinstance(per_seat, list) or len(per_seat) != seats:
        raise ValueError('per_seat must hold one object per seat')
    team_kills = [0, 0]
    for slot, row in enumerate(per_seat):
        if not isinstance(row, dict) or set(row) != set(SEAT_FIELDS):
            raise ValueError(f'per_seat[{slot}] must have exactly {", ".join(SEAT_FIELDS)}')
        for name in SEAT_FIELDS:
            _count(row[name], f'per_seat[{slot}].{name}')
        # Contiguous banks: the first half of the slots is west (team 0).
        if row['slot'] != slot or row['team'] != (0 if slot < seats // 2 else 1):
            raise ValueError(f'per_seat[{slot}] slot/team must be {slot}/{0 if slot < seats // 2 else 1}')
        team_kills[row['team']] += row['kills']
        mine, theirs = kills[TEAMS[row['team']]], kills[TEAMS[1 - row['team']]]
        # coworld_host.nim:1066-1071@cd8111a5: 1.0 iff that bank has strictly more kills.
        if scores[slot] != (1.0 if mine > theirs else 0.0):
            raise ValueError(f'scores[{slot}] must be 1 when its team leads in kills, else 0')
    for team, personal in zip(TEAMS, team_kills):
        # Rot suicides credit the other team with no personal kill (sim.nim:1755-1759@cd8111a5).
        if personal > kills[team]:
            raise ValueError(f'per_seat kills of {team} exceed {team}_kills')
    if 'seed' in data and (isinstance(data['seed'], bool) or not isinstance(data['seed'], int)):
        raise ValueError('seed must be an integer')


def metrics(data: dict, seat: int) -> dict:
    """One seat's outcome metrics; raises ValueError/TypeError on invalid input."""
    validate(data)
    if isinstance(seat, bool) or not isinstance(seat, int) or seat not in range(len(data['scores'])):
        raise ValueError(f'seat must be an integer in 0..{len(data["scores"]) - 1}')
    row = data['per_seat'][seat]
    team, enemy = TEAMS[row['team']], TEAMS[1 - row['team']]
    winner = data['winner_team']
    return {'win': 0.5 if winner == 'draw' else float(winner == team),
            'team_kill_margin': data[f'{team}_kills'] - data[f'{enemy}_kills'],
            'kills': row['kills'], 'deaths': row['deaths'],
            'hook_landed_rate': (row['hooks_landed'] / row['hooks_thrown']
                                 if row['hooks_thrown'] else 0.0)}


def episodes(data: dict) -> list[dict]:
    """The hosted envelope's entries: every seat of the one episode in this file."""
    validate(data)
    seed = data.get('seed')
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError('hosted results must carry their real integer seed')  # noqa: TRY004 - a field value, reported like the others
    return [{'episode': 0, 'seat': seat, 'seed': seed, 'status': 'completed',
             'metrics': metrics(data, seat)} for seat in range(len(data['scores']))]


def main(argv=None) -> int:
    parser = cli_contract.ArgumentParser(
        __doc__, prog='evaluate.py',
        examples=['python3 games/pudge-wars-basic/tools/evaluate.py --results results.json --json',
                  'python3 games/pudge-wars-basic/tools/evaluate.py results.json --seat 3'])
    parser.add_argument('results', type=Path, nargs='?', help='results.json (local mode, with --seat)')
    parser.add_argument('--results', dest='results_flag', type=Path,
                        help='results.json (hosted mode: every seat, contract envelope)')
    parser.add_argument('--seat', type=int, help='seat (slot) to report in local mode')

    def body(args):
        path = args.results_flag or args.results
        if path is None or (args.results_flag and args.results):
            raise Rejected('supply exactly one results path')
        try:
            data = json.loads(path.read_text())
            if args.results_flag:
                return {'episodes': episodes(data)}
            if args.seat is None:
                raise Rejected('positional results requires --seat N')
            return metrics(data, args.seat)
        except (OSError, TypeError, ValueError) as error:
            raise Rejected(str(error), reason='invalid_results') from None

    argv = list(sys.argv[1:] if argv is None else argv)
    if '--results' in argv or '--json' in argv:
        return cli_contract.run(parser, body, argv)
    # Local mode prints the bare metrics object (no envelope), for shell use.
    try:
        args = parser.parse_args(argv)
        print(json.dumps(body(args), allow_nan=False))
        return 0
    except cli_contract.ToolError as error:
        print(f'evaluate.py: {error.reason}: {error}', file=sys.stderr)
        return error.exit_code


if __name__ == '__main__':
    raise SystemExit(main())
