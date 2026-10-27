"""End-to-end tests on the real binaries (opt-in: see conftest.native_binaries).

They check parity with the league host (results and state hashes from recorded league-host
runs), the gates on passing and failing policies, the build's upload acceptance, replay
decoding (gzip, hash verification, results cross-check) and branch scoring.
"""
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from support.pudge_wars_basic import FIXTURES, GAME, ROOT, TOOLS, fixture, results

from ide_research_core.softmax_tool.protocol import Refused
from ide_research_core.softmax_tool.upload import _strategy_sha256, checked_build

FIXTURES_LIVE = FIXTURES / 'live_engine.json'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(script, *args):
    return subprocess.run([sys.executable, str(TOOLS / script), *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True, check=False)


def envelope(completed):
    return json.loads(completed.stdout)


def test_binary_receipts_and_no_libcurl(tools, native_binaries):
    for target, binary in native_binaries.items():
        receipt = tools.native.read_receipt(binary)
        assert receipt['inputs']['target'] == target
        assert receipt['inputs']['engine']['commit'] == tools.native.default_commit()
        assert receipt['inputs']['dependencies']['bassy']['commit'] == tools.cli_contract.engine()['BASSY_COMMIT']
        assert '--dynlibOverride:libcurl' in receipt['inputs']['options']
        assert receipt['binary_sha256'] == tools.episodes.sha256(binary)
        assert b'libcurl(|' not in binary.read_bytes()  # nothing left to dlopen at startup
    assert run('native.py', 'path').stdout.strip() == str(native_binaries['pudge_headless'])
    league = run('native.py', 'path', '--target', 'pudge_league')
    assert league.stdout.strip() == str(native_binaries['pudge_league'])


def test_short_3v3_matches_the_league_host(native_binaries, tmp_path):
    completed = run('match.py', '--seat', 'base:3', '--seat', 'aggressive:3', '--seed', 7, '--ticks', 3000,
                    '--out', tmp_path / 'm', '--json')
    assert completed.returncode == 0, completed.stderr
    data = envelope(completed)['data']
    episode = tmp_path / 'm/seed-7'
    assert json.loads((episode / 'results.json').read_text()) == results('local_3v3_time_limit.results.json')
    assert data['episodes'][0]['state_hash'] == '00000000FADD3447'
    for name in ('replay.pwr', 'summary.json', 'events.jsonl', 'episode.json', 'player_status.json',
                 'players/0/log.txt'):
        assert (episode / name).is_file(), name
    assert data['runner'] == 'league' and set(data['binaries']) == {'pudge_headless', 'pudge_league'}
    assert data['per_policy'][0]['metrics']['win'] == 1.0
    assert envelope(completed)['engine']['commit'] == data['engine_commit']
    assert envelope(completed)['knowledge']['current'] is True


def test_league_and_headless_runners_agree(native_binaries, tmp_path):
    rows = {}
    for runner in ('league', 'headless'):
        completed = run('match.py', '--seat', 'base:3', '--seat', 'sniper:3', '--seeds', '3..4', '--ticks', 2000,
                        '--runner', runner, '--out', tmp_path / runner, '--json')
        assert completed.returncode == 0, completed.stderr
        rows[runner] = [(row['state_hash'], row['west_kills'], row['east_kills']) for row in envelope(completed)['data']['episodes']]
        for seed in (3, 4):
            rows[(runner, seed)] = (tmp_path / runner / f'seed-{seed}/results.json').read_bytes()
    assert rows['league'] == rows['headless']
    assert rows[('league', 3)] == rows[('headless', 3)] and rows[('league', 4)] == rows[('headless', 4)]
    assert not (tmp_path / 'headless/seed-3/players').exists()


def test_seat_print_logs_and_annotations(native_binaries, tmp_path):
    probe = tmp_path / 'probe.bas'
    probe.write_text('print "tick "; worldTick\nANNOTATE(worldTick, "decision", "probe", "{}")\nwalkTo(homeX, homeY)\n')
    completed = run('local_check.py', probe, '--baseline', 'base', '--gate', 'G2', '--work-dir', tmp_path / 'w', '--json')
    assert completed.returncode == 0, completed.stderr
    logs = envelope(completed)['data']['gates']['G2']['summary']['seat_logs']
    # One ANNOTATE per tick, capped by the host at 1,000 events per seat (annotations.nim).
    assert [row['annotations'] for row in logs] == [1000] * 6
    assert all(row['log_lines'] > 1000 for row in logs)
    episode = tmp_path / 'w/g2-episode'
    text = (episode / 'players/3/log.txt').read_text().splitlines()
    assert text[0] == 'Player slot 3 started.' and text[1] == 'tick 1'
    first = json.loads((episode / 'players/3/annotations.jsonl').read_text().splitlines()[0])
    assert first == {'schema_version': 1, 'time': 1, 'kind': 'decision', 'function': 'probe', 'args': {}}


def test_duel_matches_the_league_host(native_binaries, tmp_path):
    completed = run('match.py', '--variant', 'duel-1v1', '--seat', 'base', '--seat', 'aggressive',
                    '--out', tmp_path / 'd', '--json')
    assert completed.returncode == 0, completed.stderr
    assert json.loads((tmp_path / 'd/seed-2026/results.json').read_text()) == results('league_host_duel.results.json')
    assert envelope(completed)['data']['episodes'][0]['state_hash'] == '00000000A59D8019'


def test_seed_sweep_and_compile_failure(native_binaries, tmp_path):
    bad = tmp_path / 'bad.bas'
    bad.write_text('x = (\n')
    completed = run('match.py', '--variant', 'duel-1v1', '--seat', bad, '--seat', 'base', '--seeds', '1..2',
                    '--ticks', 200, '--out', tmp_path / 'b', '--json')
    assert completed.returncode == 1
    out = envelope(completed)
    assert out['error']['reason'] == 'policy_load_failed'
    assert [episode['failed_slots'] for episode in out['data']['episodes']] == [[0], [0]]
    assert 'expected a numeric expression' in out['data']['episodes'][0]['errors']['players/0/file']


def test_runtime_vm_failure_is_reported_per_seat(native_binaries, tmp_path):
    crash = tmp_path / 'crash.bas'
    crash.write_text('walkTo(64 / 3, 64)\n')
    completed = run('match.py', '--variant', 'duel-1v1', '--seat', crash, '--seat', 'base', '--ticks', 200,
                    '--out', tmp_path / 'c', '--json')
    assert completed.returncode == 0, completed.stderr
    data = envelope(completed)['data']
    assert data['episodes'][0]['vm_failed_slots'] == [0]
    assert data['episodes'][0]['vm_errors'] == {'0': 'BASIC error: BASIC value must be an exact int32'}
    assert data['per_seat'][0]['vm_failures'] == 1
    status = json.loads((tmp_path / 'c/seed-2026/player_status.json').read_text())
    assert [seat['exit_code'] for seat in status['players']] == [1, 0]


def test_local_check_gates(native_binaries, tmp_path):
    passed = run('local_check.py', GAME / 'baselines/base.bas', '--baseline', 'aggressive', '--seeds', 1,
                 '--work-dir', tmp_path / 'ok', '--full', '--json')
    assert passed.returncode == 0, passed.stderr
    evaluation = envelope(passed)['data']
    assert [name for name in evaluation['gates']] == ['G1', 'G2', 'G3', 'G4']
    assert evaluation['gates']['G2']['summary']['replay_verified'] is True
    assert evaluation['gates']['G4']['summary']['candidate']['win'] == 1.0
    assert json.loads((tmp_path / 'ok/gates.json').read_text()).keys() == evaluation['gates'].keys()
    crash = tmp_path / 'crash.bas'
    crash.write_text('walkTo(64 / 3, 64)\n')
    failed = run('local_check.py', crash, '--baseline', 'base', '--gate', 'G2',
                 '--work-dir', tmp_path / 'bad', '--json')
    assert failed.returncode == 1
    gate = envelope(failed)['data']['gates']['G2']
    assert gate['outcome'] == 'failed' and 'VM disabled' in gate['summary']['error']
    assert 'exact int32' in gate['summary']['error']
    assert list(envelope(failed)['data']['gates']) == ['G2']


def test_starter_build_is_accepted_by_the_shared_upload_check(native_binaries, tmp_path):
    out = tmp_path / 'starter'
    completed = run('build.py', '--starter', 'base', '--out', out, '--json')
    assert completed.returncode == 0, completed.stderr
    version = json.loads((out / 'version.json').read_text())
    assert version['status'] == 'passed' and version['compiler'] == {'mode': 'basic', 'transform': 'identity'}
    live = json.loads(FIXTURES_LIVE.read_text())
    recorded = version['engine']
    assert recorded['commit'] == recorded['source_url'].rsplit('/', 1)[1] == live['commit']
    assert (recorded['version'], recorded['coworld_id'], recorded['manifest_sha256']) == (
        live['version'], live['coworld_id'], live['manifest_sha256'])
    assert recorded['live'] is True and recorded['knowledge']['current'] is True
    assert set(recorded['native_build']['binaries']) == {'pudge_headless', 'pudge_league'}
    assert set(version['gates']) == {'G1', 'G2'} and version['validation_scope'] == 'build'
    build, artifact, artifact_hash = checked_build(out)
    assert artifact.name == 'policy.bas' and artifact_hash == version['policy_sha256']
    shipped = GAME / 'seed/strategy/STRATEGY.md'
    if shipped.is_file():
        assert _strategy_sha256(build) == sha(shipped)
    for name in ('report.json', 'report.md'):
        assert (out / name).is_file()


def test_candidate_build_and_failed_build(native_binaries, tmp_path):
    candidate = tmp_path / 'cand'
    candidate.mkdir()
    shutil.copyfile(GAME / 'baselines/sniper.bas', candidate / 'policy.bas')
    (candidate / 'STRATEGY.md').write_text('# Sniper\n')
    completed = run('build.py', '--source', candidate / 'policy.bas', '--strategy', candidate / 'STRATEGY.md',
                    '--out', tmp_path / 'b1', '--json')
    assert completed.returncode == 0, completed.stderr
    build, _, _ = checked_build(tmp_path / 'b1')
    assert _strategy_sha256(build) == sha(candidate / 'STRATEGY.md')
    (candidate / 'policy.bas').write_text('walkTo(64 / 3, 64)\n')
    failed = run('build.py', '--source', candidate / 'policy.bas', '--strategy', candidate / 'STRATEGY.md',
                 '--out', tmp_path / 'b2', '--json')
    assert failed.returncode == 1
    assert json.loads((tmp_path / 'b2/version.json').read_text())['status'] == 'failed'
    with pytest.raises(Refused):
        checked_build(tmp_path / 'b2')


def test_replay_inspect_decodes_a_gzipped_league_host_replay(native_binaries, tmp_path):
    episode = tmp_path / 'episode'
    episode.mkdir()
    shutil.copyfile(fixture('league_host_duel.replay.gz'), episode / 'ereq_test.replay')
    shutil.copyfile(fixture('league_host_duel.results.json'), episode / 'results.json')
    completed = run('replay_inspect.py', episode, '--events', 'kill', '--seat', 0, '--binary', native_binaries['pudge_headless'], '--json')
    assert completed.returncode == 0, completed.stderr
    data = envelope(completed)['data']
    assert data['gzip'] is True and data['game_version'] == 38 and data['verified'] is True
    assert data['results_check']['matched'] is True
    [seat] = data['seats']
    assert (seat['kills'], seat['deaths'], seat['hooksThrown'], seat['hooksLanded']) == (10, 0, 65, 30)
    assert 0 < seat['enemy_hook_accuracy'] <= seat['host_landed_ratio']
    assert data['events_total'] == 10 and all(event['killer'] == 1 for event in data['events'])
    again = envelope(run('replay_inspect.py', episode, '--binary', native_binaries['pudge_headless'], '--json'))['data']
    assert again['cache']['reused'] is True
    tampered = results('league_host_duel.results.json')
    tampered['per_seat'][0]['kills'] = 9
    tampered['west_kills'] = 10
    (episode / 'results.json').write_text(json.dumps(tampered))
    mismatch = run('replay_inspect.py', episode, '--binary', native_binaries['pudge_headless'], '--json')
    assert mismatch.returncode == 1 and 'per_seat' in envelope(mismatch)['error']['message']


def test_replay_inspect_rejects_a_corrupt_tape(native_binaries, tmp_path):
    raw = bytearray(gzip.decompress(fixture('league_host_duel.replay.gz').read_bytes()))
    raw[len(raw) // 2] ^= 0xFF
    tape = tmp_path / 'replay.pwr'
    tape.write_bytes(bytes(raw))
    completed = run('replay_inspect.py', tape, '--binary', native_binaries['pudge_headless'], '--json')
    assert completed.returncode in (1, 2)
    assert envelope(completed)['ok'] is False


def test_replay_engine_comes_from_the_episode_row(tools, native_binaries, tmp_path):
    episode = tmp_path / 'hosted'
    episode.mkdir()
    shutil.copyfile(fixture('league_host_duel.replay.gz'), episode / 'replay')
    live = json.loads(FIXTURES_LIVE.read_text())
    (episode / 'episode.json').write_text(json.dumps({'id': 'ereq_x', 'coworld_id': live['coworld_id'],
                                                      'coworld_version': live['version']}))
    completed = run('replay_inspect.py', episode, '--json')
    assert completed.returncode == 0, completed.stderr
    chosen = envelope(completed)['data']['decode_engine']
    assert chosen['commit'] == live['commit'] and 'coworld' in chosen['chosen_by']
    assert chosen['replay_game_version'] == 38


def test_replay_from_another_game_version_is_refused(native_binaries, tmp_path):
    raw = bytearray(gzip.decompress(fixture('league_host_duel.replay.gz').read_bytes()))
    raw[17:19] = (37).to_bytes(2, 'little')  # header game version (tapes.nim:98-115)
    tape = tmp_path / 'old.pwr'
    tape.write_bytes(bytes(raw))
    completed = run('replay_inspect.py', tape, '--json')
    assert completed.returncode == 2
    error = envelope(completed)['error']
    assert error['reason'] == 'engine_mismatch' and '--coworld-id' in error['message']


def test_branch_scores_options(native_binaries, tmp_path):
    tape = tmp_path / 'replay.replay'
    shutil.copyfile(fixture('league_host_duel.replay.gz'), tape)
    completed = run('branch.py', tape, '--slot', 1, '--ticks', '1000,2000', '--binary', native_binaries['pudge_headless'], '--json')
    assert completed.returncode == 0, completed.stderr
    windows = envelope(completed)['data']['windows']
    assert [window['tick'] for window in windows] == [1000, 2000]
    assert all(window['best'] and any(o['option'] == 'none' for o in window['options']) for window in windows)
    refused = run('branch.py', tape, '--slot', 5, '--ticks', '1000', '--binary', native_binaries['pudge_headless'], '--json')
    assert refused.returncode == 2
