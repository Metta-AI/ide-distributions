"""Shared gates use current engine compilation and measured decisions."""
import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from games.gota.gates import GateContext

BASELINE = ROOT / 'tests/fixtures/gota/v13-live.bas'


@pytest.fixture
def context(tmp_path):
    return GateContext(work_dir=tmp_path, baseline=BASELINE, seeds=(1,), max_ticks=3000)


def gate(name):
    return importlib.import_module(f'games.gota.gates.{name}')


def test_real_compile_baseline_and_invalid_syntax(prepare_native_tools, context, tmp_path):
    assert gate('g1_lint').run(BASELINE, context).ok
    invalid = tmp_path / 'invalid.bas'
    invalid.write_text('IF THEN\n')
    assert not gate('g1_lint').run(invalid, context).ok


def test_original_is_expected_live_compile_rejection(prepare_native_tools, context):
    result = gate('g1_lint').run(BASELINE.with_name('submitted-v13.bas'), context)
    assert not result.ok
    assert "'is'" in str(result.details)


def test_measured_budget_baseline_and_33rd_array(prepare_native_tools, context, tmp_path):
    result = gate('g3_budget').run(BASELINE, context)
    assert result.ok
    assert result.details['native_mode'] == {
        'drafting': True, 'draft_mode': 'OpenDraft', 'replay_mode': False}
    invalid = tmp_path / 'arrays.bas'
    invalid.write_text('\n'.join(f'DIM a{i}(0)' for i in range(33)))
    assert not gate('g3_budget').run(invalid, context).ok


def test_dynamic_loop_rejected(prepare_native_tools, context, tmp_path):
    invalid = tmp_path / 'loop.bas'
    invalid.write_text('i = 0\nWHILE i < 30000\ni = i + 1\nWEND\n')
    result = gate('g3_budget').run(invalid, context)
    assert not result.ok


def test_print_accounting_real_engine(prepare_native_tools, context, tmp_path):
    policy = tmp_path / 'print.bas'
    policy.write_text('PRINT "X", 12\n')
    result = gate('g3_budget').run(policy, context)
    assert result.ok
    assert result.details['runtime'][0]['print_events'] == 4
    assert result.details['runtime'][0]['print_bytes'] == 5


def test_print_limit_rejected(prepare_native_tools, context, tmp_path):
    invalid = tmp_path / 'print.bas'
    invalid.write_text('i = 0\nWHILE i < 130\nPRINT "X";\ni = i + 1\nWEND\n')
    assert not gate('g3_budget').run(invalid, context).ok


def test_runtime_measurement_missing_never_passes(context):
    context.native_binary = context.work_dir / 'missing'
    assert not gate('g3_budget').run(BASELINE, context).ok


def test_telemetry_malformed_line_rejected(tmp_path):
    policy = tmp_path / 'policy.log'
    policy.write_text('LH 240 1 0 0 2 3 0 0 0 0 50\nLH malformed\n')
    with pytest.raises(ValueError, match='malformed'):
        gate('g5_telemetry').parse_log(policy)


@pytest.fixture
def recorded_episode(tmp_path):
    import shutil

    fixture = ROOT / 'tests/fixtures/gota'
    directory = tmp_path / 'recorded-envelope'
    for slot in range(10):
        seat = directory / 'players' / str(slot)
        seat.mkdir(parents=True)
        shutil.copy(BASELINE, seat / 'file')
        logs = directory / 'logs'
        logs.mkdir(exist_ok=True)
        text = (fixture / 'v13-live-telemetry.log').read_text()
        (logs / f'policy_agent_{slot}.log').write_text(
            text + f'Player slot {slot} completed.\n')
    shutil.copy(fixture / 'v13-live-results.json', directory / 'results.json')
    shutil.copy(fixture / 'v13-live-game.log', directory / 'logs/game.stdout.log')
    return directory


def test_telemetry_real_episode_requires_ten_logs(context, recorded_episode):
    context.episode_dir = recorded_episode
    assert gate('g5_telemetry').run(BASELINE, context).ok
    (recorded_episode / 'logs/policy_agent_7.log').unlink()
    assert not gate('g5_telemetry').run(BASELINE, context).ok


def test_episode_missing_manifest_fails(context):
    assert not gate('g2_episode').run(BASELINE, context).ok


def test_screen_requires_all_ten_seeds(context):
    assert not gate('g4_screen').run(BASELINE, context).ok


@pytest.mark.parametrize('source', [
    '\n'.join(f'a{i} = 0' for i in range(257)),
    'DIM a(4096)\n',
    "'" + 'x' * 65536,
    '\n'.join(f'SUB s{i}()\nEND SUB' for i in range(64)),
    'a=' + '+'.join(['a'] * 21000) + '\n',
    'PRINT "' + 'x' * 1025 + '"\n',
], ids=['globals', 'cells', 'source', 'routines', 'static', 'print-bytes'])
def test_hard_budget_violations(prepare_native_tools, context, tmp_path, source):
    artifact = tmp_path / 'violation.bas'
    artifact.write_text(source)
    assert not gate('g3_budget').run(artifact, context).ok


def test_telemetry_artifact_identity_mismatch(context, recorded_episode):
    context.episode_dir = recorded_episode
    assert gate('g5_telemetry').run(BASELINE, context).ok
    (recorded_episode / 'players/7/file').write_text('PRINT \"different\"\n')
    result = gate('g5_telemetry').run(BASELINE, context)
    assert not result.ok
    assert result.outcome == 'errored'
    assert 'Artifact identity mismatch at seat 7' in result.details['error']['message']


def test_screen_rejects_incomplete_metrics():
    with pytest.raises(ValueError, match='ten finite'):
        gate('g4_screen').metrics({'ticks': 1, 'scores': [], 'total_xp': []}, range(5))


@pytest.mark.parametrize(('statement', 'events', 'size'), [
    ('PRINT "X"; 12;', 2, 3),
    ('PRINT', 1, 1),
    ('PRINT "X", 12', 4, 5),
])
def test_print_forms_use_actual_callback_counts(prepare_native_tools, context, tmp_path, statement, events, size):
    artifact = tmp_path / 'print-form.bas'
    artifact.write_text(statement + '\n')
    result = gate('g3_budget').run(artifact, context)
    assert result.ok
    assert result.details['runtime'][0]['print_events'] == events
    assert result.details['runtime'][0]['print_bytes'] == size


def test_screen_invalid_artifact_rejected(prepare_native_tools, context, tmp_path):
    context.seeds = tuple(range(1, 11))
    context.max_ticks = 31200
    invalid = tmp_path / 'screen.bas'
    invalid.write_text('IF THEN\n')
    assert not gate('g4_screen').run(invalid, context).ok


def test_unexecuted_seats_never_pass_budget(prepare_native_tools, context):
    context.max_ticks = 1
    assert not gate('g3_budget').run(BASELINE, context).ok


def test_glory_floors_each_winning_hero_before_mean():
    row = {'ticks': 2880, 'scores': [1, 1, 0, 0, 0, 0, 0, 0, 0, 0],
           'total_xp': [1, 3, 100, 100, 100, 0, 0, 0, 0, 0]}
    result = gate('g4_screen').metrics(row, range(5))
    assert result == {'glory': 0.2, 'win_rate': 0.4}


# Native G2 (default runner): one ten-seat episode on the pinned match binary, no Docker.
FIXTURES = ROOT / 'tests/fixtures/gota'
NATIVE_CONFIG = {'players': [{'name': f'Baseline {slot}'} for slot in range(10)],
                 'seed': 2026, 'max_ticks': 600, 'spawn_interval_ticks': 480,
                 'map_preset': {'map_size': 116}, 'draft_mode': 'open'}


def write_manifest(path, config):
    path.write_text(json.dumps({'variants': [{'id': 'competition', 'game_config': config}]}))
    return path


@pytest.fixture
def no_docker(monkeypatch, tmp_path):
    """PATH holds only git and nim (no docker, no uv); coworld/docker processes are refused.

    Every process start goes through Popen (subprocess.run included), so the guard wraps it.
    """
    tools = tmp_path / 'path-without-docker'
    tools.mkdir()
    for name in ('git', 'nim'):
        if shutil.which(name):
            (tools / name).symlink_to(shutil.which(name))
    monkeypatch.setenv('PATH', str(tools))
    monkeypatch.delenv('GOTA_G2_RUNNER', raising=False)
    spawned = []

    class Guarded(subprocess.Popen):
        def __init__(self, command, *args, **kwargs):
            spawned.append(command)
            if any(part in ('docker', 'coworld', 'run-episode', 'uv') for part in map(str, command)):
                raise AssertionError(f'native G2 spawned {command}')
            super().__init__(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', Guarded)
    assert shutil.which('docker') is None
    return spawned


@pytest.fixture
def fake_native(monkeypatch, tmp_path, no_docker):
    """Fake the native match binary: writes seat logs and result.json like gota_match."""
    local = gate('g2_episode').gota_local
    monkeypatch.setattr(local, 'binary_identity', lambda commit: {'fingerprint': 'fake-gota-match'})
    monkeypatch.setattr(local, 'binary', lambda commit: tmp_path / 'gota_match-fake')
    monkeypatch.setattr(local, 'binary_path', lambda commit: tmp_path / 'gota_match-fake')
    runner = {'failures': {}, 'config': NATIVE_CONFIG, 'actions': 5000, 'calls': [], 'commits': [],
              'options': []}

    def invoke(command, job_path, *, commit, **options):
        job = json.loads(Path(job_path).read_text())
        runner['calls'].append((command, job))
        runner['commits'].append(commit)
        runner['options'].append(options)
        out = Path(job['out'])
        telemetry = (FIXTURES / 'v13-live-telemetry.log').read_text()
        heroes = []
        for slot in range(10):
            error = runner['failures'].get(slot, '')
            (out / f'seat-{slot}.log').write_text(
                telemetry + (f'\nBASIC error: {error}\n' if error else ''))
            heroes.append({'slot': slot, 'team': slot // 5, 'xp': 100 + slot,
                           'decisions': 620, 'vm_failed': bool(error), 'vm_error': error,
                           'vm_routine': None, 'vm_pc': None,
                           'policy_source_sha1': 'same-source'})
        config = {**runner['config'], 'seed': job['seed'], 'max_ticks': job['ticks'],
                  'draft_mode': job['draft']}
        result = {'outcome': 'time_limit', 'ticks': job['ticks'] + 2400, 'heroes': heroes,
                  'config': config, 'engine': commit, 'actions': runner['actions']}
        (out / 'result.json').write_text(json.dumps(result))
        return result
    monkeypatch.setattr(local, '_invoke', invoke)
    return runner


@pytest.fixture
def native_context(context, tmp_path):
    context.manifest = write_manifest(tmp_path / 'manifest.json', NATIVE_CONFIG)
    return context


def test_native_episode_passes_without_docker(fake_native, no_docker, native_context):
    result = gate('g2_episode').run(BASELINE, native_context)
    assert result.outcome == 'passed', result.details
    assert result.details['runner'] == 'native'
    assert result.details['native_build'] == {'fingerprint': 'fake-gota-match'}
    assert result.details['scripts_active'] == 10
    assert result.details['decisions'] == 6200
    assert result.details['actions'] == 5000
    assert result.details['results']['ticks'] == 3000
    assert no_docker == []  # the fake runner spawns nothing
    [(command, job)] = fake_native['calls']
    assert command == 'match'
    assert job['seats'] == [str(BASELINE.resolve())] * 10
    assert (job['seed'], job['ticks'], job['draft'], job['replay']) == (2026, 600, 'open', True)
    [options] = fake_native['options']
    assert options['timeout'] == native_context.timeout
    assert options['env']['COGAME_LLM'] == 'off'  # no paid LLM requests during G2
    assert options['executable'] == native_context.work_dir / 'gota_match-fake'
    # G5 reads the same native episode's ten seat logs.
    assert native_context.episode_dir == native_context.work_dir / 'episode'
    assert gate('g5_telemetry').run(BASELINE, native_context).ok


def test_native_episode_vm_error_fails(fake_native, native_context):
    fake_native['failures'][3] = 'array index out of range'
    result = gate('g2_episode').run(BASELINE, native_context)
    assert result.outcome == 'failed'
    assert 'seat 3' in result.details['error']
    assert 'array index out of range' in result.details['error']


def test_native_episode_zero_actions_fails(fake_native, native_context):
    fake_native['actions'] = 0
    result = gate('g2_episode').run(BASELINE, native_context)
    assert result.outcome == 'failed'
    assert 'zero recorded actions' in result.details['error']


def test_native_episode_missing_binary_errors(monkeypatch, no_docker, native_context, tmp_path):
    monkeypatch.setattr(gate('g2_episode').gota_local, 'TOOLS', tmp_path / 'no-tools')
    result = gate('g2_episode').run(BASELINE, native_context)
    assert result.outcome == 'errored'
    assert not any('coworld' in map(str, command) for command in no_docker)


def test_native_episode_timeout_kills_match_and_errors(monkeypatch, no_docker, native_context,
                                                       tmp_path):
    """A hung match binary is killed at context.timeout and the gate reports a tool fault."""
    local = gate('g2_episode').gota_local
    hung = tmp_path / 'gota_match-hung'
    hung.write_text('#!/bin/sh\nexec /bin/sleep 60\n')
    hung.chmod(0o755)
    monkeypatch.setattr(local, 'binary_identity', lambda commit: {'fingerprint': 'fake'})
    monkeypatch.setattr(local, 'binary_path', lambda commit: hung)
    native_context.timeout = 1
    result = gate('g2_episode').run(BASELINE, native_context)
    assert result.outcome == 'errored'
    assert result.details['error']['message'] == 'gota_match timed out after 1s'


def test_native_episode_uses_resolved_engine_binary(fake_native, native_context):
    """The context's resolved engine selects the match binary; engine.env is only the default."""
    native_context.engine_commit = 'e' * 40
    result = gate('g2_episode').run(BASELINE, native_context)
    assert result.outcome == 'passed', result.details
    assert result.details['engine'] == 'e' * 40
    assert fake_native['commits'] == ['e' * 40]


def test_native_episode_engine_defaults_to_engine_env(fake_native, native_context):
    pin = gate('g2_episode').gota_local.gota_cli.engine()['POLYWORLD_COMMIT']
    assert gate('g2_episode').run(BASELINE, native_context).details['engine'] == pin
    assert fake_native['commits'] == [pin]


def test_native_episode_rejects_manifest_config_it_cannot_reproduce(fake_native, native_context):
    write_manifest(native_context.manifest, {**NATIVE_CONFIG, 'spawn_interval_ticks': 240})
    result = gate('g2_episode').run(BASELINE, native_context)
    assert result.outcome == 'errored'
    assert 'game_config' in result.details['error']['message']


def test_docker_runner_is_opt_in(monkeypatch, native_context):
    monkeypatch.setenv('GOTA_G2_RUNNER', 'docker')
    commands = []

    def episode(command, **kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(gate('g2_episode').subprocess, 'run', episode)
    result = gate('g2_episode').run(BASELINE, native_context)
    assert result.outcome == 'errored'
    assert commands[0][8:10] == ['coworld', 'run-episode']


def test_unknown_runner_errors(monkeypatch, native_context):
    monkeypatch.setenv('GOTA_G2_RUNNER', 'podman')
    result = gate('g2_episode').run(BASELINE, native_context)
    assert result.outcome == 'errored'
    assert 'GOTA_G2_RUNNER' in result.details['error']['message']


def test_native_episode_real_engine(prepare_native_tools, no_docker, context, tmp_path):
    """A short real ten-seat native episode at the engine.env pin, read back by G2 and G5."""
    from games.gota.gates.limits import POLYWORLD_COMMIT
    from games.gota.tools import native_build

    template = native_build.source_cache(POLYWORLD_COMMIT, native_build.TOOLS) / \
        'coworld/gota/coworld_manifest_template.json'
    manifest = json.loads(template.read_text())
    for variant in manifest['variants']:
        variant['game_config']['max_ticks'] = 600
    context.manifest = tmp_path / 'manifest.json'
    context.manifest.write_text(json.dumps(manifest))
    result = gate('g2_episode').run(BASELINE, context)
    assert result.outcome == 'passed', result.details
    assert result.details['runner'] == 'native'
    assert result.details['actions'] > 0
    assert len(result.details['seat_decisions']) == 10
    assert all(count > 0 for count in result.details['seat_decisions'])
    assert any(Path(command[0]).name.startswith('gota_match-') for command in no_docker)
    assert not any('coworld' in map(str, command) for command in no_docker)
    assert gate('g5_telemetry').run(BASELINE, context).ok
