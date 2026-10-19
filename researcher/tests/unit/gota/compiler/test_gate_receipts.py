"""Native exceptions and incomplete receipts must never become model repair input."""
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'games/gota/compiler/tools'))
import gota_gates
import pw_cli
import pw_strategy
import strategy_build as builds
import test_gate_errors
from test_gate_errors import compile_args

from games.gota.gates import GateContext, GateResult, g1_lint, g3_budget, g4_screen, native

staged = test_gate_errors.staged


@pytest.fixture
def receipt():
    return {'outcome': 'passed', 'ok': True, 'engine': 'a' * 40, 'bassy': 'b' * 40,
            'mode': 'legacy', 'globals': 1, 'arrays': 0, 'array_cells': 0,
            'source_bytes': 4, 'routines': 1, 'static_instructions': 1,
            'runtime': {'instructions': 1, 'work': 1, 'print_events': 0, 'print_bytes': 0,
                        'decisions': 10, 'seat_decisions': [1] * 10, 'errors': [],
                        'ticks': 10, 'scores': [0] * 10, 'total_xp': [0] * 10,
                        'seed': 1, 'candidate_blue': False}}


def install_probe(monkeypatch, receipt):
    from games.gota.tools import native_build
    identity = {'inputs': {'dependencies': {'bassy': {'commit': 'b' * 40}}}}
    monkeypatch.setattr(native_build, 'validate', lambda *a, **kw: identity)
    monkeypatch.setattr(native, 'validate_executable', lambda *a: identity)
    def run(command, **kw):
        data = deepcopy(receipt)
        if command[1] == 'compile':
            data.pop('runtime', None)
        Path(command[3]).write_text(json.dumps(data))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(native.subprocess, 'run', run)
    return identity


@pytest.mark.parametrize('gate_name', ['g1', 'g3'])
def test_native_tool_error_keeps_native_type_and_traceback(tmp_path, monkeypatch, gate_name):
    error = {'type': 'ValueError', 'message': 'native seed parser broke',
             'traceback': 'gate_probe.nim(28) parseInt'}
    install_probe(monkeypatch, {'outcome': 'errored', 'ok': False,
                               'engine': 'a' * 40, 'bassy': 'b' * 40, 'error': error})
    artifact = tmp_path / 'policy.bas'
    artifact.write_text('x=1')
    context = GateContext(tmp_path, native_binary=tmp_path / 'probe', engine_commit='a' * 40)
    result = (g1_lint if gate_name == 'g1' else g3_budget).run(artifact, context)
    assert result.outcome == 'errored'
    assert result.details['error'] == error


@pytest.mark.parametrize('mutation', ['globals', 'runtime', 'work', 'seats', 'scores',
                                     'counter_type', 'negative_counter', 'missing_outcome',
                                     'success_without_activity', 'tool_exception', 'unexplained_failure'])
def test_incomplete_native_receipt_stops_model_and_later_gates(staged, monkeypatch, receipt, mutation):
    _order, _stage, source = staged
    if mutation == 'globals': receipt.pop('globals')
    elif mutation == 'runtime': receipt.pop('runtime')
    elif mutation == 'work': receipt['runtime'].pop('work')
    elif mutation == 'seats': receipt['runtime']['seat_decisions'] = [1]
    elif mutation == 'scores': receipt['runtime']['scores'] = []
    elif mutation == 'counter_type': receipt['runtime']['instructions'] = 'one'
    elif mutation == 'negative_counter': receipt['runtime']['work'] = -1
    elif mutation == 'missing_outcome': receipt.pop('outcome')
    elif mutation == 'unexplained_failure':
        receipt.update(outcome='failed', ok=False)
    elif mutation == 'tool_exception':
        receipt.clear()
        receipt.update(outcome='errored', ok=False, engine='a' * 40, bassy='b' * 40,
                       error={'type': 'OSError', 'message': 'native harness fault',
                              'traceback': 'gate_probe.nim native fault'})
    elif mutation == 'success_without_activity':
        receipt['runtime']['decisions'] = 0
        receipt['runtime']['seat_decisions'] = [0] * 10
    install_probe(monkeypatch, receipt)
    calls, generations = [], []
    monkeypatch.setattr(builds, 'generate', lambda order, *, errors, **kw: generations.append(errors))
    monkeypatch.setattr(gota_gates.g2_episode, 'run', lambda *a: GateResult('passed', {}))
    monkeypatch.setattr(gota_gates.g4_screen, 'run', lambda *a: calls.append('G4'))
    result = pw_strategy.run(compile_args(source), pw_cli.Report('test', json_mode=True))
    assert generations == [[]]
    assert calls == []
    assert result['status'] == 'errored'
    assert result['error']['type'] in ('ValueError', 'KeyError', 'TypeError', 'OSError')


@pytest.mark.parametrize('field,value', [('scores', []), ('total_xp', [0] * 9),
                                        ('ticks', 0), ('scores', [float('nan')] * 10)])
def test_malformed_screen_measurement_is_tool_error(tmp_path, monkeypatch, field, value):
    artifact = tmp_path / 'policy.bas'
    artifact.write_text('x=1')
    row = {'ticks': 10, 'scores': [0] * 10, 'total_xp': [0] * 10, 'decisions': 10}
    row[field] = value
    monkeypatch.setattr(g4_screen, 'measure_many', lambda a, c, matches: [{'outcome': 'passed', 'ok': True, 'runtime': row} for _ in matches])
    result = g4_screen.run(artifact, GateContext(tmp_path, baseline=artifact))
    assert result.outcome == 'errored'


@pytest.fixture
def episode(staged):
    _order, stage, _source = staged
    directory = stage / 'fake-episode'
    for slot in range(10):
        player = directory / 'players' / str(slot)
        player.mkdir(parents=True)
        (player / 'file').write_bytes((stage / 'policy.bas').read_bytes())
        logs = directory / 'logs'
        logs.mkdir(exist_ok=True)
        (logs / f'policy_agent_{slot}.log').write_text(f'Player slot {slot} completed.\n')
    (directory / 'logs/game.stdout.log').write_text(
        'scripts: 10/10 active, 10 decisions\nreplay saved: fixture (10 actions)\n')
    builds.dump(directory / 'results.json', {'ticks': 10, 'scores': [0] * 10, 'total_xp': [0] * 10})
    return directory


@pytest.mark.parametrize('mutation', ['results', 'ticks', 'scores', 'scripts_marker',
                                     'actions_marker', 'seat_completion'])
def test_incomplete_episode_stops_model_and_later_gates(staged, episode, monkeypatch, mutation):
    _order, _stage, source = staged
    if mutation == 'results': builds.dump(episode / 'results.json', {})
    elif mutation in ('ticks', 'scores'):
        result = builds.read(episode / 'results.json')
        result[mutation] = 0 if mutation == 'ticks' else [float('inf')] * 10
        builds.dump(episode / 'results.json', result)
    elif mutation == 'seat_completion': (episode / 'logs/policy_agent_2.log').write_text('truncated')
    else:
        text = 'replay saved: fixture (10 actions)\n' if mutation == 'scripts_marker' else 'scripts: 10/10 active, 10 decisions\n'
        (episode / 'logs/game.stdout.log').write_text(text)
    monkeypatch.setattr(gota_gates.g1_lint, 'run', lambda *a: GateResult('passed', {}))
    def episode_run(artifact, context):
        from games.gota.gates import gate_run
        return gate_run(lambda: GateResult('passed', gota_gates.g2_episode.inspect_episode(artifact, episode)))()
    monkeypatch.setattr(gota_gates.g2_episode, 'run', episode_run)
    monkeypatch.setattr(gota_gates.g3_budget, 'run', lambda *a: pytest.fail('later gate ran'))
    generations = []
    monkeypatch.setattr(builds, 'generate', lambda order, *, errors, **kw: generations.append(errors))
    result = pw_strategy.run(compile_args(source), pw_cli.Report('test', json_mode=True))
    assert generations == [[]]
    assert result['status'] == 'errored'


@pytest.mark.parametrize('marker', ['scripts: 0/10 active, 0 decisions', 'replay saved: fixture (0 actions)'])
def test_complete_zero_activity_episode_is_policy_failure(staged, episode, marker):
    from games.gota.gates import PolicyFailure
    _order, stage, _source = staged
    text = (episode / 'logs/game.stdout.log').read_text()
    text = text.replace('scripts: 10/10 active, 10 decisions', marker) if marker.startswith('scripts') else text.replace('replay saved: fixture (10 actions)', marker)
    (episode / 'logs/game.stdout.log').write_text(text)
    with pytest.raises(PolicyFailure):
        gota_gates.g2_episode.inspect_episode(stage / 'policy.bas', episode)


def test_real_native_basic_rejection_and_harness_exception_are_distinct(prepare_native_tools, tmp_path):
    from games.gota.gates.limits import POLYWORLD_COMMIT
    artifact = tmp_path / 'policy.bas'
    artifact.write_text('IF THEN\n')
    context = GateContext(tmp_path / 'native', native_binary=native.TOOLS / 'bin/gate_probe-801749e5',
                          engine_commit=POLYWORLD_COMMIT, seeds=(1,), max_ticks=100)
    result = g1_lint.run(artifact, context)
    assert result.outcome == 'failed'
    assert result.details['error']['type'] == 'BasicError'
    artifact.write_text('x = 1\n')
    context.seeds = ('bad-seed',)
    result = g3_budget.run(artifact, context)
    assert result.outcome == 'errored'
    assert result.details['error']['type'] == 'ValueError'
    assert 'gate_probe.nim' in result.details['error']['traceback']
    assert 'invalid integer' in result.details['error']['message']


@pytest.mark.parametrize('vm_error', [False, True])
def test_complete_zero_execution_native_receipt_is_policy_failure(tmp_path, monkeypatch, receipt, vm_error):
    receipt['ok'] = False
    receipt['outcome'] = 'failed'
    receipt['runtime']['decisions'] = 0
    receipt['runtime']['seat_decisions'] = [0] * 10
    receipt['runtime']['errors'] = [{'slot': 0, 'error': 'division by zero'}] if vm_error else []
    native.validate_measurement(receipt, simulated=True)
    monkeypatch.setattr(g3_budget, 'measure', lambda *a:
                        {'outcome': 'passed', **{k: v for k, v in receipt.items()
                                               if k not in ('outcome', 'ok', 'runtime')}, 'ok': True}
                        if len(a) == 2 else receipt)
    monkeypatch.setattr(g3_budget, 'measure_many', lambda *a: [receipt])
    result = g3_budget.run(tmp_path / 'policy.bas', GateContext(tmp_path, seeds=(1,)))
    assert result.outcome == 'failed'


def test_incomplete_native_screen_stops_model_and_telemetry(staged, monkeypatch, receipt):
    _order, _stage, source = staged
    receipt['runtime'].pop('scores')
    install_probe(monkeypatch, receipt)
    for module in (gota_gates.g1_lint, gota_gates.g2_episode, gota_gates.g3_budget):
        monkeypatch.setattr(module, 'run', lambda *a: GateResult('passed', {}))
    monkeypatch.setattr(gota_gates.g5_telemetry, 'run', lambda *a: pytest.fail('later gate ran'))
    generations = []
    monkeypatch.setattr(builds, 'generate', lambda order, *, errors, **kw: generations.append(errors))
    result = pw_strategy.run(compile_args(source), pw_cli.Report('test', json_mode=True))
    assert generations == [[]]
    assert result['status'] == 'errored'
    assert result['gates']['G4']['outcome'] == 'errored'
