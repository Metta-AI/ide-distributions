"""Exercise hosted evaluation through actual shared consumers."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ide_research_core.games.bindings import binding_evaluate
from ide_research_core.games.loader import load_game
from ide_research_core.softmax_tool.league import episode_rows

ROOT = Path(__file__).resolve().parents[3]


def test_real_shared_evaluate_and_league_consumers(tmp_path):
    game = load_game(ROOT, 'gota')
    data = {'ticks': 1000, 'seed': 37, 'outcome': 'RedTeam',
            'scores': [1] * 5 + [0] * 5, 'total_xp': list(range(10))}
    path = tmp_path / 'results.json'
    path.write_text(json.dumps(data))
    evaluate = binding_evaluate(game)
    entries = evaluate(path)
    assert [entry['seat'] for entry in entries] == list(range(10))
    assert {entry['seed'] for entry in entries} == {37}
    episode = {'episode_id': 'ep', 'results': data,
               'seats': [{'position': i, 'policy_version_id': f'p{i}'} for i in range(10)]}
    rows = episode_rows(episode, evaluate, tmp_path, '2026.10.7.2')
    assert len(rows) == 10
    assert rows[7].policy_version == 'p7'
    assert rows[7].metrics['glory'] == 0
    del data['seed']
    path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match='real integer seed'):
        evaluate(path)



def test_hosted_non_object_fails_without_traceback(tmp_path):
    path = tmp_path / 'results.json'
    path.write_text('[]')
    completed = subprocess.run([sys.executable, str(ROOT / 'games/gota/tools/evaluate.py'),
        '--results', str(path), '--json'], capture_output=True, text=True, check=False)
    assert completed.returncode == 2
    assert 'must contain an object' in completed.stderr
    assert 'Traceback' not in completed.stderr
