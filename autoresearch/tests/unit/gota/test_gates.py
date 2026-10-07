"""Shared gates use current engine compilation and measured decisions."""
import importlib
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


def test_real_compile_baseline_and_invalid_syntax(context, tmp_path):
    assert gate('g1_lint').run(BASELINE, context).ok
    invalid = tmp_path / 'invalid.bas'
    invalid.write_text('IF THEN\n')
    assert not gate('g1_lint').run(invalid, context).ok


def test_original_is_expected_live_compile_rejection(context):
    result = gate('g1_lint').run(BASELINE.with_name('submitted-v13.bas'), context)
    assert not result.ok
    assert "'is'" in str(result.details)


def test_measured_budget_baseline_and_33rd_array(context, tmp_path):
    result = gate('g3_budget').run(BASELINE, context)
    assert result.ok
    assert result.details['native_mode'] == {
        'drafting': True, 'draft_mode': 'UniqueDraft', 'replay_mode': False}
    invalid = tmp_path / 'arrays.bas'
    invalid.write_text('\n'.join(f'DIM a{i}(0)' for i in range(33)))
    assert not gate('g3_budget').run(invalid, context).ok


def test_dynamic_loop_rejected(context, tmp_path):
    invalid = tmp_path / 'loop.bas'
    invalid.write_text('i = 0\nWHILE i < 30000\ni = i + 1\nWEND\n')
    result = gate('g3_budget').run(invalid, context)
    assert not result.ok


def test_print_accounting_real_engine(context, tmp_path):
    policy = tmp_path / 'print.bas'
    policy.write_text('PRINT "X", 12\n')
    result = gate('g3_budget').run(policy, context)
    assert result.ok
    assert result.details['runtime'][0]['print_events'] == 4
    assert result.details['runtime'][0]['print_bytes'] == 5


def test_print_limit_rejected(context, tmp_path):
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
def test_hard_budget_violations(context, tmp_path, source):
    artifact = tmp_path / 'violation.bas'
    artifact.write_text(source)
    assert not gate('g3_budget').run(artifact, context).ok


def test_telemetry_artifact_identity_mismatch(context, recorded_episode):
    context.episode_dir = recorded_episode
    assert gate('g5_telemetry').run(BASELINE, context).ok
    (recorded_episode / 'players/7/file').write_text('PRINT \"different\"\n')
    result = gate('g5_telemetry').run(BASELINE, context)
    assert not result.ok
    assert 'Artifact identity mismatch at seat 7' in result.details['error']


def test_screen_rejects_incomplete_metrics():
    with pytest.raises(ValueError, match='Incomplete'):
        gate('g4_screen').metrics({'ticks': 1, 'scores': [], 'total_xp': []}, range(5))


@pytest.mark.parametrize(('statement', 'events', 'size'), [
    ('PRINT "X"; 12;', 2, 3),
    ('PRINT', 1, 1),
    ('PRINT "X", 12', 4, 5),
])
def test_print_forms_use_actual_callback_counts(context, tmp_path, statement, events, size):
    artifact = tmp_path / 'print-form.bas'
    artifact.write_text(statement + '\n')
    result = gate('g3_budget').run(artifact, context)
    assert result.ok
    assert result.details['runtime'][0]['print_events'] == events
    assert result.details['runtime'][0]['print_bytes'] == size


def test_screen_invalid_artifact_rejected(context, tmp_path):
    context.seeds = tuple(range(1, 11))
    context.max_ticks = 31200
    invalid = tmp_path / 'screen.bas'
    invalid.write_text('IF THEN\n')
    assert not gate('g4_screen').run(invalid, context).ok


def test_unexecuted_seats_never_pass_budget(context):
    context.max_ticks = 1
    assert not gate('g3_budget').run(BASELINE, context).ok


def test_glory_floors_each_winning_hero_before_mean():
    row = {'ticks': 2880, 'scores': [1, 1, 0, 0, 0, 0, 0, 0, 0, 0],
           'total_xp': [1, 3, 100, 100, 100, 0, 0, 0, 0, 0]}
    result = gate('g4_screen').metrics(row, range(5))
    assert result == {'glory': 0.2, 'win_rate': 0.4}
