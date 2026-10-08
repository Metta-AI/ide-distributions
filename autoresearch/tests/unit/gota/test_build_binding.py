import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('gota_build_binding', ROOT / 'games/gota/tools/build.py')


def test_build_adapter_exists():
    assert Path(spec.origin).is_file()

import json
import subprocess
import sys

import pytest

from autoresearch.softmax_tool.upload import _read_build


def test_missing_manifest_launches_no_agent(tmp_path, monkeypatch):
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def forbidden(*args, **kwargs):
        raise AssertionError('no subprocess allowed before manifest validation')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(sys, 'argv', ['build', str(tmp_path), '--out', str(tmp_path / 'out'), '--json'])
    monkeypatch.delenv('GOTA_MANIFEST', raising=False)
    assert module.main() == 2
    assert not (tmp_path / 'out/version.json').exists()


def test_wrong_manifest_pin_launches_no_agent(tmp_path, monkeypatch):
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'game': {'runnable': {'source_url': 'https://example/tree/wrong/game'}}}))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('unexpected process'))
    with pytest.raises(ValueError, match='must pin'):
        module.build(tmp_path, tmp_path / 'out', manifest)


def test_shared_upload_refuses_failed_build(tmp_path):
    (tmp_path / 'version.json').write_text(json.dumps({'artifact': 'policy.bas',
        'status': 'failed', 'gates': {'G1': {'passed': False}}}))
    with pytest.raises(Exception, match='gates did not pass'):
        _read_build(tmp_path)


def test_archive_distribution_and_candidate_committed_inputs(tmp_path, monkeypatch):
    """Real committed-input checker supports a distribution outside candidate Git."""
    sys.path.insert(0, str(ROOT / 'games/gota/compiler/tools'))
    import strategy_build as compiler
    workspace = tmp_path / 'candidate-repo'
    candidate = workspace / 'strategy/candidates/E'
    candidate.mkdir(parents=True)
    source = candidate / 'STRATEGY.md'
    source.write_text('candidate')
    installed = tmp_path / 'archive/games/gota/compiler'
    installed.mkdir(parents=True)
    driver = installed / 'driver.py'
    driver.write_text('installed compiler')
    run = subprocess.run
    for args in (['init'], ['add', '.'], ['-c', 'user.name=Test', '-c', 'user.email=test@invalid', 'commit', '-m', 'candidate']):
        run(['git', '-C', str(workspace), *args], check=True, capture_output=True)
    monkeypatch.setattr(compiler, 'REPO', workspace)
    monkeypatch.setattr(compiler, 'LAB', installed)
    monkeypatch.setattr(compiler, 'source_files', lambda _: [source, driver])
    monkeypatch.setenv('GOTA_CANDIDATE_REPO', str(workspace))
    def git(*args, root=None):
        return run(['git', '-C', str(root or workspace), *args], check=True, capture_output=True, text=True).stdout.strip()
    monkeypatch.setattr(compiler, 'git', git)
    hashes = compiler.committed_inputs(source)
    assert hashes[str(driver)] == compiler.digest(driver)
    order = {'source_path': 'strategy/candidates/E/STRATEGY.md', 'source_hashes': hashes}
    compiler.assert_inputs(order)
    driver.write_text('changed')
    with pytest.raises(SystemExit, match='input changed'):
        compiler.assert_inputs(order)
    source.write_text('uncommitted')
    with pytest.raises(SystemExit, match='commit compiler and strategy'):
        compiler.committed_inputs(source)


def test_adapter_publishes_shared_upload_envelope_with_fake_compiler(tmp_path, monkeypatch):
    import hashlib
    import types

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate = tmp_path / 'worker/strategy/candidates/E'
    candidate.mkdir(parents=True)
    source = candidate / 'STRATEGY.md'
    source.write_text('fake candidate')
    manifest = tmp_path / 'manifest.json'
    from games.gota.gates.limits import POLYWORLD_COMMIT
    manifest.write_text(json.dumps({'game': {'runnable': {
        'source_url': f'https://github.com/Metta-AI/polyworld/tree/{POLYWORLD_COMMIT}/examples/gods_of_the_arena'}}}))
    scanner = tmp_path / 'tirith'
    scanner.write_text('fake scanner')
    scanner.chmod(0o755)
    monkeypatch.setenv('GOTA_TIRITH_BIN', str(scanner))
    monkeypatch.setenv('GOTA_HERMES_PYTHON', '/fake/hermes/python')
    monkeypatch.setattr(subprocess, 'check_output', lambda argv, **kw: str(tmp_path / 'worker'))
    def fake_process(argv, **kwargs):
        assert argv[0] == '/fake/hermes/python', 'real model or external process forbidden'
        assert kwargs['env']['HERMES_HOME'] != str(tmp_path)
        return types.SimpleNamespace(stdout=str(Path(kwargs['env']['HERMES_HOME']) / 'bin/tirith'), returncode=0)
    monkeypatch.setattr(subprocess, 'run', fake_process)
    compiler = types.SimpleNamespace(read=lambda p: json.loads(p.read_text()),
        dump=lambda p, data: p.write_text(json.dumps(data)))
    monkeypatch.setitem(sys.modules, 'strategy_build', compiler)
    def compile_fake(args, report):
        target = compiler.BUILDS / 'abcdef12-1'
        target.mkdir(parents=True)
        (target / 'policy.bas').write_text('fake immutable policy')
        data = {'build_id': target.name, 'status': 'passed', 'gates': {'G1': {'passed': True}},
                'compiler': {}, 'source_commit': 'abcdef12', 'source_hashes': {str(ROOT / 'driver'): 'hash'},
                'policy_sha256': hashlib.sha256((target / 'policy.bas').read_bytes()).hexdigest()}
        compiler.dump(target / 'version.json', data)
        return {'build': str(target)}
    parser = types.SimpleNamespace(parse_args=lambda argv: argv)
    monkeypatch.setitem(sys.modules, 'pw_strategy', types.SimpleNamespace(parser=lambda: parser, run=compile_fake))
    out = tmp_path / 'separate-output'
    result = module.build(candidate, out, manifest)
    assert result['ok']
    version, artifact = _read_build(out)
    assert artifact == out / 'policy.bas'
    assert version['strategy_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert version['driver']['installed_commit'] is None
    with pytest.raises(ValueError, match='immutable'):
        module.build(candidate, out, manifest)


def test_upload_delegates_only_to_shared_cli(monkeypatch):
    upload_spec = importlib.util.spec_from_file_location('gota_upload_binding', ROOT / 'games/gota/tools/upload.py')
    upload = importlib.util.module_from_spec(upload_spec)
    upload_spec.loader.exec_module(upload)
    monkeypatch.setenv('AUTORESEARCH_CLI', '/fake/shared-cli')
    monkeypatch.setattr(sys, 'argv', ['upload.py', 'E', '--build-dir', '/builds/E'])
    def fake_process(argv, **kwargs):
        assert argv == ['/fake/shared-cli', 'softmax', 'upload', 'E', '--build-dir', '/builds/E']
        return subprocess.CompletedProcess(argv, 7)
    monkeypatch.setattr(subprocess, 'run', fake_process)
    assert upload.main() == 7
