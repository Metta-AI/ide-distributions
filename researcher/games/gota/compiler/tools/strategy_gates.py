# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/strategy_gates.py
"""Local-only strategy compilation gates. Missing evidence is a failure, never a pass."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pw_local
import pw_release
import pw_terrain
from strategy_build import LAB, REPO, build_path, dump, read, assert_inputs


def run_tool(arguments: list[str], output: Path, timeout: int = 600) -> dict:
    completed = subprocess.run([sys.executable, str(LAB / 'tools/pw.py'), *arguments, '--json'],
                               cwd=REPO, capture_output=True, text=True, timeout=timeout)
    output.with_suffix('.log').write_text(completed.stderr)
    try:
        envelope = json.loads(completed.stdout)
    except ValueError:
        envelope = {'ok': False, 'failures': [{'message': 'child did not return a JSON envelope'}],
                    'result': None}
        output.with_suffix('.stdout').write_text(completed.stdout)
    envelope['exit_code'] = completed.returncode
    dump(output, envelope)
    return envelope


def screen_gate(envelope: dict, intent: str) -> dict:
    result = envelope.get('result') or {}
    numbers = result.get('summary', {}).get('seed_balanced', {})
    low, high = numbers.get('ci95_low'), numbers.get('ci95_high')
    complete = envelope.get('ok') is True and result.get('matches_with_bad_seats') == 0
    interval = isinstance(low, (int, float)) and isinstance(high, (int, float))
    outcome_ok = (intent == 'behavior_change' or
                  (interval and high >= 0.5 ))
    return {'passed': bool(complete and outcome_ok), 'summary': numbers,
            'intent': intent, 'outcome_advisory': intent == 'behavior_change',
            'identical_play': result.get('identical_play', False),
            'matches_with_bad_seats': result.get('matches_with_bad_seats'),
            'warning': 'local regression; hosted evaluation needed' if interval and high < 0.5 else None}


def parse_peaks(text: str) -> dict:
    peaks = {}
    for label in ('instructions', 'work'):
        match = re.search(r'^peak_' + label + r'=\[([^\]]+)\]', text, re.M)
        if not match:
            raise ValueError(f'missing per-seat peak_{label}')
        values = [int(value.strip()) for value in match.group(1).split(',')]
        # The engine prints its MaxSeats array, including unused zero-valued slots.
        if len(values) < 16 or any(value <= 0 for value in values[:16]) or any(values[16:]):
            raise ValueError(f'peak_{label} must contain 16 positive seat values')
        peaks[label] = values[:16]
    return peaks


def budget_gate(policy: Path, opponent: Path, directory: Path, budget: dict) -> dict:
    build = pw_local.check_build(pw_release.current_tag())
    command = pw_local.headless_command(build, policy, opponent, 7, 0, 14400, pw_local.LEAGUE_GLORY)
    with pw_terrain.session(pw_release.current_tag()) as terrain:
        env = dict(os.environ, PW_BASIC_PEAKS='1')
        if terrain:
            env['PW_TERRAIN_CACHE_DIR'] = str(terrain)
        result = subprocess.run(command, cwd=pw_release.release_tree(), env=env,
                                capture_output=True, text=True, timeout=300)
    (directory / 'peaks.log').write_text(result.stdout + result.stderr)
    try:
        peaks = parse_peaks(result.stdout)
        # Gate applies to the complete 16-seat run, not merely the candidate's best seat.
        numbers = {'instructions': max(peaks['instructions']), 'work': max(peaks['work']),
                   'print_bytes': budget['telemetry']['bytes'], 'print_events': budget['telemetry']['events']}
        passed = result.returncode == 0 and numbers['instructions'] <= 40000 and numbers['work'] <= 100000
        passed = passed and numbers['print_bytes'] <= 512 and numbers['print_events'] <= 64
        gate = {'passed': bool(passed), 'summary': numbers, 'per_seat': peaks,
                'seed': 7, 'max_ticks': 14400, 'limits': {'instructions': 40000, 'work': 100000,
                                                        'print_bytes': 512, 'print_events': 64}}
    except (ValueError, KeyError) as error:
        gate = {'passed': False, 'summary': str(error)}
    dump(directory / 'peaks.json', gate)
    return gate


def telemetry_gate(policy: Path, opponent: Path, directory: Path, mapping: dict) -> dict:
    import pw_intent
    # Small, bounded recording; full-length seat-health coverage belongs to G4.
    recordings = LAB / 'tmp/strategy-evidence' / directory.parent.name / 'telemetry'
    envelope = run_tool(['intent', 'record', str(policy), str(opponent), '--seeds', '7',
                         '--sides', '0,1', '--ticks', '720', '--out', str(recordings)],
                        directory / 'telemetry-record.json')
    result = pw_intent.validate_v2_logs(recordings, mapping)
    result['passed'] = bool(envelope.get('ok') and result['passed'])
    dump(directory / 'telemetry.json', result)
    return result


def verify(build_id: str) -> dict:
    from strategy_format import parse_strategy, lint_strategy
    stage = build_path(build_id, staged=True)
    order = read(stage / 'work_order.json')
    assert_inputs(order)
    directory = stage / 'verify'
    directory.mkdir(exist_ok=True)
    policy = stage / 'policy.bas'
    if not policy.exists():
        raise ValueError('assemble the staged build before verify')
    diagnostics = [d.to_dict() for d in lint_strategy(parse_strategy(REPO / order['source_path']))]
    gates = {'G1': {'passed': not any(d['level'] == 'error' for d in diagnostics),
                    'summary': diagnostics}}
    compiled = run_tool(['local', 'compile', str(policy), '--ticks', '720'], directory / 'compile.json')
    seats = (compiled.get('result') or {}).get('seats', [])
    gates['G2'] = {'passed': bool(compiled.get('ok') and len(seats) == 16),
                   'summary': {'seats': len(seats), 'failures': compiled.get('failures')}}
    if order['milestone'] == 'm1':
        opponent = LAB / 'reference/base-bassy-28030de6.bas'
    elif order['previous_build']:
        opponent = build_path(order['previous_build']) / 'policy.bas'
    else:
        # Initial tooling fixture has no predecessor; compare against itself for seat-health only.
        opponent = policy
    if not gates['G2']['passed']:
        for name in ('G3', 'G4', 'G5'):
            gates[name] = {'passed': False, 'summary': 'not run: G2 failed'}
        dump(stage / 'gates.json', gates)
        return gates
    gates['G3'] = budget_gate(policy, opponent, directory, read(stage / 'budget.json'))
    screen = run_tool(['local', 'screen', str(policy), str(opponent), '--seeds', '1-28',
                       '--out', str(directory / 'screen')], directory / 'screen.json')
    gates['G4'] = screen_gate(screen, order['intent'])
    gates['G5'] = telemetry_gate(policy, opponent, directory, read(stage / 'map.json'))
    dump(stage / 'gates.json', gates)
    return gates
