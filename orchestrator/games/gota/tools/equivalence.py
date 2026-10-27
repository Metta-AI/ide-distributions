"""Exact five-hero attempted-command oracle using the pinned native engine.

Seeds 1–20, Red seats 0–4, fixed opponents, Open Draft. This is local trajectory
identity, not league qualification. PRINT records alone are excluded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from itertools import zip_longest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from games.gota.gates.limits import POLYWORLD_COMMIT

BASSY_COMMIT = 'a02e6dd5889310b13d21725c891bde8d01539f08'
BASELINE = ROOT / 'tests/fixtures/gota/v13-live.bas'
BASELINE_SHA256 = '1637c5604a522342de710afbffb16cdaa84befe99d3df17fa28e146414f86f9c'
ACTION_KEYS = {'heroId', 'tick', 'kind', 'slot', 'first', 'second', 'offset'}
ACTION_KINDS = {1, 2, 3, 4, 5, 6, 10, 15, 16, 17, 18}
SETUP_KEYS = ('schema', 'engine', 'bassy', 'seed', 'draft_mode', 'max_battle_ticks',
              'hero_ids', 'candidate_seats', 'config')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_stream(path: Path):
    with path.open() as source:
        for number, line in enumerate(source, 1):
            try:
                yield json.loads(line)
            except ValueError as error:
                raise ValueError(f'{path}:{number}: invalid trace JSON') from error


def checked_stream(records):
    """Validate complete scheduling evidence, including decisions with no commands."""
    header = None
    counts = [0] * 10
    draft_decisions = [0] * 10
    tick = 0
    draft_ticks = battle_ticks = 0
    ended = False
    for row in records:
        if not isinstance(row, dict):
            raise TypeError('trace row must be an object')
        kind = row.get('type')
        if kind == 'print':
            continue
        if ended:
            raise ValueError('trace contains evidence after its end')
        if header is None:
            if kind != 'header' or any(key not in row for key in SETUP_KEYS):
                raise ValueError('missing trace header/setup')
            if (type(row['seed']) is not int or type(row['max_battle_ticks']) is not int
                    or row['schema'] != 1 or row['hero_ids'] != list(range(100, 110))
                    or row['candidate_seats'] != list(range(5))
                    or row['draft_mode'] != 'open' or row['max_battle_ticks'] < 1
                    or row['engine'] != POLYWORLD_COMMIT or row['bassy'] != BASSY_COMMIT):
                raise ValueError('unsupported trace setup')
            config = row['config']
            if (not isinstance(config, dict) or config.get('seed') != row['seed']
                    or config.get('max_ticks') != row['max_battle_ticks']
                    or config.get('draft_mode') != row['draft_mode']):
                raise ValueError('trace config contradicts requested setup')
            header = row
            yield {key: row[key] for key in SETUP_KEYS}
            continue
        if kind == 'tick':
            if type(row.get('tick')) is not int or row['tick'] != tick + 1:
                raise ValueError('missing, repeated, or out-of-order tick')
            tick += 1
            order = row.get('decision_order')
            phase = row.get('phase')
            if (not isinstance(order, list) or len(set(order)) != len(order)
                    or any(hero not in range(100, 110) for hero in order)
                    or phase not in ('draft', 'battle')
                    or (phase == 'draft' and len(order) > 1)
                    or (phase == 'battle' and len(order) != 10)):
                raise ValueError('invalid or incomplete decision order')
            if phase == 'draft':
                if battle_ticks:
                    raise ValueError('draft phase resumed after battle')
                draft_ticks += 1
            else:
                battle_ticks += 1
            for hero in order:
                counts[hero - 100] += 1
                if phase == 'draft':
                    draft_decisions[hero - 100] += 1
            actions = row.get('actions')
            if not isinstance(actions, list):
                raise ValueError('missing action stream')
            previous_seat = -1
            for action in actions:
                if (not isinstance(action, dict) or set(action) != ACTION_KEYS
                        or any(type(action[key]) is not int for key in ACTION_KEYS - {'offset'})
                        or action['heroId'] not in order or action['tick'] != tick
                        or action['kind'] not in ACTION_KINDS
                        or not isinstance(action['offset'], list) or len(action['offset']) != 2
                        or any(type(value) is not int for value in action['offset'])):
                    raise ValueError('invalid attempted action')
                seat = order.index(action['heroId'])
                if seat < previous_seat:
                    raise ValueError('action order disagrees with native seat order')
                previous_seat = seat
            yield {'type': kind, 'tick': tick, 'phase': phase, 'decision_order': order,
                   'actions': [action for action in actions if action['heroId'] < 105]}
        elif kind == 'end':
            if (not tick or row.get('complete') is not True or row.get('errors') != []
                    or row.get('ticks') != tick or row.get('seat_decisions') != counts
                    or any(count <= 0 for count in counts) or any(count <= 0 for count in draft_decisions)
                    or any(type(row.get(name)) is not int for name in ('ticks', 'battle_ticks', 'draft_ticks'))
                    or row.get('battle_ticks') != battle_ticks or row.get('draft_ticks') != draft_ticks
                    or draft_ticks + battle_ticks != tick or battle_ticks < 1
                    or battle_ticks > header['max_battle_ticks']
                    or row.get('outcome') not in ('RedTeam', 'BlueTeam', 'draw', 'time_limit')
                    or (row['outcome'] == 'time_limit' and battle_ticks != header['max_battle_ticks'])):
                raise ValueError('incomplete/error trace or missing decisions')
            ended = True
            yield {key: row[key] for key in ('type', 'ticks', 'battle_ticks', 'draft_ticks', 'outcome', 'seat_decisions')}
        else:
            raise ValueError(f'unknown trace record: {kind}')
    if not ended:
        raise ValueError('empty or truncated trace')


def compare_streams(reference, candidate) -> dict:
    """Return a fail-closed verdict; preserve the first ordered-stream difference."""
    first = None
    rows = 0
    try:
        for index, (left, right) in enumerate(zip_longest(checked_stream(reference), checked_stream(candidate))):
            rows += 1
            if left != right and first is None:
                first = {'record': index, 'reference': left, 'candidate': right}
    except (ValueError, TypeError, KeyError) as error:
        return {'equivalent': False, 'error': str(error), 'first_difference': first, 'records': rows}
    return {'equivalent': first is None, 'first_difference': first, 'records': rows}


def cached_binary() -> Path:
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import gota_local
    return gota_local.binary()


def completed_stream(directory: Path, seed: int, ticks: int):
    result = json.loads((directory / 'result.json').read_text())
    yield {'type': 'header', 'schema': 1, 'engine': result['engine'],
           'bassy': result['bassy'], 'seed': seed,
           'draft_mode': 'open', 'max_battle_ticks': ticks,
           'hero_ids': list(range(100, 110)), 'candidate_seats': list(range(5)),
           'config': result['config']}
    yield from read_stream(directory / 'trace.jsonl')
    yield {'type': 'end', 'ticks': result['ticks'], 'battle_ticks': result['battle_ticks'],
           'draft_ticks': result['draft_ticks'],
           'complete': True, 'outcome': result['outcome'],
           'errors': [hero['vm_error'] for hero in result['heroes'] if hero['vm_failed']],
           'seat_decisions': [hero['decisions'] for hero in result['heroes']]}


def run(candidate: Path, output: Path, *, seeds=range(1, 21), ticks=28800, opponent=BASELINE, expected_native=None) -> dict:
    seeds = tuple(seeds)
    candidate, opponent = candidate.resolve(), opponent.resolve()
    if sha256(BASELINE) != BASELINE_SHA256:
        raise ValueError('exact v13-live baseline hash mismatch')
    binary = cached_binary()
    from games.gota.tools.native_build import validate
    native_identity = validate(binary, 'gota_match', POLYWORLD_COMMIT)
    if expected_native is not None and native_identity != expected_native:
        raise ValueError('oracle binary differs from immutable compiler native provenance')
    output.mkdir(parents=True, exist_ok=False)
    identities = {'engine': POLYWORLD_COMMIT, 'binary_sha256': sha256(binary),
                  'binary_fingerprint': native_identity['fingerprint'],
                  'native_build': native_identity,
                  'bassy': BASSY_COMMIT,
                  'reference_sha256': BASELINE_SHA256, 'candidate_sha256': sha256(candidate),
                  'opponent_sha256': sha256(opponent), 'draft_mode': 'open',
                  'candidate_seats': list(range(5)), 'hero_ids': list(range(100, 105)),
                  'max_battle_ticks': ticks}
    results = []
    for seed in seeds:
        streams = []
        for name, policy in (('reference', BASELINE), ('candidate', candidate)):
            directory = output / f'seed-{seed}-{name}'
            directory.mkdir()
            trace = directory / 'trace.jsonl'
            # Freeze the exact bytes supplied to the loader; never trust later mutable paths.
            policies = [policy] * 5 + [opponent] * 5
            snapshots = []
            for seat, source in enumerate(policies):
                snapshot = directory / f'policy-{seat}.bas'
                shutil.copyfile(source, snapshot)
                snapshots.append(snapshot)
            loaded_sha256 = [sha256(path) for path in snapshots]
            expected = identities['reference_sha256' if name == 'reference' else 'candidate_sha256']
            if loaded_sha256 != [expected] * 5 + [identities['opponent_sha256']] * 5:
                raise ValueError('policy source changed after oracle identity was recorded')
            loaded_sha1 = [hashlib.sha1(path.read_bytes()).hexdigest() for path in snapshots]
            job = {'seed': seed, 'ticks': ticks, 'draft': 'open', 'oracle': True,
                   'seats': [str(path) for path in snapshots], 'out': str(directory)}
            job_path = directory / 'job.json'
            job_path.write_text(json.dumps(job))
            command = [str(binary), 'match', str(job_path)]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)
            (directory / 'native.log').write_text(completed.stdout + completed.stderr)
            if completed.returncode or not trace.is_file():
                results.append({'seed': seed, 'equivalent': False,
                                'error': f'{name} native process failed: {completed.returncode}'})
                break
            if validate(binary, 'gota_match', POLYWORLD_COMMIT) != native_identity:
                raise ValueError('native identity changed during oracle run')
            result = json.loads((directory / 'result.json').read_text())
            if (result.get('engine') != POLYWORLD_COMMIT or result.get('bassy') != BASSY_COMMIT
                    or [hero.get('policy_source_sha1') for hero in result['heroes']] != loaded_sha1
                    or [sha256(path) for path in snapshots] != loaded_sha256):
                raise ValueError('native loaded-policy or engine identity mismatch')
            artifacts = ['job.json', 'result.json', 'trace.jsonl'] + [f'seat-{seat}.log' for seat in range(10)]
            receipt = {**identities, 'seed': seed, 'candidate_seats': list(range(5)),
                       'seat_policy_sha256': loaded_sha256, 'seat_policy_sha1': loaded_sha1,
                       'artifacts': {name: sha256(directory / name) for name in artifacts}}
            (directory / 'identity.json').write_text(json.dumps(receipt, indent=2) + '\n')
            streams.append(trace)
        if len(streams) == 2:
            verdict = compare_streams(*(completed_stream(path.parent, seed, ticks) for path in streams))
            results.append({'seed': seed, **verdict,
                            'trace_sha256': [sha256(path) for path in streams]})
    report = {'schema': 'gota-equivalence/1', **identities, 'seeds': list(seeds),
              'equivalent': bool(results) and all(row['equivalent'] for row in results),
              'scope': 'local five-hero attempted-action identity; no league or goal-truth claim',
              'results': results}
    (output / 'equivalence.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path, nargs='?')
    parser.add_argument('--build', help='checked immutable build; binds expected oracle executable')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        expected = None
        if args.build:
            if args.candidate is not None:
                parser.error('use --build or a fixture candidate, not both')
            sys.path.insert(0, str(ROOT / 'games/gota/compiler/tools'))
            from strategy_audit import load_build
            path, version, _, _ = load_build(args.build)
            args.candidate = path / 'policy.bas'
            expected = version.get('native_builds', {}).get('gota_match')
            if expected is None:
                raise ValueError('build has no verified native oracle provenance')
        elif args.candidate is None:
            parser.error('candidate or --build is required')
        report = run(args.candidate, args.out, expected_native=expected)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(report))
    return 0 if report['equivalent'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
