"""Contract tests for the Pudge Wars Basic tools: evaluate, build arguments, upload,
segment resolver and --help. Pure Python; no native binary."""
import json
import os
import subprocess
import sys

import pytest
from support.pudge_wars_basic import GAME, ROOT, TOOLS, fixture, results

EVALUATE = TOOLS / 'evaluate.py'


def run(script, *args, env=None):
    return subprocess.run([sys.executable, str(TOOLS / script), *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True, check=False, env=env)


def write(tmp_path, data, name='results.json'):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def test_hosted_fixture_metrics():
    path = fixture('hosted_west_win.results.json')
    west = run('evaluate.py', path, '--seat', 1)
    assert west.returncode == 0, west.stderr
    assert json.loads(west.stdout) == {'win': 1.0, 'team_kill_margin': 1, 'kills': 10, 'deaths': 1,
                                       'hook_landed_rate': 78 / 131}
    east = json.loads(run('evaluate.py', path, '--seat', 4).stdout)
    assert east['win'] == 0.0 and east['team_kill_margin'] == -1


def test_hosted_envelope_has_every_seat_and_the_real_seed():
    data = results('hosted_east_win.results.json')
    completed = run('evaluate.py', '--results', fixture('hosted_east_win.results.json'), '--json')
    assert completed.returncode == 0, completed.stderr
    envelope = json.loads(completed.stdout)
    entries = envelope['data']['episodes']
    assert envelope['ok'] is True
    assert [entry['seat'] for entry in entries] == list(range(6))
    assert {entry['seed'] for entry in entries} == {data['seed']}
    assert {entry['status'] for entry in entries} == {'completed'}
    assert [entry['metrics']['win'] for entry in entries] == [0.0] * 3 + [1.0] * 3


def test_draw_at_time_limit_scores_half(tmp_path):
    data = results('local_3v3_time_limit.results.json')
    data.update(west_kills=4, east_kills=4, winner_team='draw', scores=[0.0] * 6)
    for row in data['per_seat']:
        row['kills'] = min(row['kills'], 1)
    metrics = json.loads(run('evaluate.py', write(tmp_path, data), '--seat', 0).stdout)
    assert metrics['win'] == 0.5 and metrics['team_kill_margin'] == 0


def test_time_limit_leader_wins(tmp_path):
    path = fixture('local_3v3_time_limit.results.json')
    metrics = json.loads(run('evaluate.py', path, '--seat', 0).stdout)
    assert metrics == {'win': 1.0, 'team_kill_margin': 8, 'kills': 8, 'deaths': 2, 'hook_landed_rate': 24 / 34}


def test_zero_hooks_thrown_is_zero_rate(tmp_path):
    data = results('league_host_duel.results.json')
    data['per_seat'][1].update(hooks_thrown=0, hooks_landed=0)
    metrics = json.loads(run('evaluate.py', write(tmp_path, data), '--seat', 1).stdout)
    assert metrics['hook_landed_rate'] == 0.0


def mutate(data, field, value):
    if field.startswith('per_seat.'):
        data['per_seat'][0][field.split('.', 1)[1]] = value
    elif value is KeyError:
        del data[field]
    else:
        data[field] = value
    return data


@pytest.mark.parametrize('field,value', [
    ('end_tick', 0), ('end_tick', True), ('end_tick', KeyError), ('scores', [1.0]),
    ('scores', [float('nan')] * 6), ('scores', [0.0] * 6), ('end_reason', 'quit'),
    ('end_reason', KeyError), ('winner_team', 'red'), ('winner_team', 'draw'), ('winner_team', 'east'),
    ('west_kills', -1), ('east_kills', KeyError), ('kill_goal', 0), ('per_seat', []),
    ('per_seat.team', 1), ('per_seat.slot', 3), ('per_seat.kills', 99), ('per_seat.deaths', -1),
    ('per_seat.hooks_thrown', 1.5), ('seed', 'x')])
def test_invalid_results_never_guess(tmp_path, field, value):
    data = mutate(results('hosted_west_win.results.json'), field, value)
    completed = run('evaluate.py', write(tmp_path, data), '--seat', 0)
    assert completed.returncode == 2
    assert field.split('.')[-1] in completed.stderr
    assert not completed.stdout
    assert 'Traceback' not in completed.stderr


def test_extra_per_seat_field_rejected(tmp_path):
    data = results('hosted_west_win.results.json')
    data['per_seat'][2]['assists'] = 1
    assert run('evaluate.py', write(tmp_path, data), '--seat', 0).returncode == 2


@pytest.mark.parametrize('seat', ['-1', '6', 'x'])
def test_invalid_seat(seat):
    completed = run('evaluate.py', fixture('hosted_west_win.results.json'), '--seat', seat)
    assert completed.returncode == 2
    assert 'seat' in completed.stderr


def test_hosted_mode_requires_seed(tmp_path):
    data = results('hosted_west_win.results.json')
    del data['seed']
    completed = run('evaluate.py', '--results', write(tmp_path, data), '--json')
    assert completed.returncode == 2
    envelope = json.loads(completed.stdout)
    assert envelope['ok'] is False and 'real integer seed' in envelope['error']['message']


def test_hosted_non_object_fails_without_traceback(tmp_path):
    path = tmp_path / 'results.json'
    path.write_text('[]')
    completed = run('evaluate.py', '--results', path, '--json')
    assert completed.returncode == 2
    assert 'must contain an object' in completed.stderr
    assert 'Traceback' not in completed.stderr


def test_every_hosted_league_sample_validates(tools):
    for name in ('hosted_west_win', 'hosted_east_win', 'league_host_duel', 'local_3v3_time_limit'):
        data = results(f'{name}.results.json')
        assert len(tools.evaluate.episodes(data)) == len(data['scores'])


def test_build_argument_handling(tmp_path):
    assert run('build.py').returncode == 2
    clash = run('build.py', '--starter', 'base', '--source', 'x.bas', '--out', tmp_path / 'a', '--json')
    assert clash.returncode == 2 and 'cannot be combined' in clash.stdout
    unknown = run('build.py', '--starter', 'nobody', '--out', tmp_path / 'b', '--offline', '--json')
    assert unknown.returncode == 2 and 'unknown starter' in unknown.stdout
    missing = run('build.py', '--source', tmp_path / 'none.bas', '--strategy', tmp_path / 'S.md',
                  '--out', tmp_path / 'c', '--offline', '--json')
    assert missing.returncode == 2
    assert json.loads((tmp_path / 'c/version.json').read_text())['status'] == 'rejected'
    (tmp_path / 'exists').mkdir()
    exists = run('build.py', '--starter', 'base', '--out', tmp_path / 'exists', '--offline', '--json')
    assert exists.returncode == 2 and 'already exists' in exists.stdout
    assert not any((tmp_path / 'exists').iterdir())


def test_build_live_mode_needs_research_cli(tmp_path, monkeypatch):
    env = {key: value for key, value in os.environ.items() if key != 'RESEARCH_CLI'}
    completed = run('build.py', '--starter', 'base', '--out', tmp_path / 'live', '--json', env=env)
    assert completed.returncode == 2
    assert 'RESEARCH_CLI' in completed.stdout


def test_upload_requires_shared_cli(monkeypatch):
    monkeypatch.delenv('RESEARCH_CLI', raising=False)
    completed = run('upload.py')
    assert completed.returncode == 2
    assert 'RESEARCH_CLI' in completed.stdout


def test_segment_resolver(tmp_path):
    replay = tmp_path / 'replay.pwr'
    replay.write_bytes(b'replay')
    completed = run('resolve_segment.py', f'{replay}#ticks=10:20')
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {'replay': str(replay.resolve()), 'start_tick': 10, 'end_tick': 20}
    for reference in [f'{replay}#ticks=20:10', f'{replay}#ticks=-1:20', f'{replay}#ticks=10:10',
                      str(replay), 'missing#ticks=1:2']:
        assert run('resolve_segment.py', reference).returncode != 0


def test_upload_help_is_the_shared_cli_help(tmp_path):
    # upload.py is GotA's, unchanged: it forwards every argument, --help included.
    cli = tmp_path / 'research'
    cli.write_text('#!/bin/sh\necho "usage: research $*"\n')
    cli.chmod(0o755)
    completed = run('upload.py', '--help', env={**os.environ, 'RESEARCH_CLI': str(cli)})
    assert completed.returncode == 0
    assert completed.stdout.strip() == 'usage: research softmax upload --help'


@pytest.mark.parametrize('name', ['native', 'match', 'build', 'local_check', 'evaluate',
                                  'resolve_segment', 'replay_inspect', 'branch', 'compare'])
def test_tool_help(name):
    completed = run(f'{name}.py', '--help')
    assert completed.returncode == 0, completed.stderr
    assert 'usage' in completed.stdout.lower()


@pytest.mark.parametrize('name,args', [('match.py', ['--seat', 'base']),
                                       ('match.py', ['--seat', 'base:3', '--seat', 'nope.bas:3']),
                                       ('match.py', ['--seat', 'base:6', '--seeds', '5..1']),
                                       ('match.py', ['--seat', 'base:6', '--variant', 'ffa']),
                                       ('local_check.py', ['x.bas', '--baseline', 'base', '--work-dir', 'w',
                                                           '--gate', 'G9']),
                                       ('replay_inspect.py', ['/does/not/exist']),
                                       ('branch.py', ['/does/not/exist', '--slot', '0', '--ticks', '1'])])
def test_rejected_input_exits_2_with_envelope(name, args):
    completed = run(name, *args, '--json')
    assert completed.returncode == 2, completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope['ok'] is False and envelope['error']['message']


def test_engine_pin_matches_manifest_and_starters(tools):
    pin = tools.cli_contract.engine()
    manifest = json.loads((TOOLS / 'coworld_manifest.json').read_text())
    assert manifest['game']['name'] == 'pudge-wars-basic'
    assert manifest['game']['version'] == pin['COWORLD_VERSION']
    assert manifest['game']['runnable']['source_url'] == pin['SOURCE_URL']
    assert pin['POLYWORLD_PUDGE_COMMIT'] in pin['SOURCE_URL']
    for player in manifest['player']:
        path = tools.episodes.starter(player['id'])
        assert player['file'].endswith(tools.episodes.sha256(path))
        assert tools.episodes.sha256(path) in (GAME / f'baselines/{player["id"]}.provenance.md').read_text()


def test_seat_specs(tools, tmp_path):
    policy = tmp_path / 'cand.bas'
    policy.write_text('walkTo(selfX, selfY)\n')
    seats = tools.episodes.parse_seats([f'{policy}:3', 'base:3'], 6)
    assert seats[:3] == [policy.resolve()] * 3 and seats[3:] == [tools.episodes.starter('base')] * 3
    assert tools.episodes.bot_flags(seats) == ['--bot', f'{policy.resolve()}:3', '--bot',
                                               f'{tools.episodes.starter("base")}:3']
    assert tools.episodes.parse_seeds(None, '3..5', 2026) == [3, 4, 5]
    assert tools.episodes.parse_seeds(None, None, 2026) == [2026]
    assert tools.episodes.mode_flags(2) == ['--duel'] and tools.episodes.mode_flags(6) == ['--team-size', '3']
