"""The oracle compares complete ordered decisions, including rejected/no-op calls."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest

from games.gota.tools.equivalence import BASSY_COMMIT, POLYWORLD_COMMIT, compare_streams


def stream():
    return [
        {'type': 'header', 'schema': 1, 'engine': POLYWORLD_COMMIT, 'bassy': BASSY_COMMIT, 'seed': 1,
         'draft_mode': 'open', 'max_battle_ticks': 1, 'hero_ids': list(range(100, 110)),
         'candidate_seats': list(range(5)), 'config': {'fixed': True}},
        {'type': 'tick', 'tick': 1, 'phase': 'battle', 'decision_order': list(range(100, 110)),
         'actions': [{'heroId': 100, 'tick': 1, 'kind': 2, 'slot': 0,
                      'first': 0, 'second': 0, 'offset': [0, 0]}]},
        {'type': 'end', 'ticks': 1, 'battle_ticks': 1, 'complete': True,
         'errors': [], 'seat_decisions': [1] * 10},
    ]


def test_identical_and_print_only_changes():
    a = stream()
    b = copy.deepcopy(a)
    b.insert(1, {'type': 'print', 'heroId': 100, 'text': 'PWD ...'})
    assert compare_streams(a, b)['equivalent']


@pytest.mark.parametrize('mutation', ['action', 'order', 'missing_decision', 'tick', 'truncated',
                                     'error', 'empty', 'setup', 'seat_order', 'missing_hero'])
def test_differences_and_incomplete_evidence_fail(mutation):
    a = stream()
    b = copy.deepcopy(a)
    if mutation == 'action': b[1]['actions'][0]['first'] = 9
    if mutation == 'order':
        a[1]['actions'] *= 2
        a[1]['actions'][1] = dict(a[1]['actions'][0], first=7)
        b[1]['actions'] = list(reversed(a[1]['actions']))
    if mutation == 'missing_decision': b[1]['decision_order'].remove(100)
    if mutation == 'tick': b[1]['tick'] = 2
    if mutation == 'truncated': b.pop()
    if mutation == 'error': b[-1]['errors'] = ['VM failed']
    if mutation == 'empty': b = []
    if mutation == 'setup': b[0]['draft_mode'] = 'unique'
    if mutation == 'seat_order': b[1]['decision_order'].reverse()
    if mutation == 'missing_hero':
        b[1]['decision_order'].remove(104)
        b[-1]['seat_decisions'][4] = 0
    assert not compare_streams(a, b)['equivalent']


def test_matching_but_wrong_engine_identity_is_not_equivalence():
    rows = stream()
    rows[0]['engine'] = 'old-engine'
    assert not compare_streams(rows, rows)['equivalent']
