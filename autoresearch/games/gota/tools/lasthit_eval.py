#!/usr/bin/env python3
# Source: Metta-AI/personal_labs @ 07dc6f94760fae3cf2de36877e0b6186cfd1dc63; path: gods_of_the_arena_lab/tools/lasthit_eval.py
"""Local GotA last-hit telemetry evaluation using an explicit game manifest.

Run with uv run python tools/lasthit_eval.py --candidate PATH (from this lab),
optionally --reference PATH --episodes N --out DIR --no-swap --json PATH.
Use --mirror for candidate-only teams, --mixed for two candidate seats with
starter allies/opponents, and --label-candidate/--label-reference
for display names. Rate columns average per-seat rates using final episode ticks.
LH fields are last observed snapshots, not necessarily final-match counters.
Missing telemetry stays unknown; XP never substitutes for footman last hits.
Class labels remain unknown: these runner results and LH logs contain no verified
draft class evidence. Native draws and timeouts are reported separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Telemetry:
    tick: int
    lastHits: int
    heroKills: int
    towerKills: int
    level: int
    orders: int
    secures: int
    poisons: int
    lost: int
    deaths: int
    gold: int
    early: int | None = None
    missed: int | None = None


@dataclass
class SeatRecord:
    episode: str
    seat: int
    hero_class: str
    side: str
    policy: str
    policy_path: str
    seed: int | None = None
    ticks: int | None = None
    outcome: str | None = None
    total_xp: int | None = None
    telemetry: Telemetry | None = None
    basic_errors: list[str] = field(default_factory=list)
    status_reason: str | None = None
    xp_expected: int | None = None
    xp_matches: bool | None = None
    issues: list[str] = field(default_factory=list)


def read_json(path: Path, issues: list[str]) -> dict:
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError('expected an object')  # noqa: TRY004 - caller catches input errors
        return data
    except (OSError, ValueError) as error:
        issues.append(f'{path.name}: {error}')
        return {}


def candidate_seats(candidate_red: bool, *, mirror: bool = False,
                    mixed: bool = False) -> tuple[int, ...]:
    """Return the same roster used by execution and artifact attribution."""
    if mirror:
        return tuple(range(10))
    if mixed:
        return (1, 3) if candidate_red else (6, 8)
    return tuple(range(5)) if candidate_red else tuple(range(5, 10))


def parse_episode(directory: Path, candidate_red: bool, candidate: Path,
                  reference: Path, mirror: bool = False, mixed: bool = False) -> list[SeatRecord]:
    issues: list[str] = []
    result = read_json(directory / 'results.json', issues)
    status = read_json(directory / 'player_status.json', issues)
    xp = result.get('total_xp')
    if not (isinstance(xp, list) and len(xp) == 10
            and all(type(value) is int and value >= 0 for value in xp)):
        issues.append('results require ten nonnegative integer total_xp values')
        xp = [None] * 10
    outcome = result.get('outcome')
    if outcome not in ('RedTeam', 'BlueTeam', 'draw', 'time_limit'):
        issues.append(f'unknown or missing outcome: {outcome!r}')
        outcome = None
    players = status.get('players', [])
    if not isinstance(players, list):
        issues.append('player_status.players must be a list')
        players = []
    reasons = {p.get('slot'): p.get('reason') for p in players if isinstance(p, dict)}
    records = []
    roster = candidate_seats(candidate_red, mirror=mirror, mixed=mixed)
    for seat in range(10):
        is_candidate = seat in roster
        record = SeatRecord(
            episode=str(directory), seat=seat, hero_class='unknown',
            side='Red' if seat < 5 else 'Blue',
            policy='candidate' if is_candidate else 'reference',
            policy_path=str(candidate if is_candidate else reference),
            outcome=outcome, total_xp=xp[seat], status_reason=reasons.get(seat),
            issues=issues.copy(),
        )
        for name in ('seed', 'ticks'):
            value = result.get(name)
            if type(value) is int:
                setattr(record, name, value)
            else:
                record.issues.append(f'missing or invalid {name}')
        try:
            with (directory / f'logs/policy_agent_{seat}.log').open(errors='replace') as log:
                for line in log:
                    if 'BASIC error:' in line:
                        record.basic_errors.append(line.strip())
                    if line.strip().split()[:1] == ['LH']:
                        # A malformed last snapshot must not silently reuse an older one.
                        record.telemetry = None
                        try:
                            fields = line.split()[1:]
                            values = [int(value) for value in fields[:11]]
                            if len(values) != 11:
                                raise ValueError('expected 11 integer fields')
                            record.telemetry = Telemetry(*values)
                            # The first eleven fields are the stable LH contract.
                            # Read the two known optional counters; ignore future fields.
                            if len(fields) > 11:
                                record.telemetry.early = int(fields[11])
                            if len(fields) > 12:
                                record.telemetry.missed = int(fields[12])
                        except ValueError as error:
                            record.issues.append(f'malformed LH line: {error}: {line.strip()}')
        except OSError as error:
            record.issues.append(f'policy log: {error}')
        if record.status_reason != 'Completed':
            record.issues.append(f'player status: {record.status_reason!r}')
        telemetry = record.telemetry
        if telemetry is not None and record.total_xp is not None:
            record.xp_expected = (25 * telemetry.lastHits + 150 * telemetry.heroKills
                                  + 100 * telemetry.towerKills)
            record.xp_matches = record.xp_expected == record.total_xp
        records.append(record)
    return records


def mean_field(records: list[SeatRecord], name: str, *, rate: bool = False) -> str:
    values = []
    for record in records:
        value = (record.total_xp if name == 'total_xp' else
                 getattr(record.telemetry, name) if record.telemetry else None)
        if value is not None:
            if rate:
                if record.ticks is None or record.ticks <= 0:
                    continue
                value = value * 1000 / record.ticks
            values.append(value)
    return f'{statistics.mean(values):.2f}' if values else '-'


def report(records: list[SeatRecord], label_candidate: str = 'candidate',
           label_reference: str = 'reference', *, mixed: bool = False) -> None:
    labels = {'candidate': label_candidate, 'reference': label_reference}
    width = max(10, len(label_candidate), len(label_reference))
    print('\nLast hits/1,000 ticks are the headline; XP includes hero and structure kills.')
    print('Rates average per-hero-match rates using final episode ticks; LH uses the last snapshot.')
    print('Means use available observations; LH/n is telemetry coverage; - means unknown.')
    print('LH, level, deaths and lost are last snapshots; errors and mismatches are counts.')
    print(f"{'policy':{width}} {'class':>5} {'side':4} {'LH/n':>7} {'last hits':>10} "
          f"{'XP':>10} {'LH/1k':>9} {'XP/1k':>10} {'level':>7} {'deaths':>7} {'lost':>7} {'early':>7} {'missed':>8} {'errors':>6} {'XP !=':>5}")
    if mixed:
        print('Mixed table: candidate seats only. Reference summary includes allies and opponents.')
    groups = defaultdict(list)
    for record in records:
        if mixed and record.policy != 'candidate':
            continue
        groups[record.policy, record.hero_class, record.side].append(record)
    for (policy, hero_class, side), rows in sorted(groups.items()):
        coverage = f'{sum(r.telemetry is not None for r in rows)}/{len(rows)}'
        means = [mean_field(rows, name) for name in ('lastHits', 'total_xp', 'level', 'deaths', 'lost')]
        print(f'{labels[policy]:{width}} {hero_class:5} {side:4} {coverage:>7} '
              f'{means[0]:>10} {means[1]:>10} '
              f'{mean_field(rows, "lastHits", rate=True):>9} {mean_field(rows, "total_xp", rate=True):>10} '
              f'{means[2]:>7} {means[3]:>7} {means[4]:>7} '
              f'{mean_field(rows, "early"):>7} {mean_field(rows, "missed"):>8} '
              f'{sum(len(r.basic_errors) for r in rows):6} '
              f'{sum(r.xp_matches is False for r in rows):5}')
    for policy in ('candidate', 'reference'):
        rows = [r for r in records if r.policy == policy]
        if not rows:
            continue
        # Five allied seats are one team outcome, not five independent outcomes.
        teams = {(r.episode, r.side): r for r in rows}
        outcomes = Counter()
        for row in teams.values():
            if row.outcome in (None, 'draw', 'time_limit'):
                outcome = row.outcome or 'unavailable'
            else:
                outcome = 'win' if row.outcome == row.side + 'Team' else 'loss'
            outcomes[outcome] += 1
        counts = ', '.join(f'{name}={outcomes[name]}' for name in
                           ('win', 'loss', 'draw', 'time_limit', 'unavailable'))
        print(f'{labels[policy]}: mean last hits/1,000 ticks={mean_field(rows, "lastHits", rate=True)}; '
              f'mean XP/1,000 ticks={mean_field(rows, "total_xp", rate=True)}; '
              f'mean last hits/hero-match={mean_field(rows, "lastHits")}; '
              f'mean XP/hero-match={mean_field(rows, "total_xp")}; {counts}; '
              f'XP coverage={sum(r.total_xp is not None for r in rows)}/{len(rows)}')
    print('Player status reasons: ' + str(dict(Counter(r.status_reason for r in records))))
    for record in records:
        diagnostics = record.issues + record.basic_errors
        if record.xp_matches is False:
            diagnostics = diagnostics + [
                (f'XP MISMATCH: LH implies {record.xp_expected}, final XP={record.total_xp}; '
                f'LH tick={record.telemetry.tick}, final ticks={record.ticks}')
            ]
        for diagnostic in diagnostics:
            print(f'WARNING {record.episode} seat {record.seat}: {diagnostic}')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--episodes', type=int, default=4)
    parser.add_argument('--out', type=Path, default=ROOT / 'tmp/lasthit_eval' / datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f'))
    parser.add_argument('--no-swap', action='store_true')
    parser.add_argument('--json', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--mirror', action='store_true', help='Candidate in all ten seats; no swap.')
    mode.add_argument('--mixed', action='store_true', help='Candidate in Red seats 1/3, then Blue 6/8; reference elsewhere.')
    parser.add_argument('--label-candidate', default='candidate')
    parser.add_argument('--label-reference', default='reference')
    args = parser.parse_args()
    if args.episodes < 1:
        parser.error('--episodes must be positive')
    if not args.mirror and args.reference is None:
        parser.error('--reference is required unless --mirror is used')
    for path in ([args.candidate, args.manifest] if args.mirror else
                 [args.candidate, args.reference, args.manifest]):
        if not path.is_file():
            parser.error(f'file not found: {path}')
    candidate, reference, out = args.candidate.resolve(), (args.reference or args.candidate).resolve(), args.out.resolve()
    configurations = [True] if args.no_swap or args.mirror else [True, False]
    for candidate_red in configurations:
        directory = out / ('mirror' if args.mirror else 'candidate-red' if candidate_red else 'candidate-blue')
        if directory.exists():
            parser.error(f'refusing to reuse artifacts: {directory}')
    out.mkdir(parents=True, exist_ok=True)
    # The shared policy can be rebuilt while a batch is running. Freeze the bytes
    # once so the runner's digest check and both side configurations use one input.
    inputs = out / 'inputs'
    inputs.mkdir(exist_ok=True)
    snapshots = {}
    for role, source in ([('candidate', candidate)] if args.mirror else
                         [('candidate', candidate), ('reference', reference)]):
        data = source.read_bytes()
        frozen = inputs / f'{role}.bas'
        frozen.write_bytes(data)
        snapshots[role] = frozen
        print(f'{role} source: {source}; SHA-256: {hashlib.sha256(data).hexdigest()}', flush=True)
    records: list[SeatRecord] = []
    failed = False
    for candidate_red in configurations:
        directory = out / ('mirror' if args.mirror else 'candidate-red' if candidate_red else 'candidate-blue')
        roster = candidate_seats(candidate_red, mirror=args.mirror, mixed=args.mixed)
        players = [snapshots['candidate' if seat in roster else 'reference'] for seat in range(10)]
        command = ['uv', 'run', '--with', 'coworld==0.1.57', 'coworld', 'run-episode', str(args.manifest.resolve()),
                   *map(str, players), '--variant', 'competition',
                   '--output-dir', str(directory), '--timeout-seconds', '900', '-n', str(args.episodes)]
        print('Running: ' + shlex.join(command), flush=True)
        log_path = out / f'{directory.name}.runner.log'
        try:
            with log_path.open('w') as log:
                process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False)
            if process.returncode:
                failed = True
                print(f'Runner failed (exit {process.returncode}); diagnostic: {log_path}')
                print(log_path.read_text(errors='replace'))
        except OSError as error:
            failed = True
            print(f'Could not start runner: {error}')
        for index in range(args.episodes):
            episode = directory if args.episodes == 1 else directory / f'episode-{index + 1:04d}'
            records.extend(parse_episode(episode, candidate_red, candidate, reference, args.mirror, args.mixed))
    report(records, args.label_candidate, args.label_reference, mixed=args.mixed)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps([asdict(record) for record in records], indent=2) + '\n')
    print(f'Artifacts: {out}')
    return int(failed or any(record.issues or record.basic_errors for record in records))


if __name__ == '__main__':
    sys.exit(main())
