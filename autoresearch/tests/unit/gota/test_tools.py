"""Acceptance checks for the pinned GotA toolkit."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / 'games/gota/tools'


@pytest.mark.parametrize('name', ['replay_stats', 'compare', 'miner_rows', 'features', 'lasthit_eval'])
def test_python_tool_help(name):
    result = subprocess.run([sys.executable, str(TOOLS / f'{name}.py'), '--help'],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope='module')
def expander():
    import shutil

    if shutil.which('nim') is None:
        pytest.skip('Nim is missing; install Nim 2.2.10 to compile the replay expander')
    result = subprocess.run(['sh', str(TOOLS / 'build_nim.sh'),
                             '5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd'],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    binary = Path(result.stdout.strip().splitlines()[-1])
    assert binary.is_file()
    return binary


def test_replay_stats_recorded_fixture(expander, tmp_path):
    import json
    import shutil

    fixture = ROOT / 'tests/fixtures/gota'
    shutil.copy(fixture / 'replay_small.bin', tmp_path / 'replay.bin')
    shutil.copy(fixture / 'replay_small.results.json', tmp_path / 'results.json')
    result = subprocess.run([sys.executable, str(TOOLS / 'replay_stats.py'), str(tmp_path),
                             '--binary', str(expander), '--json'],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    episode = report['episodes'][0]
    for key in ['directory', 'expanded', 'episode_id']:
        episode.pop(key)
    assert report == json.loads((fixture / 'replay_small.expected.json').read_text())


def test_spell_request_counts_match_independent_raw_tape(expander, tmp_path):
    import json
    from collections import Counter

    fixture = ROOT / 'tests/fixtures/gota/replay_small.bin'
    build = subprocess.run(['sh', str(TOOLS / 'build_nim.sh'),
                            '5287c437fe5e4ae7a1fc56df3fa40f6821a7d5fd', 'raw_spell_counts'],
                           capture_output=True, text=True, check=True)
    raw = subprocess.run([build.stdout.strip().splitlines()[-1], str(fixture)],
                         capture_output=True, text=True, check=True)
    expected = {row['hero_id']: row['submitted_casts']
                for row in json.loads(raw.stdout)['heroes']}
    output = tmp_path / 'expanded.jsonl'
    subprocess.run([str(expander), str(fixture), str(output), '--actions'], check=True)
    actual = Counter()
    for line in output.read_text().splitlines():
        row = json.loads(line)
        if row['type'] == 'action' and row['kind'] in [6, 10]:
            assert 'ability_slot' in row
            actual[row['hero_id']] += 1
    assert expected
    assert dict(actual) == expected


def test_replay_stats_rejects_changed_result(expander, tmp_path):
    import json
    import shutil

    fixture = ROOT / 'tests/fixtures/gota'
    shutil.copy(fixture / 'replay_small.bin', tmp_path / 'replay.bin')
    results = json.loads((fixture / 'replay_small.results.json').read_text())
    results['total_xp'][0] += 1
    (tmp_path / 'results.json').write_text(json.dumps(results))
    result = subprocess.run([sys.executable, str(TOOLS / 'replay_stats.py'), str(tmp_path),
                             '--binary', str(expander), '--json'],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert not report['episodes']
    assert 'score/XP mismatch for seat 0' in report['errors'][0]['error']
