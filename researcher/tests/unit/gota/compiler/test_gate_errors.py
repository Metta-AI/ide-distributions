"""Broken gate tooling stops generation and retains evidence instead of repairing policy."""
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'games/gota/compiler/tools'))
import gota_gates
import pw_cli
import pw_strategy
import strategy_build as builds

from games.gota.gates import GateContext, GateResult


@pytest.fixture
def staged(tmp_path, monkeypatch):
    monkeypatch.setattr(builds, 'STAGING', tmp_path / 'staging')
    monkeypatch.setattr(builds, 'BUILDS', tmp_path / 'builds')
    builds.BUILDS.mkdir()
    order = {'build_id': 'abcdef12-1', 'source_commit': 'abcdef12',
             'source_path': 'STRATEGY.md', 'source_hashes': {}, 'previous_build': None,
             'compiler': {}, 'engine': {'commit': 'a' * 40}, 'created': 'now',
             'intent': 'behavior_change', 'units': {}, 'components': {}}
    stage = builds.STAGING / order['build_id']
    stage.mkdir(parents=True)
    (stage / 'units').mkdir()
    (stage / 'policy.bas').write_text('x = 1\n')
    builds.dump(stage / 'map.json', {})
    builds.dump(stage / 'work_order.json', order)
    source = tmp_path / 'STRATEGY.md'
    source.write_text('mocked strategy')
    monkeypatch.setattr(builds, 'assert_inputs', lambda order: None)
    monkeypatch.setattr(builds, 'prepare', lambda *a, **kw: order)
    monkeypatch.setattr(builds, 'assemble_build', lambda *a: None)
    monkeypatch.setattr(gota_gates, 'cached_probe', lambda *a: tmp_path / 'probe')
    import strategy_format
    monkeypatch.setattr(strategy_format, 'parse_strategy', lambda *a, **kw: None)
    monkeypatch.setattr(strategy_format, 'lint_strategy', lambda *a: [])
    from games.gota.tools import native_build
    monkeypatch.setattr(native_build, 'validate', lambda *a: {})
    return order, stage, source


def compile_args(source):
    return SimpleNamespace(action='compile', full_evaluation=True, source=source, agent_timeout=1, agent_budget=None,
                           agent='hermes', model=None, full=False, milestone=None,
                           previous=None, manifest=source)


@pytest.mark.parametrize('name', ['g1_lint', 'g2_episode', 'g3_budget', 'g4_screen', 'g5_telemetry'])
@pytest.mark.parametrize('exception', [ValueError('path bug'), KeyError('missing field'),
                                      RuntimeError('tool broke'), ImportError('missing reader')])
def test_shared_gate_unexpected_exception_is_errored(tmp_path, monkeypatch, name, exception):
    module = importlib.import_module(f'games.gota.gates.{name}')
    artifact = tmp_path / 'policy.bas'
    artifact.write_text('x=1')
    context = GateContext(tmp_path, baseline=artifact, manifest=tmp_path / 'manifest',
                          episode_dir=tmp_path)
    def broken(*a, **kw):
        raise exception
    if name in ('g1_lint', 'g3_budget', 'g4_screen'):
        monkeypatch.setattr(module, 'measure_many' if name == 'g4_screen' else 'measure', broken)
    elif name == 'g2_episode':
        monkeypatch.setenv('GOTA_G2_RUNNER', 'docker')
        monkeypatch.setattr(module.subprocess, 'run', broken)
    else:
        monkeypatch.setattr(module, 'inspect_episode', broken)
    result = module.run(artifact, context)
    assert result.outcome == 'errored'
    assert not result.ok
    assert result.details['error']['type'] == type(exception).__name__
    assert result.details['error']['message'] == str(exception)
    assert 'broken' in result.details['error']['traceback']


@pytest.mark.parametrize('failed_gate', ['G1', 'G2', 'G3', 'G4', 'G5'])
def test_raw_gate_exception_stops_all_later_gates_and_model_rounds(staged, monkeypatch, failed_gate):
    _order, _stage, source = staged
    calls, generations = [], []
    monkeypatch.setattr(builds, 'generate', lambda order, *, errors, **kw: generations.append(errors))
    for name, module in zip(('G1', 'G2', 'G3', 'G4', 'G5'),
                            (gota_gates.g1_lint, gota_gates.g2_episode, gota_gates.g3_budget,
                             gota_gates.g4_screen, gota_gates.g5_telemetry)):
        def run(*a, name=name):
            calls.append(name)
            if name == failed_gate:
                raise ValueError('Python path bug')
            return GateResult('passed', {})
        monkeypatch.setattr(module, 'run', run)
    result = pw_strategy.run(compile_args(source), pw_cli.Report('test', json_mode=True))
    assert generations == [[]]
    assert calls == [f'G{i}' for i in range(1, int(failed_gate[1:]) + 1)]
    target = Path(result['build'])
    assert result['status'] == 'errored'
    assert builds.read(target / 'version.json')['status'] == 'errored'
    assert builds.read(target / 'gates.json')[failed_gate]['outcome'] == 'errored'
    assert result['gates'][failed_gate]['summary']['error']['type'] == 'ValueError'
    assert 'Python path bug' in (target / 'report.md').read_text()
    assert 'test_gate_errors.py' in (target / 'report.md').read_text()


@pytest.mark.parametrize('where', ['generate', 'assemble', 'verify'])
@pytest.mark.parametrize('exception', [ValueError('unexpected value'), RuntimeError('environment broke'),
                                      KeyError('schema'), ImportError('missing harness')])
def test_harness_exceptions_never_feed_repairs(staged, monkeypatch, where, exception):
    _order, _stage, source = staged
    generations = []
    def generate(order, *, errors, **kw):
        generations.append(errors)
        if where == 'generate':
            raise exception
    def broken(*a, **kw):
        raise exception
    monkeypatch.setattr(builds, 'generate', generate)
    if where == 'assemble':
        monkeypatch.setattr(builds, 'assemble_build', broken)
    elif where == 'verify':
        monkeypatch.setattr(gota_gates, 'verify', broken)
    result = pw_strategy.run(compile_args(source), pw_cli.Report('test', json_mode=True))
    assert generations == [[]]
    assert result['status'] == 'errored'
    assert result['error']['type'] == type(exception).__name__
    assert result['error']['traceback']


def test_policy_failure_can_feed_repair_and_pass(staged, monkeypatch):
    _order, _stage, source = staged
    generations = []
    monkeypatch.setattr(builds, 'generate', lambda order, *, errors, **kw: generations.append(errors))
    def verify(*a, **kw):
        return {f'G{i}': {'outcome': 'failed' if i == 3 and len(generations) == 1 else 'passed',
                          'passed': not (i == 3 and len(generations) == 1), 'summary': {}}
                for i in range(1, 6)}
    monkeypatch.setattr(gota_gates, 'verify', verify)
    result = pw_strategy.run(compile_args(source), pw_cli.Report('test', json_mode=True))
    assert result['status'] == 'passed'
    assert generations[0] == []
    assert [row['gate'] for row in generations[1]] == ['G3']
    assert all(row['outcome'] == 'failed' for row in generations[1])


def test_generation_budget_shortens_repairs_and_excludes_gates(staged, monkeypatch):
    _order, _stage, source = staged
    now, timeouts = [0.0], []
    durations = iter([800, 500, 100])
    monkeypatch.setattr(pw_strategy.time, 'monotonic', lambda: now[0])
    def generate(order, *, errors, timeout):
        timeouts.append(timeout)
        now[0] += next(durations)
    monkeypatch.setattr(builds, 'generate', generate)
    def verify(*a, **kw):
        now[0] += 3300
        return {f'G{i}': {'outcome': 'failed' if i == 1 and len(timeouts) < 3 else 'passed',
                          'passed': not (i == 1 and len(timeouts) < 3), 'summary': {}}
                for i in range(1, 6)}
    monkeypatch.setattr(gota_gates, 'verify', verify)
    args = compile_args(source)
    args.agent_timeout, args.agent_budget = 1200, 1800
    result = pw_strategy.run(args, pw_cli.Report('test', json_mode=True))
    assert result['status'] == 'passed'
    assert timeouts == [1200, 1000, 500]


@pytest.mark.parametrize('durations', [[1800], [1000, 800]])
def test_generation_budget_exhaustion_stops_before_next_round(staged, monkeypatch, durations):
    _order, _stage, source = staged
    now, timeouts, gates = [0.0], [], []
    elapsed = iter(durations)
    monkeypatch.setattr(pw_strategy.time, 'monotonic', lambda: now[0])
    def generate(order, *, errors, timeout):
        timeouts.append(timeout)
        now[0] += next(elapsed)
    monkeypatch.setattr(builds, 'generate', generate)
    def verify(*a, **kw):
        gates.append(True)
        return {'G1': {'outcome': 'failed', 'passed': False, 'summary': {}}}
    monkeypatch.setattr(gota_gates, 'verify', verify)
    args = compile_args(source)
    args.agent_timeout, args.agent_budget = 3600, 1800
    result = pw_strategy.run(args, pw_cli.Report('test', json_mode=True))
    assert timeouts == ([1800] if len(durations) == 1 else [1800, 800])
    assert len(gates) == len(durations)
    assert result['status'] == 'errored'
    assert 'generation budget of 1800s exhausted' in result['error']['message']
    target = Path(result['build'])
    assert builds.read(target / 'version.json')['status'] == 'errored'
    assert 'generation budget of 1800s exhausted' in (target / 'report.md').read_text()


@pytest.mark.parametrize('durations', [[1800], [600, 600, 600]])
def test_structural_generation_error_cannot_repair_after_budget_exhaustion(
        staged, monkeypatch, durations):
    from strategy_basic import BuildError
    from strategy_format import Diagnostic

    _order, _stage, source = staged
    now, timeouts = [0.0], []
    elapsed = iter(durations)
    monkeypatch.setattr(pw_strategy.time, 'monotonic', lambda: now[0])
    def generate(order, *, errors, timeout):
        timeouts.append(timeout)
        now[0] += next(elapsed)
        raise BuildError([Diagnostic('error', 'missing_unit', 'unit missing', 'C.idle')])
    monkeypatch.setattr(builds, 'generate', generate)
    monkeypatch.setattr(builds, 'assemble_build',
                        lambda *a: pytest.fail('exhausted generation reached assembly'))
    monkeypatch.setattr(gota_gates, 'verify',
                        lambda *a, **kw: pytest.fail('exhausted generation reached gates'))
    args = compile_args(source)
    args.agent_timeout, args.agent_budget = 3600, 1800
    result = pw_strategy.run(args, pw_cli.Report('test', json_mode=True))
    assert timeouts == ([1800] if len(durations) == 1 else [1800, 1200, 600])
    if len(durations) == 1:
        assert result['status'] == 'errored'
        assert result['error']['type'] == 'RuntimeError'
        assert 'generation budget of 1800s exhausted' in result['error']['message']
    else:
        assert result['status'] == 'failed'
        assert 'unit missing' in result['error']
    assert result['gates'] == {}


@pytest.mark.parametrize('elapsed', [1800, 1801])
def test_completed_generation_at_budget_proceeds_to_gates(staged, monkeypatch, elapsed):
    _order, _stage, source = staged
    now, timeouts, gates = [0.0], [], []
    monkeypatch.setattr(pw_strategy.time, 'monotonic', lambda: now[0])
    def generate(order, *, errors, timeout):
        timeouts.append(timeout)
        now[0] += elapsed
    monkeypatch.setattr(builds, 'generate', generate)
    def verify(*a, **kw):
        gates.append(True)
        return {f'G{i}': {'outcome': 'passed', 'passed': True, 'summary': {}}
                for i in range(1, 6)}
    monkeypatch.setattr(gota_gates, 'verify', verify)
    args = compile_args(source)
    args.agent_timeout, args.agent_budget = 1800, 1800
    result = pw_strategy.run(args, pw_cli.Report('test', json_mode=True))
    assert timeouts == [1800]
    assert gates == [True]
    assert result['status'] == 'passed'


@pytest.mark.parametrize('budget', [0, -1])
def test_invalid_generation_budget_stops_before_prepare(staged, monkeypatch, budget):
    _order, _stage, source = staged
    monkeypatch.setattr(builds, 'prepare', lambda *a, **kw: pytest.fail('invalid budget prepared'))
    args = compile_args(source)
    args.agent_budget = budget
    with pytest.raises(pw_cli.UsageError, match='--agent-budget must be positive'):
        pw_strategy.run(args, pw_cli.Report('test', json_mode=True))


def test_standalone_generation_budget_is_optional():
    args = pw_strategy.parser().parse_args(['compile', '--agent-timeout', '3600',
                                          '--manifest', '/fake/manifest'])
    assert args.agent_timeout == 3600
    assert args.agent_budget is None


def test_import_time_fault_retains_build_cli_report(tmp_path, monkeypatch, capsys):
    from games.gota.tools import build as binding
    monkeypatch.setattr(sys, 'argv', ['build', str(tmp_path), '--out', str(tmp_path / 'out'), '--json'])
    def broken(*a, **kw):
        raise ImportError('cannot import gate reader')
    monkeypatch.setattr(binding, 'build', broken)
    assert binding.main() == 3
    output = json.loads(capsys.readouterr().out)
    assert output['status'] == 'errored'
    assert output['error']['type'] == 'ImportError'
    assert json.loads((tmp_path / 'out/report.json').read_text()) == output
    assert json.loads((tmp_path / 'out/version.json').read_text())['status'] == 'errored'


@pytest.mark.parametrize('where', ['gate', 'harness', 'import'])
def test_local_check_retains_fault_and_stops(tmp_path, monkeypatch, capsys, where):
    from games.gota.gates import __main__ as check
    from games.gota.tools import engine_identity
    (tmp_path / 'policy.bas').write_text('x=1')
    monkeypatch.setattr(sys, 'argv', ['check', str(tmp_path / 'policy.bas'),
                                     '--work-dir', str(tmp_path / 'check')])
    calls = []
    def resolve(*a, **kw):
        if where == 'harness':
            raise KeyError('host contract')
        return {'commit': 'a' * 40, 'manifest': {}}
    monkeypatch.setattr(engine_identity, 'resolve', resolve)
    monkeypatch.setattr(engine_identity, 'provision', lambda identity: (identity, None, {}))
    def load(name):
        calls.append(name)
        if where == 'import':
            raise ImportError('reader dependency')
        def run(*a):
            raise RuntimeError('bad harness')
        return SimpleNamespace(run=run)
    monkeypatch.setattr(check.importlib, 'import_module', load)
    assert check.main() == 3
    result = json.loads((tmp_path / 'check/gates.json').read_text())
    assert len(result) == 1
    failure = next(iter(result.values()))
    assert failure['outcome'] == 'errored'
    assert failure['details']['error']['traceback']
    assert len(calls) == (0 if where == 'harness' else 1)
    assert json.loads(capsys.readouterr().out)['outcome'] == 'errored'


def test_expected_structural_build_error_still_repairs(staged, monkeypatch):
    import strategy_basic
    from strategy_format import Diagnostic
    order, _stage, source = staged
    order['components'] = {'C.idle': {'llm': True, 'fields': {}}}
    order['units'] = {'C.idle': 'new'}
    generations = []
    monkeypatch.setattr(builds, 'generate', lambda order, *, errors, **kw: generations.append(errors))
    def assemble(*a):
        if len(generations) == 1:
            raise strategy_basic.BuildError([Diagnostic('error', 'missing_unit', 'unit missing', 'C.idle')])
    monkeypatch.setattr(builds, 'assemble_build', assemble)
    monkeypatch.setattr(gota_gates, 'verify', lambda *a, **kw:
                        {f'G{i}': {'outcome': 'passed', 'passed': True} for i in range(1, 6)})
    result = pw_strategy.run(compile_args(source), pw_cli.Report('test', json_mode=True))
    assert result['status'] == 'passed'
    assert len(generations) == 2
    assert generations[1][0]['code'] == 'missing_unit'
    assert order['units']['C.idle'] == 'changed'


@pytest.mark.parametrize('action', ['compile', 'verify'])
def test_gate_module_import_error_stops_model_after_first_generation(staged, monkeypatch, action):
    import builtins
    _order, _stage, source = staged
    generations = []
    monkeypatch.setattr(builds, 'generate', lambda order, *, errors, **kw: generations.append(errors))
    real_import = builtins.__import__
    def load(name, *a, **kw):
        if name == 'gota_gates':
            raise ImportError('gate dependency absent')
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, '__import__', load)
    args = compile_args(source) if action == 'compile' else SimpleNamespace(
        action='verify', build='abcdef12-1', manifest=source)
    result = pw_strategy.run(args, pw_cli.Report('test', json_mode=True))
    assert generations == ([[]] if action == 'compile' else [])
    assert result['status'] == 'errored'
    assert result['error']['type'] == 'ImportError'


def test_verification_harness_fault_is_persisted(staged, monkeypatch):
    order, stage, _source = staged
    from games.gota.tools import native_build
    def validate(*a, **kw):
        raise ValueError('receipt changed')
    monkeypatch.setattr(native_build, 'validate', validate)
    gates = gota_gates.verify(order['build_id'])
    assert gates['verification']['outcome'] == 'errored'
    assert builds.read(stage / 'gates.json') == gates
    target = builds.finalize(order, gates)
    assert builds.read(target / 'version.json')['status'] == 'errored'


def test_compact_telemetry_broken_map_is_not_policy_failure(tmp_path, monkeypatch):
    import strategy_comms

    from games.gota.gates.g5_telemetry import compiler_logs
    # A valid compact payload with a broken generated map is a tool error.
    mapping = {'comms': {'version': 1, 'keys': strategy_comms.DEFAULT_KEYS, 'messages': {}}}
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs/policy_agent_0.log').write_text('PWC v=3 t=1 b=1\n')
    monkeypatch.setattr(strategy_comms, 'decode_batch',
                        lambda *a: [(1, SimpleNamespace(kind=1))])
    result = compiler_logs(GateContext(tmp_path, episode_dir=tmp_path, compiler_map=mapping))
    assert result.outcome == 'errored'
    assert result.details['error']['type'] == 'KeyError'

@pytest.mark.parametrize('failed_gate', ['G4', 'G5'])
def test_advisory_policy_failure_passes_without_repair(staged, monkeypatch, failed_gate):
    _order, _stage, source = staged
    generations, calls = [], []
    monkeypatch.setattr(builds, 'generate', lambda order, *, errors, **kw: generations.append(errors))
    for name, module in zip(('G1', 'G2', 'G3', 'G4', 'G5'),
                            (gota_gates.g1_lint, gota_gates.g2_episode, gota_gates.g3_budget,
                             gota_gates.g4_screen, gota_gates.g5_telemetry)):
        def run(*a, name=name):
            calls.append(name)
            return GateResult('failed' if name == failed_gate else 'passed', {})
        monkeypatch.setattr(module, 'run', run)
    result = pw_strategy.run(compile_args(source), pw_cli.Report('test', json_mode=True))
    assert result['status'] == 'passed'
    assert generations == [[]]
    assert calls == ['G1', 'G2', 'G3', 'G4', 'G5']
    assert result['gates'][failed_gate]['advisory'] is True
    assert result['warnings'] == [{'gate': failed_gate, 'summary': {}}]
    assert failed_gate in (Path(result['build']) / 'report.md').read_text()

@pytest.mark.parametrize('failed_gate', ['G1', 'G2', 'G3'])
def test_blocking_policy_failure_remains_failed(staged, monkeypatch, failed_gate):
    _order, _stage, source = staged
    monkeypatch.setattr(builds, 'generate', lambda *a, **kw: None)
    monkeypatch.setattr(gota_gates, 'verify', lambda *a, **kw:
                        {f'G{i}': {'outcome': 'failed' if f'G{i}' == failed_gate else 'passed',
                                  'passed': f'G{i}' != failed_gate, 'summary': {}}
                         for i in range(1, 6)})
    result = pw_strategy.run(compile_args(source), pw_cli.Report('test', json_mode=True))
    assert result['status'] == 'failed'


def test_local_check_advisory_failures_exit_zero(tmp_path, monkeypatch):
    from games.gota.gates import __main__ as check
    from games.gota.tools import engine_identity
    (tmp_path / 'policy.bas').write_text('x=1')
    monkeypatch.setattr(sys, 'argv', ['check', str(tmp_path / 'policy.bas'),
                                     '--work-dir', str(tmp_path / 'check')])
    monkeypatch.setattr(engine_identity, 'resolve', lambda *a, **kw:
                        {'commit': 'a' * 40, 'manifest': {}})
    monkeypatch.setattr(engine_identity, 'provision', lambda identity: (identity, None, {}))
    monkeypatch.setattr(check.importlib, 'import_module', lambda name:
                        SimpleNamespace(run=lambda *a: GateResult(
                            'failed' if name.endswith(('g4_screen', 'g5_telemetry')) else 'passed', {})))
    assert check.main() == 0
    receipt = json.loads((tmp_path / 'check/gates.json').read_text())
    assert receipt['g5_telemetry']['advisory'] is True
    assert receipt['g5_telemetry']['outcome'] == 'failed'


@pytest.mark.parametrize('repair', [True, False])
def test_generated_report_failure_uses_bounded_repair(staged, monkeypatch, repair):
    order, _stage, source = staged
    order['components']['C.idle'] = {'llm': True, 'compiled_text': 'Wait.'}
    order['units']['C.idle'] = 'new'
    generations = []

    def generate(order, *, errors, **kw):
        generations.append(errors)
        quote = 'Wait.' if repair and len(generations) > 1 else 'Invented quote'
        builds.validate_draft({'guesses': [{
            'id': 'G-C.idle-1', 'component': 'C.idle', 'spec_quote': quote,
            'decision': 'wait', 'why': 'test', 'severity': 'low', 'state': 'open',
        }], 'gaps': []}, order)

    monkeypatch.setattr(builds, 'generate', generate)
    monkeypatch.setattr(gota_gates, 'verify', lambda *a, **kw: {
        f'G{i}': {'outcome': 'passed', 'passed': True, 'summary': {}} for i in range(1, 6)
    })
    result = pw_strategy.run(compile_args(source), pw_cli.Report('test', json_mode=True))
    assert result['status'] == ('passed' if repair else 'failed')
    assert len(generations) == (2 if repair else 3)
    assert generations[1][0]['code'] == 'report-draft'
    assert generations[1][0]['component'] == 'C.idle'
    assert 'Invented quote' in generations[1][0]['message']
