"""Cross-tool outcome, draft attribution, and invocation regressions."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'games/gota/tools'))
import compare
import evaluate
import gota_episodes
import lasthit_eval
import miner_rows


def episode(tmp_path, data):
    (tmp_path / 'results.json').write_text(json.dumps(data))
    (tmp_path / 'episode.json').write_text(json.dumps({'id': 'test', 'status': 'completed',
        'participants': [{'position': i, 'policy_name': 'test', 'version': 1} for i in range(10)]}))
    return gota_episodes.load_episode(tmp_path)


def test_real_winner_across_tools(tmp_path):
    data = json.loads((ROOT / 'tests/fixtures/gota/probe_full.results.json').read_text())
    record = episode(tmp_path, data)
    seat = record.seats[3]
    assert data['scores'][3] == 452
    assert seat.won == bool(evaluate.metrics(data, 3)['win_rate'])
    assert compare.seat_value(seat, record, 'win_rate') == 1
    row = miner_rows.seat_row(seat, record, 'win')
    assert row['won'] and row['hero_class'] == 'unknown' and row['role'] == 'unknown'
    with pytest.raises(ValueError, match='verified replay'):
        compare.load_arm(tmp_path, 'test', 1, 'class')


@pytest.mark.parametrize('outcome', ['draw', 'time_limit'])
def test_no_winner_aggregation_and_lasthit(tmp_path, capsys, outcome):
    data = {'outcome': outcome, 'ticks': 1500, 'scores': [0] * 10, 'total_xp': [10] * 10, 'seed': 1}
    record = episode(tmp_path, data)
    assert record.draw
    assert not any(s.won for s in record.seats)
    assert compare.metric_value([{'draw': record.draw}], 'draw_rate') == (1, 1)
    assert miner_rows.seat_row(record.seats[0], record, 'win')['draw']
    (tmp_path / 'player_status.json').write_text(json.dumps({'players': []}))
    rows = lasthit_eval.parse_episode(tmp_path, True, Path('a'), Path('b'))
    assert rows[0].outcome == outcome and rows[0].hero_class == 'unknown'
    lasthit_eval.report(rows)
    output = capsys.readouterr().out
    assert f'{outcome}=1' in output and 'loss=0' in output


def test_verified_draft_class_join(tmp_path):
    data = json.loads((ROOT / 'tests/fixtures/gota/replay_small.results.json').read_text())
    episode(tmp_path, data)
    report = json.loads((ROOT / 'tests/fixtures/gota/replay_small.expected.json').read_text())
    report['episodes'][0]['episode_id'] = 'test'
    for row in report['episodes'][0]['seats']:
        row['policy_name'] = 'test'
    stats = tmp_path / 'stats.json'
    stats.write_text(json.dumps(report))
    records, _ = gota_episodes.load_batch(tmp_path, stats)
    assert records[0].seats[0].hero_class == 'Crossbowman'
    assert miner_rows.seat_row(records[0].seats[0], records[0], 'win')['role'] == 'burst'
    groups, _ = compare.load_arm(tmp_path, 'test', 1, 'class', stats)
    assert len(groups['Crossbowman'][0][0]) == 8


def test_relative_manifest_is_absolute_in_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('manifest.json').write_text('{}')
    Path('policy.bas').write_text('END')
    monkeypatch.setattr(sys, 'argv', ['lasthit_eval', '--candidate', 'policy.bas', '--manifest',
        'manifest.json', '--mirror', '--episodes', '1', '--out', str(tmp_path / 'out')])
    commands = []
    def capture(command, **kwargs):
        commands.append(command)
        return type('Result', (), {'returncode': 0})()
    monkeypatch.setattr(lasthit_eval.subprocess, 'run', capture)
    monkeypatch.setattr(lasthit_eval, 'parse_episode', lambda *a: [])
    assert lasthit_eval.main() == 0
    assert commands[0][6] == str(tmp_path / 'manifest.json')


@pytest.mark.parametrize('outcome', ['unknown', None])
def test_reader_rejects_invalid_completed_outcome(tmp_path, outcome):
    data = {'outcome': outcome, 'ticks': 1500, 'scores': [0] * 10, 'total_xp': [10] * 10}
    with pytest.raises(ValueError, match='outcome must be'):
        episode(tmp_path, data)


@pytest.mark.parametrize('value', [True, -1, 10, '6'])
def test_invalid_replay_class_rejected(value):
    with pytest.raises(ValueError, match='invalid replay hero class'):
        gota_episodes.replay_class_name({'class': value})


def test_missing_replay_class_stays_unknown():
    assert gota_episodes.replay_class_name({}) == 'unknown'


def test_fresh_package_and_standalone_imports(tmp_path):
    import os
    import subprocess

    for module, pythonpath in [
        ('games.gota.gates.g5_telemetry', ROOT),
        ('gota_episodes', ROOT / 'games/gota/tools'),
    ]:
        result = subprocess.run([sys.executable, '-c', f'import {module}'], cwd=tmp_path,
            env=dict(os.environ, PYTHONPATH=str(pythonpath)),
            capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('single_seat,expected_kind', [(False, 'mean'), (True, 'rate')])
def test_verified_class_group_statistical_dispatch(tmp_path, single_seat, expected_kind):
    data = json.loads((ROOT / 'tests/fixtures/gota/replay_small.results.json').read_text())
    episode(tmp_path, data)
    report = json.loads((ROOT / 'tests/fixtures/gota/replay_small.expected.json').read_text())
    report['episodes'][0]['episode_id'] = 'test'
    for row in report['episodes'][0]['seats']:
        row['policy_name'] = 'test'
    if single_seat:
        metadata = json.loads((tmp_path / 'episode.json').read_text())
        metadata['participants'] = metadata['participants'][:1]
        (tmp_path / 'episode.json').write_text(json.dumps(metadata))
    stats = tmp_path / 'stats.json'
    stats.write_text(json.dumps(report))
    groups, _ = compare.load_arm(tmp_path, 'test', 1, 'class', stats)
    actual_single = all(len(seats) == 1 for group in compare.CLASS_NAMES
                        for seats, _ in groups[group])
    assert actual_single == single_seat
    kinds = {key: kind for key, _, kind, _ in compare.metrics_for('class', actual_single)}
    assert kinds['win_rate'] == expected_kind
    assert kinds['vm_error_rate'] == expected_kind
    assert kinds['no_last_hit_rate'] == expected_kind
    assert kinds['ops_fail_rate'] == kinds['draw_rate'] == 'rate'
