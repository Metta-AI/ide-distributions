import json
import subprocess
from types import SimpleNamespace

import pytest

from games.gota.gates.limits import POLYWORLD_COMMIT
from games.gota.tools import engine_identity as engine


def manifest(commit=POLYWORLD_COMMIT):
    return {'game': {'runnable': {'source_url': f'https://github.com/Metta-AI/polyworld/tree/{commit}/examples/gods_of_the_arena'}}}


def test_explicit_offline_pin(tmp_path):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest()))
    identity = engine.resolve(path, offline=True)
    assert identity['commit'] == POLYWORLD_COMMIT
    assert identity['mode'] == 'offline'
    path.write_text(json.dumps(manifest('a' * 40)))
    with pytest.raises(ValueError, match='historical'):
        engine.resolve(path, offline=True)


@pytest.mark.parametrize('change', ['none', 'version', 'manifest', 'auth', 'error', 'bad_envelope'])
def test_live_resolution_fail_closed(tmp_path, monkeypatch, change):
    path = tmp_path / 'manifest.json'
    current = manifest('b' * 40)
    path.write_text(json.dumps(current))
    monkeypatch.setenv('AUTORESEARCH_CLI', '/fake/shared')
    def run(argv, **kwargs):
        assert argv == ['/fake/shared', 'softmax', 'game-version']
        if change == 'auth':
            raise subprocess.CalledProcessError(1, argv)
        if change == 'error':
            return SimpleNamespace(stdout=json.dumps({'ok': False, 'error': 'denied'}))
        if change == 'bad_envelope':
            return SimpleNamespace(stdout='[]')
        return SimpleNamespace(stdout=json.dumps({'ok': True, 'data': {'league_id': 'league-test', 'game_version': '2026.10.8.1'}}))
    monkeypatch.setattr(subprocess, 'run', run)
    from ide_research_core.softmax_tool import platform_cli
    class FakeTransport:
        def __init__(self, env):
            pass
        def run_json(self, cli, argv):
            assert cli == 'coworld'
            if argv[0] == 'leagues':
                return {'game': {'canonical_coworld_id': 'cow-test'}}
            return {'version': 'old' if change == 'version' else '2026.10.8.1',
                    'manifest': manifest() if change == 'manifest' else current}
    monkeypatch.setattr(platform_cli, 'PlatformCli', FakeTransport)
    if change == 'none':
        result = engine.resolve()
        assert result['commit'] == 'b' * 40
        assert result['coworld_id'] == 'cow-test'
        assert result['manifest_sha256'] == engine.fingerprint(current)
    else:
        with pytest.raises((ValueError, subprocess.CalledProcessError)):
            engine.resolve(path)


def test_abbreviated_pin_rejected():
    with pytest.raises(ValueError, match='complete'):
        engine.from_manifest(manifest('02eda3d3'), '2026.10.8.1', mode='live')


def test_host_contract_rejects_changed_surface(monkeypatch):
    reference = {'host_data': ['selfHp'], 'host_functions': [], 'records': {}, 'limits': {},
                 'structured_storage': {'empty': {'globals': 78}}}
    surface = {**reference, 'baseline': {'structured_storage': reference['structured_storage']},
               'engine': {'polyworld_commit': 'b' * 40, 'bassy_commit': 'a' * 40}}
    monkeypatch.setattr(engine, 'HOST_CONTRACT', {'surface_sha256': engine.fingerprint(reference)})
    identity = {'commit': 'b' * 40, 'bassy_commit': 'a' * 40}
    engine.check_surface(surface, identity)
    surface['host_data'] = ['changed']
    with pytest.raises(ValueError, match='unsupported'):
        engine.check_surface(surface, identity)


def test_direct_gate_fetches_manifest_and_propagates_pin(tmp_path, monkeypatch):
    import sys

    from games.gota.gates import GateResult, g1_lint
    from games.gota.gates import __main__ as gates
    current = manifest('b' * 40)
    identity = {**engine.from_manifest(current, '2026.10.8.1', mode='live'), 'manifest': current}
    monkeypatch.setattr(engine, 'resolve', lambda path, **kwargs: dict(identity))
    monkeypatch.setattr(engine, 'provision', lambda receipt: (receipt, tmp_path / 'fake-probe'))
    monkeypatch.setattr(sys, 'argv', ['gates', '/fake/artifact', '--work-dir', str(tmp_path), '--gate', 'g1_lint'])
    def run(artifact, context):
        assert context.engine_commit == 'b' * 40
        assert json.loads(context.manifest.read_text()) == current
        return GateResult('passed', {})
    monkeypatch.setattr(g1_lint, 'run', run)
    assert gates.main() == 0
    assert json.loads((tmp_path / 'engine_identity.json').read_text())['commit'] == 'b' * 40
