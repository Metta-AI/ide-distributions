"""Test-only full units exercise the compiler; no production baseline-copy path exists."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'games/gota/compiler/tools'))
import strategy_basic as basic
from strategy_format import lint_strategy, parse_strategy

from games.gota.tools.goal_audit import audit

SEED = ROOT / 'games/gota/seed/strategy/STRATEGY.md'
FIXTURES = ROOT / 'tests/fixtures/gota/v13_units'
RUNTIME = ROOT / 'games/gota/compiler/strategy/compiler/runtime'


def assembled():
    strategy = parse_strategy(SEED, backend='gota')
    assert not [d for d in lint_strategy(strategy) if d.level == 'error']
    units = {path.stem: path.read_text() for path in FIXTURES.glob('*.bas')}
    return basic.assemble(strategy, units, RUNTIME, 'abcdef12-1', 'a' * 40)


def test_full_fixture_is_structured_and_bounded():
    result = assembled()
    assert result['policy'].startswith("' @gota-structures\n")
    assert 'DIM c_farm__pvId(63)'.lower() in result['policy'].lower()
    assert 'packing' not in result['map']
    assert result['budget']['globals'] == 384
    assert result['budget']['arrays'] == 102
    assert result['budget']['array_cells'] == 157993
    assert result['map']['rules'][0]['goal_refs'] == ['G.survive', 'G.farm', 'G.finish']


def test_fixture_mechanical_statements_match_exact_reference():
    original = (ROOT / 'tests/fixtures/gota/v13-live.bas').read_text()
    units = {path.stem: path.read_text() for path in FIXTURES.glob('*.bas')}
    def tokens(text):
        return [item for line in text.splitlines() for item in basic._tokens(line)]
    def undo(text):
        return re.sub(r'\b(?:c_farm|k_config)__', '', text, flags=re.IGNORECASE)
    old = {m[1].lower(): m[0] for m in re.finditer(r'(?ims)^sub (\w+)\([^\n]*\).*?^end sub', original)}
    restored = undo(units['C.farm'] + units['K.config'])
    new = {m[1].lower(): m[0] for m in re.finditer(r'(?ims)^sub (\w+)\([^\n]*\).*?^end sub', restored)}
    new['cfginit'] = new.pop('update').replace('update()', 'cfginit()')
    for name, body in old.items():
        assert tokens(new[name]) == tokens(body)
    main = original[original.index('\ncfgInit()'):original.index("' ---- telemetry ----")]
    main = main.replace('\ncfgInit()', '', 1)
    tick = new['tick'].split('\n', 1)[1].rsplit('\n', 3)[0]
    assert tokens(tick) == tokens(main)


def test_goal_audit_reports_all_levels_without_invented_predicates(tmp_path):
    mapping = assembled()['map']
    for seat in range(5):
        (tmp_path / f'seat-{seat}.log').write_text(
            'PWD v=3 g=G.survive,G.farm,G.finish t=1 r=1 c=1 i=0,0,0 h=0 p=0 f=0\n')
    result = audit(mapping, tmp_path)
    assert len(result['levels']) == 5
    assert all(check['status'] == 'unmeasurable' for check in result['checks'])
    assert all(seat['coverage_status'] == 'unmeasurable' for seat in result['seats'])
    assert result['seats'][0]['goal_label_counts']['G.survive'] == 1
    (tmp_path / 'seat-0.log').write_text('PWD malformed\n')
    assert audit(mapping, tmp_path)['seats'][0]['issues']


def test_native_death_edge_resets_selection_once_without_resetting_policy_state(prepare_native_tools, tmp_path):
    import json
    import subprocess

    from games.gota.tools.equivalence import cached_binary

    main = (RUNTIME / 'main.bas').read_text()
    lifecycle = main[main.index('IF selfRespawnTicks > 0'):main.index('st__receive()')]
    policy = tmp_path / 'death-edge.bas'
    # Drive the actual runtime fragment through alive/dead/dead/alive/dead inputs.
    policy.write_text('''SUB rt__died()
resets = resets + 1
END SUB
state = state + 1
testDead = 0
IF state = 2 THEN testDead = 5
IF state = 3 THEN testDead = 4
IF state = 5 THEN testDead = 5
''' + lifecycle.replace('selfRespawnTicks', 'testDead') + '''
IF state <= 5 THEN PRINT state;",";resets
END
''')
    directory = tmp_path / 'native'
    job = tmp_path / 'job.json'
    job.write_text(json.dumps({'seed': 1, 'ticks': 10, 'draft': 'open',
                               'seats': [str(policy)] * 10, 'out': str(directory)}))
    subprocess.run([str(cached_binary()), 'match', str(job)], check=True, capture_output=True, timeout=30)
    assert (directory / 'seat-0.log').read_text().splitlines()[:5] == ['1,0', '2,1', '3,1', '4,1', '5,2']


def test_oracle_refuses_other_native_build_before_execution(tmp_path, monkeypatch):
    import pytest

    from games.gota.tools import equivalence, native_build

    monkeypatch.setattr(equivalence, 'cached_binary', lambda: tmp_path / 'never-executed')
    monkeypatch.setattr(native_build, 'validate', lambda *args, **kwargs: {'fingerprint': 'actual'})
    output = tmp_path / 'oracle'
    with pytest.raises(ValueError, match='differs from immutable'):
        equivalence.run(equivalence.BASELINE, output, seeds=[1], ticks=1,
                        expected_native={'fingerprint': 'different'})
    assert not output.exists()
