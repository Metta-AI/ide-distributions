"""Conformance of the real GotA game contract and local bindings."""
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from autoresearch.games.loader import GameError, load_game

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / 'tests/fixtures/gota/probe_full.results.json'


def binding(name, *args):
    game = load_game(ROOT, 'gota')
    return subprocess.run([*shlex.split(game.bindings[name]), *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True, check=False)


def test_real_contract():
    game = load_game(ROOT, 'gota')
    assert game.compiler == 'pw'
    assert game.layers == ['P', 'K', 'S', 'SK', 'C', 'G', 'ST', 'A']
    assert game.min_episodes_floor == 40
    assert game.performance_guardrails == ['glory', 'win_rate']
    assert game.league_id == 'league_3c60897b-25cf-4b37-9d1a-8554c1198f28'
    assert [(m.name, m.direction) for m in game.outcome_metrics] == [
        (name, 'up') for name in ('glory', 'win_rate', 'xp_per_1k_ticks', 'team_xp_lead')]
    for command in game.bindings.values():
        parts = shlex.split(command)
        assert parts[0] == 'python3'
        if parts[1] == '-m':
            assert (ROOT / Path(*parts[2].split('.')) / '__main__.py').is_file()
        else:
            assert (ROOT / parts[1]).is_file()
    assert (ROOT / game.reference_types['replay-segment']).is_file()


def test_missing_league_id(tmp_path):
    source = ROOT / 'games/gota/GAME.md'
    destination = tmp_path / 'games/gota/GAME.md'
    destination.parent.mkdir(parents=True)
    destination.write_text('\n'.join(line for line in source.read_text().splitlines()
                                     if not line.startswith('league_id:')))
    with pytest.raises(GameError, match='league_id: missing or empty'):
        load_game(tmp_path, 'gota')


def test_probe_metrics():
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == json.loads(
        (FIXTURE.parent / 'probe_full.provenance.json').read_text())['sha256']
    result = binding('evaluate', FIXTURE, '--seat', 3)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        'glory': 452, 'win_rate': 1, 'xp_per_1k_ticks': 9080 * 1000 / 28877,
        'team_xp_lead': 1}
    losing = binding('evaluate', FIXTURE, '--seat', 5)
    assert json.loads(losing.stdout)['win_rate'] == 0
    assert json.loads(losing.stdout)['glory'] == 0


@pytest.mark.parametrize('field,value', [('ticks', 0), ('ticks', True),
    ('outcome', 'unknown'), ('scores', [1]), ('total_xp', [1] * 9),
    ('total_xp', [float('nan')] * 10), ('scores', [float('inf')] * 10)])
def test_invalid_results(tmp_path, field, value):
    data = json.loads(FIXTURE.read_text())
    data[field] = value
    path = tmp_path / 'results.json'
    path.write_text(json.dumps(data))
    result = binding('evaluate', path, '--seat', 0)
    assert result.returncode != 0
    assert field in result.stderr
    assert not result.stdout


def test_ties_and_draw(tmp_path):
    # Native draw spelling: Polyworld 5287c437 sim.nim:3853-3859.
    data = json.loads(FIXTURE.read_text())
    data.update(total_xp=[10] * 10, scores=[0] * 10, outcome='draw')
    path = tmp_path / 'results.json'
    path.write_text(json.dumps(data))
    result = binding('evaluate', path, '--seat', 0)
    assert json.loads(result.stdout) == {
        'glory': 0, 'win_rate': 0, 'xp_per_1k_ticks': 10000 / 28877, 'team_xp_lead': 0}


@pytest.mark.parametrize('seat', ['-1', '10'])
def test_invalid_seat(seat):
    result = binding('evaluate', FIXTURE, '--seat', seat)
    assert result.returncode != 0
    assert 'seat' in result.stderr


@pytest.mark.parametrize('name,gap', [('build', 'Task 2.5'), ('upload', 'C6')])
def test_deferred_bindings_fail_closed(name, gap):
    result = binding(name)
    assert result.returncode != 0
    assert gap in result.stderr
    assert not result.stdout


def test_segment_resolver(tmp_path):
    replay = tmp_path / 'replay.bin'
    replay.write_bytes(b'replay')
    command = [sys.executable, 'games/gota/tools/resolve_segment.py']
    result = subprocess.run([*command, f'{replay}#ticks=10:20'], cwd=ROOT,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'replay': str(replay.resolve()),
                                       'start_tick': 10, 'end_tick': 20}
    for reference in [f'{replay}#ticks=20:10', f'{replay}#ticks=-1:20',
                      f'{replay}#ticks=10:10', str(replay), 'missing#ticks=1:2']:
        result = subprocess.run([*command, reference], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        assert result.returncode != 0


@pytest.mark.parametrize('outcome,seat,win', [('BlueTeam', 5, 1),
                                            ('BlueTeam', 0, 0), ('time_limit', 5, 0)])
def test_blue_and_timeout(tmp_path, outcome, seat, win):
    data = json.loads(FIXTURE.read_text())
    data.update(outcome=outcome, scores=[0] * 5 + ([7] * 5 if win or outcome == 'BlueTeam' else [0] * 5))
    path = tmp_path / 'results.json'
    path.write_text(json.dumps(data))
    result = binding('evaluate', path, '--seat', seat)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['win_rate'] == win
    assert json.loads(result.stdout)['glory'] == (7 if win else 0)


@pytest.mark.parametrize('data,field', [([], 'object'), ({}, 'ticks'),
    ({'ticks': 1, 'outcome': 'draw', 'scores': [0] * 10}, 'total_xp')])
def test_missing_evidence(tmp_path, data, field):
    path = tmp_path / 'results.json'
    path.write_text(json.dumps(data))
    result = binding('evaluate', path, '--seat', 0)
    assert result.returncode != 0
    assert field in result.stderr
    assert not result.stdout


def test_nonwinning_scores_rejected(tmp_path):
    data = json.loads(FIXTURE.read_text())
    data['scores'][5] = 1
    path = tmp_path / 'results.json'
    path.write_text(json.dumps(data))
    result = binding('evaluate', path, '--seat', 0)
    assert result.returncode != 0
    assert 'scores' in result.stderr


def test_real_live_timeout_metrics():
    # Untouched live Polyworld 5287c437 result from the Task 1.4 ten-seat run.
    result = binding('evaluate', ROOT / 'tests/fixtures/gota/v13-live-results.json', '--seat', 2)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        'glory': 0, 'win_rate': 0, 'xp_per_1k_ticks': 3600 * 1000 / 31200,
        'team_xp_lead': 1}
