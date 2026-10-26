#!/usr/bin/env python3
"""Run local Pudge Wars Basic episodes on the headless binary and aggregate per-seat metrics.

Seats: each --seat SPEC is PATH[:COUNT] (a .bas file or a ZIP package) or a starter name
(base, aggressive, sniper, neural from games/pudge-wars-basic/baselines), with an optional
:COUNT. Specs fill slots in order; the first half of the slots is west, the rest east. A
league-3v3 episode needs six seats, a duel-1v1 two.

Defaults come from the league's Coworld manifest variant (seed 2026; 3v3: 38,400 ticks,
first to 30; duel: 15,360 ticks, first to 10). Hosted episodes get a platform-injected seed
instead, so sweep --seeds for evidence. Each episode goes to OUT/seed-<seed>/ (see
episodes.py for the files; results.json uses the hosted schema). Metrics are evaluate.py's,
averaged over completed episodes per seat and per distinct policy.

Exit 1 when a seat's policy fails to load (compile or package error) in any episode;
runtime VM failures are reported per seat (`vm_failed`) and do not fail the run.
"""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_contract
import episodes
import evaluate
import native
from cli_contract import Failed, Rejected


def binary_for(args) -> tuple[Path, dict | None]:
    if getattr(args, 'binary', None):
        if not args.binary.is_file():
            raise Rejected(f'--binary does not exist: {args.binary}')
        return args.binary.resolve(), native.read_receipt(args.binary.resolve())
    return native.ensure(getattr(args, 'commit', None))


def mean_metrics(rows: list[dict]) -> dict:
    return {name: statistics.fmean(row[name] for row in rows) for name in evaluate.METRICS} if rows else {}


def aggregate(policies: list[Path], records: list[dict], out: Path) -> dict:
    """Per-seat and per-policy metric means over completed episodes, plus VM failure counts."""
    per_seat: dict[int, list[dict]] = {slot: [] for slot in range(len(policies))}
    for record in records:
        if record['status'] != 'completed':
            continue
        results = (out / record['dir'] / 'results.json')
        data = json.loads(results.read_text())
        for slot, rows in per_seat.items():
            rows.append(evaluate.metrics(data, slot))
    vm_failures = {slot: sum(1 for record in records if record['status'] == 'completed'
                             and record['seats'][slot]['vm_failed']) for slot in per_seat}
    by_policy: dict[str, list[dict]] = {}
    for slot, rows in per_seat.items():
        by_policy.setdefault(str(policies[slot]), []).extend(rows)
    return {'per_seat': [{'slot': slot, 'policy': str(policies[slot]), 'episodes': len(rows),
                          'vm_failures': vm_failures[slot], 'metrics': mean_metrics(rows)}
                         for slot, rows in per_seat.items()],
            'per_policy': [{'policy': policy, 'seat_episodes': len(rows), 'metrics': mean_metrics(rows)}
                           for policy, rows in by_policy.items()]}


def run_matches(binary: Path, receipt: dict | None, policies: list[Path], seeds: list[int], *,
                variant: str, max_ticks: int, kill_goal: int, out: Path, jobs: int,
                timeout: int = 900) -> list[dict]:
    def one(seed):
        record = episodes.run_episode(binary, policies, seed=seed, max_ticks=max_ticks,
                                      kill_goal=kill_goal, out=out / f'seed-{seed}', variant=variant,
                                      timeout=timeout, binary_receipt=receipt)
        return {'dir': f'seed-{seed}', **record}

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return list(pool.map(one, seeds))


def episode_row(record: dict, out: Path) -> dict:
    row = {'seed': record['seed'], 'dir': str(out / record['dir']), 'status': record['status']}
    if record['status'] == 'completed':
        data = json.loads((out / record['dir'] / 'results.json').read_text())
        row.update({key: data[key] for key in ('winner_team', 'end_reason', 'end_tick',
                                               'west_kills', 'east_kills')},
                   vm_failed_slots=[seat['slot'] for seat in record['seats'] if seat['vm_failed']],
                   state_hash=record['state_hash'], wall_seconds=record['wall_seconds'])
    else:
        row.update(failed_slots=record['failed_slots'], errors=record['errors'])
    return row


def main(argv=None) -> int:
    parser = cli_contract.ArgumentParser(
        __doc__, prog='match.py',
        examples=['python3 games/pudge-wars-basic/tools/match.py --seat cand.bas:3 --seat base:3 --seeds 1..10 --json',
                  'python3 games/pudge-wars-basic/tools/match.py --variant duel-1v1 --seat cand.bas --seat aggressive --seed 7'])
    parser.add_argument('--seat', action='append', required=True, metavar='SPEC',
                        help='PATH[:COUNT] or base|aggressive|sniper|neural[:COUNT], in slot order')
    parser.add_argument('--variant', default='league-3v3', help='league-3v3 (default) or duel-1v1')
    parser.add_argument('--seed', type=int, help='one map seed (default: the variant seed, 2026)')
    parser.add_argument('--seeds', metavar='A..B', help='inclusive seed range, one episode per seed')
    parser.add_argument('--ticks', type=int, help='episode tick limit (default: the variant max_ticks)')
    parser.add_argument('--kill-goal', type=int, help='team kills that end the episode (default: the variant)')
    parser.add_argument('--out', type=Path, help='new output directory (default: a new temporary directory)')
    parser.add_argument('--jobs', type=int, default=2, help='episodes run in parallel (default 2)')
    parser.add_argument('--timeout', type=int, default=900, help='seconds per episode (default 900)')
    parser.add_argument('--binary', type=Path, help='use this pudge_headless instead of native.py path')
    parser.add_argument('--commit', help='engine commit for native.py (default: tools/engine.env)')

    def body(args):
        config = episodes.variant_config(args.variant)
        policies = episodes.parse_seats(args.seat, config['seats'])
        seeds = episodes.parse_seeds(args.seed, args.seeds, config['seed'])
        for name, value in (('--ticks', args.ticks), ('--kill-goal', args.kill_goal), ('--jobs', args.jobs)):
            if value is not None and value < 1:
                raise Rejected(f'{name} must be positive')
        if args.out is not None and args.out.exists():
            raise Rejected(f'--out already exists: {args.out}')
        out = (args.out or Path(tempfile.mkdtemp(prefix='pudge-match-'))).resolve()
        out.mkdir(parents=True, exist_ok=True)
        binary, receipt = binary_for(args)
        records = run_matches(binary, receipt, policies, seeds, variant=args.variant,
                              max_ticks=args.ticks or config['max_ticks'],
                              kill_goal=args.kill_goal or config['kill_goal'], out=out,
                              jobs=args.jobs, timeout=args.timeout)
        data = {'out': out, 'variant': args.variant, 'binary': binary,
                'max_ticks': args.ticks or config['max_ticks'],
                'kill_goal': args.kill_goal or config['kill_goal'], 'seeds': seeds,
                'seats': [{'slot': slot, 'team': 'west' if slot < len(policies) // 2 else 'east',
                           'policy': policy, 'sha256': episodes.sha256(policy)}
                          for slot, policy in enumerate(policies)],
                'episodes': [episode_row(record, out) for record in records],
                **aggregate(policies, records, out)}
        if any(record['status'] != 'completed' for record in records):
            raise Failed('a seat policy failed to load; see episodes[].errors',
                         reason='policy_load_failed', data=data)
        return data

    return cli_contract.run(parser, body, argv)


if __name__ == '__main__':
    raise SystemExit(main())
