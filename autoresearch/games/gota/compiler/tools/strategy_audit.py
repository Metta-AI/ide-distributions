# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/strategy_audit.py
"""Evidence-backed five-level strategy audit. Missing evidence never implies a pass."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import pw_cli
import pw_episodes as pe
import pw_intent
import strategy_build as builds
import strategy_format as sf
from strategy_audit_runtime import reconstruct

LEVELS = ('True', 'Believed', 'Acted', 'Acted properly', 'Result')
# A missing execution predicate cannot be treated as correct execution.
RESULT_REQUIRES_FULL_EXECUTION = True
BASELINE = 'b41ef1fc-1'
# The second build changes only print generation and adds an empty flush phase.
# Its unchanged component units passed G1-G5 and 56 identical-play matches.
RUNTIME_BASELINES = (BASELINE, '3d0f8a4f-1')
# The codec is qualified by real-engine roundtrip, transport and kill-switch tests.
# Its surrounding skeleton is independently qualified by the immutable foundation build.
COMMS_RUNTIME_SHA256 = '05639c3995e9d527f0e4b124a3d37b57d76636266102a8da7d21f5c4603d1e4e'


# Exact contracts; a new release needs replay and semantic requalification, not a range.
ENGINE_CONTRACTS = {'coworld-v0.3.89': ('118e1619', 48),
                    'coworld-v0.3.115': ('244dc62b', 49)}


def check_engine_contract(engine, rules):
    expected = ENGINE_CONTRACTS.get(engine['tag'])
    if expected is None or not engine['commit'].startswith(expected[0]) or rules != expected[1]:
        raise ValueError('unqualified audit engine/rules contract')


def binding(component, check):
    value = [component, check['level'], check['text'], check.get('reads', [])]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def load_build(build_id):
    path = builds.build_path(build_id)
    try:
        version = builds.checked_build(path)
    except (OSError, KeyError, ValueError) as error:
        raise pw_cli.UsageError(f'incomplete or unreadable build: {error}') from error
    mapping = builds.read(path / 'map.json')
    if 'map.json' not in version.get('artifact_hashes', {}):
        raise pw_cli.UsageError('build has no map integrity record')
    try:
        source = subprocess.run(['git', 'show', f"{version['source_commit']}:{version['source_path']}"],
                                cwd=builds.REPO, check=True, capture_output=True).stdout
    except subprocess.CalledProcessError as error:
        raise pw_cli.UsageError('committed strategy source is unavailable') from error
    if hashlib.sha256(source).hexdigest() != version['source_hashes'][version['source_path']]:
        raise pw_cli.UsageError('committed strategy source hash mismatch')
    with tempfile.TemporaryDirectory(prefix='pw-audit-source-') as directory:
        source_path = Path(directory) / 'STRATEGY.md'
        source_path.write_bytes(source)
        strategy = sf.parse_strategy(source_path)
    if strategy.diagnostics:
        raise pw_cli.UsageError('committed source cannot be parsed')
    from strategy_basic import build_map
    units = {p.stem: p.read_text() for p in (path / 'units').glob('*.bas')}
    expected = build_map(strategy, units, build_id, version['source_commit'], {'telemetry': {'log_offsets': {}}})
    for key in ('rules', 'roles', 'commitment', 'codes', 'adaptations'):
        if mapping[key] != expected[key]:
            raise pw_cli.UsageError(f'build map disagrees with committed source: {key}')
    for key, comp in expected['components'].items():
        if mapping['components'][key]['checks'] != comp['checks'] or mapping['components'][key]['text_hash'] != comp['text_hash']:
            raise pw_cli.UsageError(f'build map disagrees with committed source: {key}')
    return path, version, mapping, strategy


def candidate_seats(ep, version, uploads):
    if ep.meta['engine_release'] != version['engine']['tag']:
        raise ValueError('engine identity differs from build')
    if ep.meta['mode'] != 'teams' or ep.meta['seats'] != 16:
        raise ValueError('audit requires a 16-seat teams replay')
    if ep.source.kind == 'local':
        if ep.source.local_meta is None:
            raise ValueError('local recording has no policy hash metadata')
        meta = builds.read(ep.source.local_meta)
        if meta.get('engine_release') != version['engine']['tag']:
            raise ValueError('recording engine identity differs from build')
        side = meta.get('a_side')
        if side not in (0, 1):
            raise ValueError('recording has no candidate side')
        sides = [side if arm == 'A' else 1 - side for arm in ('A', 'B')
                 if meta.get('policies', {}).get(arm, {}).get('sha256') == version['policy_sha256']]
        seats = [seat for seat in range(16) if seat % 2 in sides]
    else:
        played_version = ep['episodes'].iloc[0]['coworld_version']
        if played_version != version['engine']['tag'].removeprefix('coworld-v'):
            raise ValueError('hosted episode release differs from build or is missing')
        # Receipts must carry the immutable platform version ID; display names are insufficient.
        ids = {row['policy_version_id'] for row in uploads if row.get('build_id') == version['build_id']
               and row.get('policy_version_id') and row.get('policy_sha256') == version['policy_sha256']}
        seats = [int(row.seat) for row in ep['seats'].itertuples() if row.policy_version_id in ids]
    if not seats:
        raise ValueError('no seat has a verified policy identity for this build')
    return seats



def telemetry_phase(event):
    """Logical runtime phase; compact batches are checked separately at tick end."""
    kind = event['kind']
    if kind == 'PWE':
        return {5: 0, 4: 3, 1: 4, 2: 5, 3: 5}[event['e']]
    if kind == 'PWP':
        return 9 if event['a'] == 0 else 2
    if kind == 'PWC':
        return 1 if event['s'] == 2 else 6
    return 7 if kind == 'PWD' else 8


def read_log(directory, seat, mapping):
    paths = [p for p in directory.rglob('*.log') if not pe.in_cache(p) and any(
        (match := pattern.search(p.name)) and int(match.group(1)) == seat for pattern in pe.POLICY_LOG_PATTERNS)]
    if len(paths) != 1:
        return [], [{'reason': 'missing_seat_log' if not paths else 'duplicate_seat_logs', 't': None}], paths
    events, issues = [], []
    last, last_phase = -1, -1
    for number, line in enumerate(paths[0].read_text(errors='replace').splitlines(), 1):
        if 'BASIC error:' in line or 'BASIC VM disabled' in line:
            issues.append({'reason': 'seat_disabled', 't': last, 'line': number})
        if line.split(' ', 1)[0] not in pw_intent.V2_KEYS:
            continue
        try:
            parsed = pw_intent.parse_telemetry_line(line, mapping)
            tick = parsed[0]['t']
            batch = line.startswith('PWC v=3 ')
            phase = 10 if batch else telemetry_phase(parsed[0])
            if tick < last:
                raise pw_intent.IntentError('ticks went backwards')
            if tick == last and phase < last_phase:
                raise pw_intent.IntentError('telemetry phase order went backwards')
            if tick == last and batch and last_phase == 10:
                raise pw_intent.IntentError('duplicate compact batch')
            last, last_phase = tick, phase
            for event in parsed:
                row = {**event, 'line': number}
                if batch:
                    # Move only batch records to their declared logical phase.
                    # Ordinary telemetry is never sorted to conceal bad ordering.
                    position = next((i for i, previous in enumerate(events)
                                     if previous['t'] == tick and
                                     telemetry_phase(previous) > telemetry_phase(event)), len(events))
                    events.insert(position, row)
                else:
                    events.append(row)
        except (pw_intent.IntentError, ValueError, KeyError) as error:
            issues.append({'reason': 'malformed_telemetry', 't': last, 'line': number, 'detail': str(error)})
    return events, issues, paths


class Evidence:
    """Bounded summary samples and complete evidence rows, keyed by declared check identity."""
    def __init__(self, mapping):
        self.checks = {f'{component}.{i}': {'component': component, 'binding': binding(component, check), **check}
                       for component, entry in mapping['components'].items()
                       for i, check in enumerate(entry['checks'], 1)}
        self.rows = []

    def add(self, check, episode, seat, t, status, reason=None, **detail):
        self.rows.append({'check': check, 'episode': episode, 'seat': seat, 't': t,
                          'status': status, 'reason': reason, **detail})

    def summaries(self, selected, episode=None):
        result = []
        for key in selected:
            rows = [row for row in self.rows if row['check'] == key and (episode is None or row['episode'] == episode)]
            counts = Counter(row['status'] for row in rows)
            if counts['fail']:
                status = 'fail'
            elif counts['unmeasurable']:
                status = 'unmeasurable'
            elif counts['measured']:
                status = 'measured'
            elif counts['pass']:
                status = 'pass'
            else:
                status = 'not_exercised'
            observations = [row for row in rows if row['t'] is not None]
            measurable = sum(row['status'] in ('pass', 'fail', 'measured') for row in observations)
            unknown = sum(row['status'] == 'unmeasurable' for row in observations)
            blocked_seats = len({(row['episode'], row['seat']) for row in rows
                                 if row['t'] is None and row['status'] == 'unmeasurable'})
            result.append({'id': key, **self.checks[key], 'status': status,
                           'opportunities': measurable + unknown, 'measurable': measurable,
                           'unmeasurable': unknown, 'unmeasurable_seats': blocked_seats, 'violations': counts['fail'],
                           'coverage': measurable / (measurable + unknown) if measurable + unknown and not blocked_seats else None,
                           'reasons': dict(Counter(row['reason'] for row in rows if row['reason'])),
                           'samples': sorted(rows, key=lambda r: r['status'] == 'pass')[:5]})
        return result


def select_checks(evidence, check=None, level=None):
    valid = list(evidence.checks) + sorted({c['component'] for c in evidence.checks.values()})
    if check and check not in valid:
        raise pw_cli.UsageError(f'unknown check: {check}', valid=valid)
    selected = [key for key, row in evidence.checks.items()
            if (not check or check in (key, row['component'])) and (not level or row['level'] == level)]
    if not selected:
        raise pw_cli.UsageError('no checks match the requested component and level', valid=valid)
    return selected


def report_markdown(result):
    lines = [f"# Strategy audit: {result['build_id']}", '',
             'Recorded evidence audit; source identities and replay verification are recorded in audit.json.', '',
             '| Check | Level | Status | Measurable / opportunities | Unknown seats | Violations |',
             '| --- | --- | --- | --- | --- | --- |']
    for row in result['checks']:
        lines.append(f"| {row['id']} | {row['level']} | {row['status']} | {row['measurable']}/{row['opportunities']} | {row['unmeasurable_seats']} | {row['violations']} |")
    transport_rows = [(episode['id'], seat['seat'], seat['communication_transport'])
                      for episode in result.get('episodes', []) for seat in episode['seats']
                      if seat.get('communication_transport')]
    if transport_rows:
        lines.extend(['', '## Communication transport', '',
                      'Transport checks compare accepted payloads and actual sends with replay delivery.',
                      'They do not establish the truth of message claims or successful receiver behavior.', '',
                      '| Episode | Seat | Status | Eligible teammate deliveries | Decode rate | Capacity excluded |',
                      '| --- | --- | --- | --- | --- | --- |'])
        for episode, seat, row in transport_rows:
            counts = row['counts']
            rate = row['eligible_decode_rate']
            shown = 'not exercised' if rate is None else f'{rate:.2%}'
            lines.append(f"| {episode} | {seat} | {row['status']} | "
                         f"{counts.get('eligible_teammate_deliveries', 0)} | {shown} | "
                         f"{counts.get('teammate_capacity_excluded', 0)} |")
    lines.extend(['', '## Limitations', '',
                  'A successful command means the audit ran, not that checks passed. Missing declarations are not proof.',
                  'Conditional Results require full belief and execution evidence. Raw outcomes do not satisfy that requirement.',
                  'Belief checks cover scheduled samples only. They do not prove correctness between samples.', '', '## Reasons', ''])
    for row in result['checks']:
        if row['reasons']:
            lines.append(f"- {row['id']}: {json.dumps(row['reasons'], sort_keys=True)}")
    return '\n'.join(lines) + '\n'


def run(args, report):
    path, version, mapping, strategy = load_build(args.build)
    evidence = Evidence(mapping)
    selected = select_checks(evidence, args.check, args.level)
    roots = [Path(root).resolve() for root in args.roots]
    key = hashlib.sha256(json.dumps([list(map(str, roots)), args.build, args.check, args.level]).encode()).hexdigest()[:12]
    out = (args.out or builds.LAB / 'analysis/strategy_audit' / f'{args.build}-{key}').resolve()
    if out == builds.BUILDS or builds.BUILDS in out.parents:
        raise pw_cli.UsageError('audit outputs cannot modify immutable compiled builds')
    uploads_path = builds.BUILDS / 'uploads.jsonl'
    uploads = [json.loads(line) for line in uploads_path.read_text().splitlines() if line.strip()] if uploads_path.exists() else []
    # Read sequentially: full dense episodes are memory-heavy; do not load the batch at once.
    sources = pe.discover(roots)
    binary = pe.resolve_binary(version['engine']['tag'], None)
    episodes, raw = [], []
    provenance_paths = [path / 'version.json', path / 'map.json', path / 'policy.bas']
    provenance_paths.extend(sorted(Path(__file__).parent.glob('strategy_audit*.py')))
    provenance_paths.extend(Path(__file__).parent / name for name in
                            ('pw_intent.py', 'pw_episodes.py', 'strategy_basic.py', 'strategy_format.py', 'pw_scout.py'))
    inputs = [{'path': str(p), 'sha256': builds.digest(p)} for p in provenance_paths]
    if uploads_path.exists():
        inputs.append({'path': str(uploads_path), 'sha256': builds.digest(uploads_path)})
    from strategy_audit_baseline import audit_seat
    reference_path = builds.BUILDS / BASELINE
    builds.checked_build(reference_path)
    reference = builds.read(reference_path / 'map.json')
    runtime_contracts = []
    for build_id in RUNTIME_BASELINES:
        runtime_path = builds.BUILDS / build_id
        builds.checked_build(runtime_path)
        runtime_contracts.append(builds.read(runtime_path / 'map.json')['runtime'])
    foundation = runtime_contracts[-1]
    runtime_contracts.append({'files': {**foundation['files'], 'runtime.comms': COMMS_RUNTIME_SHA256}})
    for source in sources:
        try:
            ep = pe.load_episode(source, binary, tag=version['engine']['tag'],
                                 options=pe.TraceOptions(state_every=1, vis_every=1), refresh=args.refresh)
            seats = candidate_seats(ep, version, uploads)
            check_engine_contract(version['engine'], ep.meta['rules'])
            if not ep.summary['verified']:
                raise ValueError('replay hash verification failed')
            states = {(int(r['t']), int(r['seat'])): r for r in ep['states'].to_dict('records')}
            paths = [p for p in (source.replay, source.local_meta, source.episode_json, source.results_json,
                                 ep.source.cache / 'receipt.json') if p and p.exists()]
            summaries = []
            seat_data = {}
            for seat in seats:
                events, issues, log_paths = read_log(ep.source.log_dir, seat, mapping)
                paths.extend(log_paths)
                runtime = reconstruct(strategy, mapping, events, states, seat, ep.summary['ticks'])
                issues.extend(runtime['issues'])
                if mapping['runtime'] not in runtime_contracts:
                    issues.append({'reason': 'runtime_model_mismatch', 't': None})
                status_path = ep.source.log_dir / 'status.json'
                if status_path.exists():
                    paths.append(status_path)
                    status = builds.read(status_path)
                    if any(row.get('slot') == seat and row.get('exit_code') not in (None, 0)
                           for row in status.get('players', [])):
                        issues.append({'reason': 'seat_disabled', 't': None})
                seat_data[seat] = (events, issues, runtime)
            seat_logs = {seat: (data[0], data[1]) for seat, data in seat_data.items()}
            for seat, (events, issues, runtime) in seat_data.items():
                # A malformed stream cannot establish unchanged fields between its lines.
                raw.extend(audit_seat(ep, seat, states, events, runtime, issues, strategy, mapping, reference, evidence))
                transport = None
                if mapping.get('comms'):
                    from strategy_comms import audit_transport
                    from strategy_audit_comms import audit_seat as audit_comms
                    audit_comms(ep, seat, states, events, issues, strategy, mapping, evidence, seat_logs)
                    transport = audit_transport(
                        ep['shouts'].to_dict('records'), events, seat,
                        {t for (t, who), state in states.items() if who == seat and state['hp'] > 0
                         and t < ep.summary['ticks']},
                        tuple(mapping['comms']['keys']))
                    if issues:
                        transport['status'] = 'unmeasurable'
                        transport['reason'] = 'incomplete_or_unqualified_execution_evidence'
                runtime_counts = Counter(row['status'] for row in runtime['ticks'])
                summaries.append({'runtime_status': 'unmeasurable' if issues else ('fail' if runtime_counts['fail'] else 'pass'),
                                  'runtime_counts': dict(runtime_counts), 'seat': seat, 'lines': len(events), 'issues': issues,
                                  'decision_ticks': len(runtime['ticks']), 'activations': len(runtime['windows']),
                                  'communication_transport': transport})
            inputs.extend({'path': str(p), 'sha256': builds.digest(p)} for p in sorted(set(paths)))
            episodes.append({'id': ep.episode_id, 'engine': ep.meta['engine_release'], 'rules': ep.meta['rules'],
                             'verified': True, 'ticks': ep.summary['ticks'], 'seats': summaries,
                             'checks': evidence.summaries(selected, ep.episode_id)})
            report.counts['processed'] += 1
        except (pe.EpisodeError, ValueError, OSError, KeyError) as error:
            report.fail(str(source.replay), 'episode_evidence', str(error))
    checks = evidence.summaries(selected)
    all_checks = evidence.summaries(list(evidence.checks))
    matrix = {component: {level: [{'id': row['id'], 'status': row['status']} for row in all_checks if row['component'] == component and row['level'] == level]
                         or 'not_declared' for level in LEVELS} for component in mapping['components']
              if mapping['components'][component]['kind'] != 'P'}
    result = {'schema': 'pw-strategy-audit/1', 'build_id': args.build, 'source_commit': version['source_commit'],
              'policy_sha256': version['policy_sha256'], 'engine': version['engine'],
              'input_failures': report.failures, 'checks': checks, 'levels': matrix, 'episodes': episodes, 'inputs': inputs,
              'raw_outcomes': raw, 'findings': [row for row in all_checks if row['status'] in ('fail', 'unmeasurable')], 'full_execution_required': RESULT_REQUIRES_FULL_EXECUTION,
              'statuses': dict(Counter(row['status'] for row in checks))}
    out.mkdir(parents=True, exist_ok=True)
    result = pw_cli.jsonable(result)
    builds.dump(out / 'audit.json', result)
    (out / 'report.md').write_text(report_markdown(result))
    with (out / 'evidence.jsonl').open('w') as stream:
        for row in evidence.rows:
            if row['check'] in selected:
                stream.write(json.dumps(pw_cli.jsonable(row), sort_keys=True) + '\n')
    for name in ('audit.json', 'report.md', 'evidence.jsonl'):
        report.output(out / name)
    report.suggest('inspect unmeasurable reasons and raw outcomes; do not infer successful gameplay from exit 0')
    return result
