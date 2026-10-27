#!/usr/bin/env python3
"""Score the options a seat had at chosen ticks of a recorded match (counterfactual rollouts).

Runs `pudge_headless --branch`: the replay is re-simulated to each tick in --ticks, the world
is cloned, the traced seat (--slot) executes one option (hook_k, attack_k, rot, retreat,
reposition, none, ... whatever was eligible, or --options), every other seat is driven by
--bot (default the base starter), and after --horizon ticks (default 160 = 5 s) the seat's
and both teams' deltas are reported. Rollouts are deterministic for a given tape, slot,
option and seed; --seeds N > 1 reseeds the world stream for extra rollouts per option.

The engine is the replay's own, chosen as replay_inspect.py does (--commit, --coworld-id,
episode metadata beside the tape, else live).

Caveat (examples/pudge_wars/branch.nim:1-14@cd8111a5): the continuation is bots, not the
players who were there, so the ranking is evidence about this state, not proof of what the
real opponents would have done.

Per tick, rows are ranked by net kill swing (team_kills - enemy_kills over the horizon),
then fewer deaths, then staying alive with more HP. `best` lists the options tied at the top.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_contract
import episodes
import replay_inspect
from cli_contract import Rejected


def rank_key(row: dict) -> tuple:
    alive_hp = row['hp_after'] if row['alive_after'] else -1
    return (row['team_kills'] - row['enemy_kills'], -row['deaths'], alive_hp)


def summarise(rows: list[dict]) -> list[dict]:
    windows: dict[int, list[dict]] = {}
    for row in rows:
        if 'option' in row:
            windows.setdefault(row['tick'], []).append(row)
    out = []
    for tick, options in sorted(windows.items()):
        ranked = sorted(options, key=rank_key, reverse=True)
        top = rank_key(ranked[0])
        baseline = next((row for row in options if row['option'] == 'none'), None)
        out.append({'tick': tick, 'best': sorted({row['option'] for row in ranked if rank_key(row) == top}),
                    'options': [{'option': row['option'], 'seed': row.get('seed'),
                                 'net_kills': row['team_kills'] - row['enemy_kills'],
                                 'deaths': row['deaths'], 'alive_after': row['alive_after'],
                                 'hp_after': row['hp_after'],
                                 'net_vs_none': (None if baseline is None else
                                                 (row['team_kills'] - row['enemy_kills'])
                                                 - (baseline['team_kills'] - baseline['enemy_kills']))}
                                for row in ranked]})
    return out


def parse_ticks(text: str) -> list[int]:
    try:
        ticks = [int(part) for part in text.split(',') if part.strip()]
    except ValueError:
        raise Rejected('--ticks must be comma-separated integers') from None
    if not ticks or any(tick < 0 for tick in ticks):
        raise Rejected('--ticks must list nonnegative ticks')
    return sorted(set(ticks))


def main(argv=None) -> int:
    parser = cli_contract.ArgumentParser(
        __doc__, prog='branch.py',
        examples=['python3 games/pudge-wars-basic/tools/branch.py EPISODE_DIR --slot 0 --ticks 2000,4000 --json'])
    parser.add_argument('tape', type=Path, help='replay file (raw or gzip) or a directory holding one')
    parser.add_argument('--slot', type=int, required=True, help='traced seat (0-based)')
    parser.add_argument('--ticks', required=True, help='comma-separated window ticks')
    parser.add_argument('--options', help='comma-separated option ids (default: every eligible one plus none)')
    parser.add_argument('--horizon', type=int, help='rollout length in ticks (default 160)')
    parser.add_argument('--seeds', type=int, help='rollouts per option (default 1)')
    parser.add_argument('--bot', default='base', help='policy for the other seats after the branch (default base)')
    parser.add_argument('--teachers', help='comma-separated policies asked what they would do at each window')
    parser.add_argument('--out', type=Path, help='also keep the raw JSONL rows here (new file)')
    parser.add_argument('--coworld-id', help="the episode's Coworld id (its engine runs the rollouts)")
    parser.add_argument('--commit', help='run the rollouts with this engine commit')
    parser.add_argument('--binary', type=Path, help='run the rollouts with this pudge_headless')

    def body(args):
        tape = replay_inspect.find_replay(args.tape)
        data, gzipped = replay_inspect.raw_bytes(tape)
        replay_inspect.header(data)
        ticks = parse_ticks(args.ticks)
        if args.slot < 0 or (args.horizon is not None and args.horizon < 1) or (args.seeds is not None and args.seeds < 1):
            raise Rejected('--slot must be >= 0; --horizon and --seeds must be positive')
        if args.out is not None and args.out.exists():
            raise Rejected(f'--out already exists: {args.out}')
        bot = episodes.resolve_policy(args.bot)
        teachers = [episodes.resolve_policy(name.strip()) for name in (args.teachers or '').split(',') if name.strip()]
        binary, decode_engine = replay_inspect.replay_engine(tape, data, binary=args.binary, commit=args.commit,
                                                             coworld_id=args.coworld_id)
        with tempfile.TemporaryDirectory(prefix='pudge-branch-') as scratch:
            raw = Path(scratch) / 'tape.pwr'
            raw.write_bytes(data)
            rows_path = Path(scratch) / 'rows.jsonl'
            command = [str(binary), '--branch', str(raw), '--branch-ticks', ','.join(map(str, ticks)),
                       '--branch-slot', str(args.slot), '--branch-bot', str(bot), '--branch-out', str(rows_path)]
            if args.options:
                command += ['--branch-options', args.options]
            if args.horizon:
                command += ['--horizon', str(args.horizon)]
            if args.seeds:
                command += ['--branch-seeds', str(args.seeds)]
            if teachers:
                command += ['--branch-teachers', ','.join(map(str, teachers))]
            completed = episodes.run_binary(command, cwd=Path(scratch), timeout=1800)
            if completed.returncode != 0:
                raise Rejected(f'pudge_headless --branch refused the request: '
                               f'{(completed.stderr.strip() or completed.stdout.strip())[-800:]}',
                               reason='branch_refused')
            text = rows_path.read_text() if rows_path.is_file() else ''
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text)
        return {'tape': str(tape.resolve()), 'gzip': gzipped, 'slot': args.slot, 'ticks': ticks,
                'bot': bot, 'binary': binary, 'decode_engine': decode_engine, 'windows': summarise(rows),
                'teachers': [row for row in rows if 'option' not in row], 'rows_out': args.out}

    return cli_contract.run(parser, body, argv)


if __name__ == '__main__':
    raise SystemExit(main())
