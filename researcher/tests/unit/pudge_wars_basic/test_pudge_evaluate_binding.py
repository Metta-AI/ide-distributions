"""The evaluate binding through the real shared consumers (`softmax league-episodes` rows and
`softmax xp evidence` seat reduction), on recorded hosted results."""
import json
import shutil

import pytest
from support.pudge_wars_basic import GAME, ROOT, fixture, results

from ide_research_core.games.bindings import binding_evaluate
from ide_research_core.games.loader import load_game
from ide_research_core.softmax_tool.league import episode_rows

GAME_MD = """---
name: pudge-wars-basic
bindings:
  build: python3 games/pudge-wars-basic/tools/build.py
  local_check: python3 games/pudge-wars-basic/tools/local_check.py
  upload: python3 games/pudge-wars-basic/tools/upload.py
  evaluate: python3 games/pudge-wars-basic/tools/evaluate.py
  replay_inspect: python3 games/pudge-wars-basic/tools/replay_inspect.py
reference_types: {}
outcome_metrics:
  - {name: win, direction: up}
  - {name: team_kill_margin, direction: up}
  - {name: kills, direction: up}
  - {name: deaths, direction: down}
  - {name: hook_landed_rate, direction: up}
performance_guardrails: [win, team_kill_margin]
min_episodes_floor: 100
league_id: league_84ab9f1e-5fb8-4b62-a3ac-e49026bd12d0
noise_source: league_episodes
layers: [S]
compiler: basic
---
# Test copy of the evaluate contract
"""


@pytest.fixture
def game(tmp_path):
    """The shipped contract when GAME.md exists; otherwise a minimal one over the real tools."""
    if (GAME / 'GAME.md').is_file():
        return load_game(ROOT, 'pudge-wars-basic')
    folder = tmp_path / 'root/games/pudge-wars-basic'
    folder.mkdir(parents=True)
    (folder / 'GAME.md').write_text(GAME_MD)
    shutil.copytree(GAME / 'tools', folder / 'tools', ignore=shutil.ignore_patterns('.cache', 'bin', '__pycache__'))
    return load_game(tmp_path / 'root', 'pudge-wars-basic')


def test_shared_league_episode_rows(game, tmp_path):
    data = results('hosted_east_win.results.json')
    evaluate = binding_evaluate(game)
    entries = evaluate(fixture('hosted_east_win.results.json'))
    assert [entry['seat'] for entry in entries] == list(range(6))
    episode = {'episode_id': 'ereq_0040bb10', 'results': data,
               'seats': [{'position': i, 'policy_version_id': f'p{i}'} for i in range(6)]}
    rows = episode_rows(episode, evaluate, tmp_path, '0.1.8')
    assert len(rows) == 6
    assert rows[4].policy_version == 'p4' and rows[4].seed == data['seed']
    assert rows[4].metrics['win'] == 1.0 and rows[0].metrics['win'] == 0.0
    assert rows[0].metrics['team_kill_margin'] == -14.0
    assert set(rows[0].metrics) == {'win', 'team_kill_margin', 'kills', 'deaths', 'hook_landed_rate'}


def test_xp_evidence_reduction_sees_one_metric_set(game, tmp_path):
    # `softmax xp evidence` fails an episode whose own seats report different metric names;
    # every seat must carry all five metrics, including zero-throw seats.
    data = results('hosted_west_win.results.json')
    data['per_seat'][0].update(hooks_thrown=0, hooks_landed=0)
    path = tmp_path / 'zero.results.json'
    path.write_text(json.dumps(data))
    entries = binding_evaluate(game)(path)
    assert len({frozenset(entry['metrics']) for entry in entries}) == 1
    assert entries[0]['metrics']['hook_landed_rate'] == 0.0


def test_missing_seed_is_an_error_not_a_row(game, tmp_path):
    data = results('hosted_west_win.results.json')
    del data['seed']
    path = tmp_path / 'results.json'
    path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match='real integer seed'):
        binding_evaluate(game)(path)
