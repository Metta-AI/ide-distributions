"""Attribute GotA goal telemetry to recorded native decisions without claiming goal truth.

Input is a compiler map and native oracle candidate directory (seat-0.log through
seat-4.log, trace.jsonl, job.json, result.json). Logs alone remain supported, with
full decision coverage explicitly unmeasurable. No Paintbot predicates are used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'games/gota/compiler/tools'))
sys.path.insert(0, str(ROOT))
from pw_intent import parse_telemetry_line

from games.gota.tools.equivalence import (
    BASSY_COMMIT,
    POLYWORLD_COMMIT,
    checked_stream,
    completed_stream,
    sha256,
)

LEVELS = ('True', 'Believed', 'Acted', 'Acted properly', 'Result')


def audit(mapping: dict, logs: Path, *, trace=None, identity=None) -> dict:
    if mapping.get('backend') != 'gota':
        raise ValueError('GotA compiler map required')
    schedule = None
    trace_error = None
    if trace is not None:
        try:
            schedule = {seat: [] for seat in range(5)}
            for row in checked_stream(trace):
                if row.get('type') == 'tick':
                    for hero in row['decision_order']:
                        if hero < 105:
                            schedule[hero - 100].append(row['tick'])
        except (ValueError, TypeError, KeyError) as error:
            trace_error = str(error)
            schedule = None
    seats = []
    for seat in range(5):
        path = logs / f'seat-{seat}.log'
        events, issues = [], []
        previous = -1
        try:
            for line in path.read_text().splitlines():
                if any(marker in line for marker in ('BASIC error', 'VM disabled', 'Policy loading failed')):
                    raise ValueError('VM failure in log')
                if line.split(' ', 1)[0] not in ('PWD', 'PWE', 'PWP', 'PWB', 'PWC'):
                    continue
                for event in parse_telemetry_line(line, mapping):
                    if event['t'] < previous:
                        raise ValueError('backwards telemetry ticks')
                    previous = event['t']
                    if event['kind'] == 'PWD':
                        if events and event['t'] == events[-1]['t']:
                            raise ValueError('duplicate PWD decision')
                        events.append(event)
            if not events:
                raise ValueError('missing PWD decisions')
        except (OSError, ValueError, KeyError, TypeError) as error:
            issues.append(str(error))
        labels = Counter(goal for event in events for goal in event.get('goal_refs', []))
        rows = []
        if schedule is not None and not issues:
            expected = set(schedule[seat])
            if any(event['t'] not in expected for event in events):
                issues.append('telemetry outside actual native decisions')
            cursor = 0
            latest = None
            for tick in schedule[seat]:
                while cursor < len(events) and events[cursor]['t'] <= tick:
                    latest = events[cursor]
                    cursor += 1
                covered = latest is not None and tick - latest['t'] < 24
                rows.append({'tick': tick, 'status': 'attributed' if covered else 'unmeasurable',
                             'goal_refs': latest.get('goal_refs', []) if covered else [],
                             'rule': latest['r'] if covered else None})
        complete = identity is not None and bool(rows) and not issues and all(row['status'] == 'attributed' for row in rows)
        seats.append({'seat': seat, 'hero_id': 100 + seat, 'log_sha256': sha256(path) if path.is_file() else None,
                      'wire_decisions': len(events), 'goal_label_counts': dict(labels),
                      'coverage_status': 'complete' if complete else 'unmeasurable',
                      'coverage_reason': None if complete else (issues or trace_error or
                                          'no verified build/native identity and complete decision schedule'),
                      'decisions': rows, 'issues': issues})
    checks = [dict(component=component, index=index, **check,
                   status='unmeasurable', reason='no_reviewed_gota_predicate_evaluator',
                   goal_refs=[component] if component.startswith('G.') else [])
              for component, data in mapping['components'].items()
              for index, check in enumerate(data.get('checks', []), 1)]
    goals = {name: {'claim_status': 'unmeasurable', 'reason': 'no_evaluator_for_goal_claim'}
             for name in mapping.get('goals', {})}
    return {'schema': 'gota-goal-audit/1', 'build_id': mapping.get('build_id'),
            'source_commit': mapping.get('source_commit'), 'levels': list(LEVELS),
            'goals': goals, 'checks': checks, 'seats': seats,
            'trace_error': trace_error,
            'scope': 'declared goal attribution only; labels do not identify branches or prove claims',
            'identity': identity,
            'map_source_verification': ('checked immutable build and committed source' if identity else
                                        'unverified caller-supplied map; attribution is unmeasurable')}


def verify_native_identity(directory: Path, version: dict) -> dict:
    """Bind immutable build policy bytes to native loaded sources and recorded logs."""
    receipt = json.loads((directory / 'identity.json').read_text())
    result = json.loads((directory / 'result.json').read_text())
    job = json.loads((directory / 'job.json').read_text())
    required = {'job.json', 'result.json', 'trace.jsonl'} | {f'seat-{seat}.log' for seat in range(10)}
    artifacts = receipt.get('artifacts', {})
    if set(artifacts) != required or any(sha256(directory / name) != digest for name, digest in artifacts.items()):
        raise ValueError('native evidence artifact hash mismatch')
    if (version['engine']['commit'] != POLYWORLD_COMMIT
            or receipt.get('engine') != POLYWORLD_COMMIT or result.get('engine') != POLYWORLD_COMMIT
            or receipt.get('bassy') != BASSY_COMMIT or result.get('bassy') != BASSY_COMMIT):
        raise ValueError('native evidence engine mismatch')
    if (receipt.get('candidate_seats') != list(range(5)) or job.get('draft') != 'open'
            or not job.get('oracle') or 'roster' in job
            or receipt.get('seed') != job['seed'] or receipt.get('max_battle_ticks') != job['ticks']):
        raise ValueError('native evidence setup/seat mismatch')
    for field in ('binary_sha256', 'binary_fingerprint'):
        digest = receipt.get(field, '')
        if len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
            raise ValueError('missing native binary identity')
    hashes = receipt.get('seat_policy_sha256', [])
    loaded = receipt.get('seat_policy_sha1', [])
    if (len(hashes) != 10 or len(loaded) != 10
            or hashes[:5] != [version['artifact_hashes']['policy.bas']] * 5
            or [hero.get('slot') for hero in result['heroes']] != list(range(10))
            or [hero.get('policy_source_sha1') for hero in result['heroes']] != loaded):
        raise ValueError('native loaded policy differs from build')
    for seat in range(10):
        snapshot = directory / f'policy-{seat}.bas'
        if (sha256(snapshot) != hashes[seat]
                or hashlib.sha1(snapshot.read_bytes()).hexdigest() != loaded[seat]):
            raise ValueError('native policy snapshot hash mismatch')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--build', help='immutable build ID; verifies committed source and map')
    source.add_argument('--map', type=Path, help='unverified exploration; coverage remains unmeasurable')
    parser.add_argument('--logs', type=Path, required=True)
    parser.add_argument('--native-evidence', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    identity = None
    if args.build:
        from strategy_audit import load_build
        build, version, mapping, _ = load_build(args.build)
        identity = verify_native_identity(args.logs, version)
        args.map = build / 'map.json'
    else:
        mapping = json.loads(args.map.read_text())
    trace = None
    if args.native_evidence or args.build:
        job = json.loads((args.logs / 'job.json').read_text())
        if job.get('draft') != 'open' or not job.get('oracle') or 'roster' in job:
            parser.error('native evidence must be an Open Draft oracle run with policy decisions')
        trace = completed_stream(args.logs, job['seed'], job['ticks'])
    report = audit(mapping, args.logs, trace=trace, identity=identity)
    report['map_sha256'] = sha256(args.map)
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'report': str(args.out), 'claim_status': 'unmeasurable',
                      'coverage': [seat['coverage_status'] for seat in report['seats']]}))
    return 0  # Analysis completed, never a policy pass.


if __name__ == '__main__':
    raise SystemExit(main())
