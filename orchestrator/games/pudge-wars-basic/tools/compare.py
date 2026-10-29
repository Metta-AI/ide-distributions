#!/usr/bin/env python3
"""A/B diagnostics for Pudge Wars Basic over two sets of episode results (local or downloaded).

BASE_DIR and CAND_DIR are searched recursively for results files (any *.json holding the
hosted results schema: `match.py` episode dirs, `softmax xp artifacts` downloads, or saved
`coworld episode-results` files). Each file is one episode, validated by evaluate.py; an
invalid one is excluded and counted, never guessed.

Unit: one observation per episode per group; the subject seats of a group in one episode
are averaged first, so a game is never counted twice. --seats names the subject seats
(default: every seat; give the design's own seats, for example 0,1,2, when the opponents
are other policies). --group: `team` (west/east, default), `seat` (each subject seat), or
`all`. Metrics: evaluate.py's win, team_kill_margin, kills, deaths, hook_landed_rate per
seat group, plus per-episode end_tick, kill_goal_rate and draw_rate (--metric limits them).

Statistics come from ab_stats.py (copied unchanged from GotA): independent samples, Welch
tests for means, Fisher for rates, Benjamini-Yekutieli correction, at least 30
observations per side before any verdict. It is a diagnostic: the decision belongs to the
shared paired statistics (`stats evaluate`) and, in the end, to league episodes.

Noise floor (--replicate DIR, repeatable): each DIR is a replicate baseline arm, a
behavior-neutral variant of the baseline (one threshold moved by its smallest step) run on
the same line-ups and seeds as BASE_DIR. Any change to a policy's actions changes what every
seat observes, so the match diverges from the baseline's despite the same seed, and replicate
arms differ from BASE_DIR by the arm-level noise of that line-up set. Each delta then carries `difference` (candidate minus baseline, in the metric's
units), `replicate_differences` (each replicate minus baseline) and `noise_floor` (the largest
of their magnitudes), and `difference_vs_replicate_mean` (candidate minus the mean of the
baseline and its replicates, the better estimate of the incumbent). A candidate difference
no larger than the floor gets verdict `within_noise_floor`, whatever its p-value. Fewer than
3 replicates give a floor that is likely too low; the JSON says so.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ab_stats
import cli_contract
import evaluate
from cli_contract import Rejected

SEAT_METRICS = [('win', True), ('team_kill_margin', True), ('kills', True), ('deaths', False),
                ('hook_landed_rate', True)]
EPISODE_METRICS = [('end_tick', True, 'mean'), ('kill_goal_rate', True, 'rate'), ('draw_rate', False, 'rate')]


def load_results(root: Path) -> tuple[list[tuple[str, dict]], dict]:
    """[(episode id, results)] under root, and exclusion counts."""
    if not root.is_dir():
        raise Rejected(f'not a directory: {root}')
    found, excluded = [], {'invalid_results': 0}
    for path in sorted(root.rglob('*.json')):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or 'per_seat' not in data or 'scores' not in data:
            continue
        try:
            evaluate.validate(data)
        except (TypeError, ValueError):
            excluded['invalid_results'] += 1
            continue
        found.append((str(path.relative_to(root)), data))
    return found, excluded


def group_names(grouping: str, seats: list[int]) -> list[str]:
    if grouping == 'seat':
        return [f'seat {seat}' for seat in seats]
    return ['west', 'east'] if grouping == 'team' else ['all']


def group_of(grouping: str, seat: int, seat_count: int) -> str:
    if grouping == 'seat':
        return f'seat {seat}'
    if grouping == 'team':
        return 'west' if seat < seat_count // 2 else 'east'
    return 'all'


def load_arm(root: Path, seats: list[int] | None, grouping: str) -> tuple[dict, dict]:
    """{group: [per-episode mean metrics]} plus "episodes": [episode rows]."""
    episodes, excluded = load_results(root)
    groups: dict[str, list] = {'episodes': []}
    for episode_id, data in episodes:
        count = len(data['scores'])
        subject = seats if seats is not None else list(range(count))
        if any(seat >= count for seat in subject):
            excluded['seat_out_of_range'] = excluded.get('seat_out_of_range', 0) + 1
            continue
        groups['episodes'].append({'episode_id': episode_id, 'end_tick': data['end_tick'],
                                   'kill_goal': data['end_reason'] == 'kill_goal',
                                   'draw': data['winner_team'] == 'draw'})
        per_group: dict[str, list[dict]] = {}
        for seat in subject:
            per_group.setdefault(group_of(grouping, seat, count), []).append(evaluate.metrics(data, seat))
        for name, rows in per_group.items():
            groups.setdefault(name, []).append({key: statistics.fmean(row[key] for row in rows)
                                                for key, _ in SEAT_METRICS})
    return groups, excluded


def value_fn(rows: list, key: str) -> list[float]:
    if key == 'end_tick':
        return [float(row['end_tick']) for row in rows]
    if key in ('kill_goal_rate', 'draw_rate'):
        return []
    return [float(row[key]) for row in rows if key in row]


def metric_value(rows: list, key: str) -> tuple[float, int] | None:
    if not rows:
        return None
    if key in ('kill_goal_rate', 'draw_rate'):
        field = 'kill_goal' if key == 'kill_goal_rate' else 'draw'
        return sum(row[field] for row in rows) / len(rows), len(rows)
    values = value_fn(rows, key)
    return (statistics.fmean(values), len(values)) if values else None


def metric_list(selected: list[str] | None) -> list[tuple]:
    metrics = [(key, higher, 'mean', None) for key, higher in SEAT_METRICS]
    metrics += [(key, higher, kind, 'episodes') for key, higher, kind in EPISODE_METRICS]
    if selected:
        known = {metric[0] for metric in metrics}
        unknown = [name for name in selected if name not in known]
        if unknown:
            raise Rejected(f'unknown metric {unknown}; valid: {", ".join(sorted(known))}')
        metrics = [metric for metric in metrics if metric[0] in selected]
    return metrics


def parse_seats(text: str | None) -> list[int] | None:
    if text is None:
        return None
    try:
        seats = sorted({int(part) for part in text.split(',') if part.strip()})
    except ValueError:
        raise Rejected('--seats must be comma-separated integers') from None
    if not seats or seats[0] < 0:
        raise Rejected('--seats must list nonnegative seats')
    return seats


def main(argv=None) -> int:
    parser = cli_contract.ArgumentParser(
        __doc__, prog='compare.py',
        examples=['python3 games/pudge-wars-basic/tools/compare.py BASE_DIR CAND_DIR --seats 0,1,2 --json',
                  'python3 games/pudge-wars-basic/tools/compare.py BASE_DIR CAND_DIR --group seat --metric deaths --json',
                  'python3 games/pudge-wars-basic/tools/compare.py BASE_DIR CAND_DIR --seats 0 --group all --replicate NF1 --replicate NF2 --replicate NF3 --json'])
    parser.add_argument('baseline_dir', type=Path)
    parser.add_argument('candidate_dir', type=Path)
    parser.add_argument('--group', choices=['team', 'seat', 'all'], default='team')
    parser.add_argument('--metric', action='append', help='limit to this metric (repeatable or comma-separated)')
    parser.add_argument('--seats', help='subject seats, comma-separated (default: every seat)')
    parser.add_argument('--target', help='lead metric shown first in the Markdown (default: win)')
    parser.add_argument('--baseline-label', help='name for the baseline arm (default: its directory name)')
    parser.add_argument('--candidate-label', help='name for the candidate arm (default: its directory name)')
    parser.add_argument('--replicate', type=Path, action='append', default=[],
                        help='a behavior-neutral baseline replicate arm on the same line-ups and seeds (repeatable)')

    def body(args):
        selected = [name.strip() for value in args.metric or [] for name in value.split(',') if name.strip()]
        metrics = metric_list(selected)
        seats = parse_seats(args.seats)
        base, excluded_base = load_arm(args.baseline_dir, seats, args.group)
        cand, excluded_cand = load_arm(args.candidate_dir, seats, args.group)
        if not base['episodes'] or not cand['episodes']:
            raise Rejected(f'an arm has no valid episodes (baseline {len(base["episodes"])}, '
                           f'candidate {len(cand["episodes"])})', reason='no_episodes')
        names = [name for name in group_names(args.group, seats or list(range(10))) if name in base or name in cand]
        deltas = ab_stats.build_deltas(base, cand, metrics, metric_value, value_fn, names)
        base_label = args.baseline_label or args.baseline_dir.name
        cand_label = args.candidate_label or args.candidate_dir.name
        target = args.target or ('win' if not selected else selected[0])
        report = ab_stats.emit_json(base_label, cand_label, target, deltas)
        replicate_differences: dict[tuple[str, str], list[float]] = {}
        for root in args.replicate:
            replicate, _ = load_arm(root, seats, args.group)
            for delta in ab_stats.build_deltas(base, replicate, metrics, metric_value, value_fn, names):
                if delta.base is not None and delta.cand is not None:
                    replicate_differences.setdefault((delta.metric, delta.group), []).append(delta.cand - delta.base)
        if args.replicate:
            for delta in report['deltas']:
                differences = replicate_differences.get((delta['metric'], delta['group']), [])
                delta['replicate_differences'] = differences
                delta['difference'] = (None if delta['base'] is None or delta['cand'] is None
                                       else delta['cand'] - delta['base'])
                delta['noise_floor'] = max((abs(value) for value in differences), default=None)
                # Against the mean of the baseline and its replicates, not the one baseline draw.
                delta['difference_vs_replicate_mean'] = (
                    None if delta['difference'] is None
                    else delta['difference'] - sum(differences) / (len(differences) + 1))
                if (delta['noise_floor'] is not None and delta['difference'] is not None
                        and abs(delta['difference']) <= delta['noise_floor']):
                    delta['verdict'] = 'within_noise_floor'
            report['noise_floor'] = {
                'replicates': [str(root) for root in args.replicate],
                'warning': None if len(args.replicate) >= 3 else
                'fewer than 3 replicate arms: the floor is likely too low',
            }
        report.update(unit=f'episode per {args.group} group (subject seats averaged within an episode)',
                      seats=seats, episodes={'baseline': len(base['episodes']), 'candidate': len(cand['episodes'])},
                      excluded_baseline=excluded_base, excluded_candidate=excluded_cand,
                      markdown=ab_stats.render_markdown(base_label, cand_label, base, cand, deltas, target, names, metrics))
        if args.replicate:
            lines = ['', '', f'## Noise floor ({len(args.replicate)} replicate baseline arms)', '',
                     '| metric | group | candidate − baseline | vs replicate mean | noise floor | verdict |', '|---|---|---|---|---|---|']
            lines += [f"| {d['metric']} | {d['group']} | {d['difference']:+.3f} | {d['difference_vs_replicate_mean']:+.3f} "
                      f"| {d['noise_floor']:.3f} | {d['verdict']} |"
                      for d in report['deltas'] if d['difference'] is not None and d['noise_floor'] is not None]
            if report['noise_floor']['warning']:
                lines += ['', report['noise_floor']['warning']]
            report['markdown'] += '\n'.join(lines) + '\n'
        return report

    return cli_contract.run(parser, body, argv)


if __name__ == '__main__':
    raise SystemExit(main())
