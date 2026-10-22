# Vendored from personal_labs_paintbot_pw@6b0bffa5085ffc4bf94ecaa8baee463e55599c84: paintbot_pw_lab/tools/tests/test_strategy_build.py
"""Source provenance and build publication are deterministic trust boundaries."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "games/gota/compiler/tools"))
import pw_cli
import strategy_build as sb
from strategy_gates import parse_peaks, screen_gate


def test_incremental_metadata_and_full_regeneration():
    old = {'K.enemy': {'text_hash': 'same', 'interface': {'outputs': ['x']}}}
    current = {'K.enemy': {'text_hash': 'same', 'interface': {'outputs': ['x']}, 'fields': {'Status': 'tested'}}}
    assert sb.changes(current, old) == {'K.enemy': 'reused'}
    assert sb.changes(current, old, full=True) == {'K.enemy': 'changed'}
    current['K.enemy']['interface'] = {'outputs': ['x', 'y']}
    assert sb.changes(current, old) == {'K.enemy': 'changed'}
    assert sb.changes({}, old) == {'K.enemy': 'removed'}


def test_changed_units_receive_previous_source(tmp_path, monkeypatch):
    from strategy_format import parse_strategy
    source = Path(__file__).parent / 'fixtures/strategy_trivial/STRATEGY.md'
    source = source.resolve()
    prior = tmp_path / 'previous'
    (prior / 'units').mkdir(parents=True)
    (prior / 'units/K.position.bas').write_text('previous unit\n')
    sb.dump(prior / 'map.json', {'components': sb.component_map(parse_strategy(source))})
    sb.dump(prior / 'version.json', {'source_hashes': {}})
    compiler = tmp_path / 'compiler'
    sb.dump(compiler / 'config.json', {'agent': 'claude', 'models': {'claude': 'test-model'}})
    for name in ('AGENT.md', 'LESSONS.md'):
        (compiler / name).write_text('instructions')
    monkeypatch.setattr(sb, 'COMPILER', compiler)
    monkeypatch.setattr(sb, 'STAGING', tmp_path / 'stage')
    monkeypatch.setattr(sb, 'BUILDS', tmp_path / 'builds')
    monkeypatch.setattr(sb, 'committed_inputs', lambda _: {})
    monkeypatch.setattr(sb, 'git', lambda *args: 'abcdef12')
    monkeypatch.setattr(sb, 'previous_build', lambda *args: prior)
    monkeypatch.setattr(sb.shutil, 'which', lambda _: '/fake/claude')
    monkeypatch.setattr(sb.subprocess, 'run', lambda *args, **kw: SimpleNamespace(stdout='test-version'))
    gota_source = tmp_path / 'STRATEGY.md'
    gota_source.write_text(source.read_text().replace(',10,11,12,13,14,15', ''))
    monkeypatch.setattr(sb, 'REPO', tmp_path)
    order = sb.prepare(gota_source, full=True)
    assert order['backend'] == 'gota'
    assert order['units']['K.position'] == 'changed'
    assert (sb.STAGING / order['build_id'] / 'units/K.position.bas').read_text() == 'previous unit\n'


def test_scope_checks_deletions_and_ignored_files(tmp_path):
    (tmp_path / 'source.md').write_text('source')
    (tmp_path / '.gitignore').write_text('hidden\n')
    before = sb.snapshot(tmp_path)
    (tmp_path / 'source.md').unlink()
    (tmp_path / 'hidden').write_text('not hidden from this check')
    (tmp_path / 'units').mkdir()
    (tmp_path / 'units/C.idle.bas').write_text('sub c_idle__tick()\nend sub\n')
    after = sb.snapshot(tmp_path)
    assert sb.scope_changes(before, after, {'units/C.idle.bas'}) == ['hidden', 'source.md']


def test_previous_unit_cannot_satisfy_missing_generated_output(tmp_path):
    stage, root = tmp_path / 'stage', tmp_path / 'agent'
    (stage / 'units').mkdir(parents=True)
    (root / 'units').mkdir(parents=True)
    (stage / 'units/C.idle.bas').write_text('previous changed unit')
    (stage / 'units/K.position.bas').write_text('unchanged unit')
    sb.copy_unit_inputs(stage, root, ['C.idle'], {}, repair=False)
    assert not (root / 'units/C.idle.bas').exists()
    assert (root / 'context/previous/C.idle.bas').read_text() == 'previous changed unit'
    assert (root / 'units/K.position.bas').read_text() == 'unchanged unit'


def test_scope_detects_symlinks(tmp_path):
    before = sb.snapshot(tmp_path)
    (tmp_path / 'escape').symlink_to(tmp_path.parent, target_is_directory=True)
    assert sb.scope_changes(before, sb.snapshot(tmp_path), set()) == ['escape']


def test_artifact_tampering_refuses_reuse(tmp_path):
    policy = tmp_path / 'policy.bas'
    policy.write_text('x = 1\n')
    sb.dump(tmp_path / 'version.json', {'status': 'passed', 'policy_sha256': sb.digest(policy)})
    sb.checked_build(tmp_path)
    policy.write_text('x = 2\n')
    with pytest.raises(pw_cli.UsageError, match='modified'):
        sb.checked_build(tmp_path)


def test_missing_or_bad_peak_evidence_fails():
    with pytest.raises(ValueError, match='missing'):
        parse_peaks('')
    with pytest.raises(ValueError, match='16 positive'):
        parse_peaks('peak_instructions=[1,2]\npeak_work=[1,2]')
    text = 'peak_instructions=[' + ','.join(['100'] * 16) + ']\npeak_work=[' + ','.join(['200'] * 16) + ']'
    assert max(parse_peaks(text)['work']) == 200
    padded = text.replace(']', ',' + ','.join(['0'] * 240) + ']')
    assert len(parse_peaks(padded)['instructions']) == 16


def screen(low, high, bad=0, ok=True):
    return {'ok': ok, 'result': {'matches_with_bad_seats': bad, 'summary': {
        'seed_balanced': {'ci95_low': low, 'ci95_high': high}}}}


def test_screen_does_not_veto_behavior_change():
    assert screen_gate(screen(.1, .2), 'behavior_change')['passed']
    assert not screen_gate(screen(.1, .2), 'no_behavior_change')['passed']
    assert screen_gate(screen(.51, .6), 'm1')['passed']
    assert screen_gate(screen(.49, .51), 'm1')['passed']
    for intent in ('behavior_change', 'no_behavior_change', 'm1'):
        assert not screen_gate(screen(.49, .51, bad=1), intent)['passed']
        assert not screen_gate(screen(.49, .51, ok=False), intent)['passed']


def test_screen_missing_evidence_never_passes():
    assert not screen_gate({'ok': True}, 'behavior_change')['passed']
    assert not screen_gate(screen(None, None), 'm1')['passed']


def test_finalization_is_one_way(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, 'STAGING', tmp_path / 'stage')
    monkeypatch.setattr(sb, 'BUILDS', tmp_path / 'builds')
    sb.BUILDS.mkdir()
    stage = sb.STAGING / 'abcdef12-1'
    stage.mkdir(parents=True)
    (stage / 'policy.bas').write_text('x = 1\n')
    order = {'build_id': stage.name, 'source_commit': 'abcdef12', 'source_path': 'STRATEGY.md',
             'source_hashes': {}, 'previous_build': None, 'compiler': {}, 'engine': {},
             'created': 'now', 'intent': 'behavior_change', 'units': {}, 'components': {}}
    result = sb.finalize(order, {f'G{i}': {'passed': True} for i in range(1, 6)}, full_evaluation=True)
    assert not stage.exists()
    assert sb.checked_build(result)['status'] == 'passed'
    with pytest.raises(pw_cli.UsageError):
        sb.finalize(order, {})


def test_partial_gate_set_cannot_finalize_as_passed(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, 'STAGING', tmp_path / 'stage')
    monkeypatch.setattr(sb, 'BUILDS', tmp_path / 'builds')
    sb.BUILDS.mkdir()
    stage = sb.STAGING / 'abcdef12-1'
    stage.mkdir(parents=True)
    order = {'build_id': stage.name, 'source_commit': 'abcdef12', 'source_path': 'STRATEGY.md',
             'source_hashes': {}, 'previous_build': None, 'compiler': {}, 'engine': {},
             'created': 'now', 'intent': 'behavior_change', 'units': {}, 'components': {}}
    result = sb.finalize(order, {'G1': {'passed': True}}, full_evaluation=True)
    assert json.loads((result / 'version.json').read_text())['status'] == 'failed'


def test_tested_component_requires_high_guess_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, 'STAGING', tmp_path / 'stage')
    monkeypatch.setattr(sb, 'BUILDS', tmp_path / 'builds')
    sb.BUILDS.mkdir()
    stage = sb.STAGING / 'abcdef12-1'
    stage.mkdir(parents=True)
    guess = {'id': 'G-C.idle-1', 'component': 'C.idle', 'spec_quote': 'Wait.',
             'decision': 'Wait', 'why': 'contradiction', 'severity': 'high', 'state': 'open'}
    sb.dump(stage / 'report_draft.json', {'guesses': [guess], 'gaps': []})
    order = {'build_id': stage.name, 'source_commit': 'abcdef12', 'source_path': 'STRATEGY.md',
             'source_hashes': {}, 'previous_build': None, 'compiler': {}, 'engine': {},
             'created': 'now', 'intent': 'behavior_change', 'units': {'C.idle': 'new'},
             'components': {'C.idle': {'fields': {'Status': 'tested'}}}}
    result = sb.finalize(order, {f'G{i}': {'passed': True} for i in range(1, 6)}, full_evaluation=True)
    report = sb.read(result / 'report.json')
    assert report['status'] == 'failed'
    assert 'G-C.idle-1' in report['error']


def test_guess_quote_and_id_are_checked():
    order = {'components': {'C.idle': {'compiled_text': '- Spec: Do nothing.', 'fields': {'Spec': 'Do nothing.'}}}}
    guess = {'id': 'G-C.idle-1', 'component': 'C.idle', 'spec_quote': 'Do nothing.',
             'decision': 'Wait.', 'why': 'No actions.', 'severity': 'low', 'state': 'open'}
    sb.validate_draft({'guesses': [guess], 'gaps': []}, order)
    sb.validate_draft({'guesses': [{**guess, 'spec_quote': 'Spec: Do nothing.'}], 'gaps': []}, order)
    with pytest.raises(sb.BuildError, match='quote') as error:
        sb.validate_draft({'guesses': [{**guess, 'spec_quote': 'invented'}], 'gaps': []}, order)
    assert error.value.diagnostics[0].component == 'C.idle'
    assert error.value.diagnostics[0].code == 'report-draft'
    with pytest.raises(sb.BuildError, match='unique'):
        sb.validate_draft({'guesses': [guess, guess], 'gaps': []}, order)


def test_committed_input_check_includes_staged_edits_and_deletions(tmp_path, monkeypatch):
    lab = tmp_path / 'lab'
    compiler = lab / 'strategy/compiler'
    compiler.mkdir(parents=True)
    (lab / 'tools').mkdir()
    (lab / 'docs').mkdir()
    (lab / 'reference').mkdir()
    (lab / 'docs/policy-surface.md').write_text('contract')
    (lab / 'reference/base-bassy-28030de6.bas').write_text('reference')
    source = lab / 'strategy/STRATEGY.md'
    source.write_text('strategy')
    comms = source.parent / 'comms.md'
    comms.write_text('comms')
    (compiler / 'AGENT.md').write_text('instructions')
    for name in ('pw_strategy.py', 'pw_intent.py', 'release.env', 'pw_local.py', 'pw_release.py',
                 'pw_cli.py', 'pw_terrain.py', 'pw.py'):
        (lab / 'tools' / name).write_text('input')
    monkeypatch.setattr(sb, 'REPO', tmp_path)
    monkeypatch.setattr(sb, 'LAB', lab)
    monkeypatch.setattr(sb, 'COMPILER', compiler)
    import gota_host
    baseline = tmp_path / 'v13-live.bas'
    baseline.write_text('fixture baseline')
    monkeypatch.setattr(gota_host, 'BASELINE', baseline)
    (tmp_path / 'tools').mkdir()
    for name in ('build_nim.sh', 'build_gota_match.sh', 'engine.env', 'host_contract.json'):
        (tmp_path / 'tools' / name).write_text('fixture builder')
    original_git = sb.git
    monkeypatch.setattr(sb, 'git', lambda *args, **kwargs: original_git(*args, root=tmp_path))
    sb.git('init')
    sb.git('add', '.')
    sb.git('-c', 'user.name=Test', '-c', 'user.email=test@invalid', 'commit', '-m', 'inputs')
    assert sb.committed_inputs(source)
    comms.unlink()
    with pytest.raises(pw_cli.UsageError, match='commit compiler'):
        sb.committed_inputs(source)
    comms.write_text('changed')
    sb.git('add', '.')
    with pytest.raises(pw_cli.UsageError, match='commit compiler'):
        sb.committed_inputs(source)


def test_kept_guess_remains_open_and_accepts_matches_whole_id(tmp_path, monkeypatch):
    previous = {'id': 'G-C.idle-1', 'component': 'C.idle', 'state': 'open', 'severity': 'high'}
    sb.dump(tmp_path / 'report.json', {'guesses': [previous]})
    monkeypatch.setattr(sb, 'build_path', lambda _: tmp_path)
    order = {'previous_build': 'previous', 'units': {'C.idle': 'changed'},
             'components': {'C.idle': {'fields': {'Accepts': 'G-C.idle-12'}}}}
    result = sb.carry_guesses(order, {'guesses': [{**previous, 'state': 'kept'}]})
    assert result[0]['state'] == 'open'
    order['components']['C.idle']['fields']['Accepts'] = 'G-C.idle-1, G-C.idle-12'
    assert sb.carry_guesses(order, {'guesses': []})[0]['state'] == 'closed'


def test_agent_context_uses_current_authored_skills(tmp_path):
    stage, root = tmp_path / 'stage', tmp_path / 'agent'
    (stage / 'units').mkdir(parents=True)
    (root / 'units').mkdir(parents=True)
    for name in ('SK.motor', 'runtime.lib', 'generated.tables'):
        (stage / 'units' / f'{name}.bas').write_text('stale bytes')
    motor, added = tmp_path / 'motor.bas', tmp_path / 'added.bas'
    motor.write_text('current motor bytes')
    added.write_text('new authored skill')
    sb.copy_unit_inputs(stage, root, [], {'SK.motor': motor, 'SK.added': added}, repair=False)
    assert (root / 'units/SK.motor.bas').read_bytes() == motor.read_bytes()
    assert (root / 'units/SK.added.bas').read_bytes() == added.read_bytes()
    assert not (root / 'units/runtime.lib.bas').exists()
    assert not (root / 'units/generated.tables.bas').exists()


def test_default_build_receipt_does_not_claim_evaluation(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, 'STAGING', tmp_path / 'stage')
    monkeypatch.setattr(sb, 'BUILDS', tmp_path / 'builds')
    sb.BUILDS.mkdir()
    stage = sb.STAGING / 'abcdef12-1'
    stage.mkdir(parents=True)
    (stage / 'policy.bas').write_text('x = 1\n')
    order = {'build_id': stage.name, 'source_commit': 'abcdef12', 'source_path': 'STRATEGY.md',
             'source_hashes': {}, 'previous_build': None, 'compiler': {}, 'engine': {},
             'created': 'now', 'intent': 'behavior_change', 'units': {}, 'components': {}}
    result = sb.finalize(order, {'G1': {'passed': True}})
    version = sb.checked_build(result)
    assert version['status'] == 'passed'
    assert version['validation_scope'] == 'build'
    assert version['evaluation'] == {'status': 'not_run'}
    assert list(version['gates']) == ['G1']
