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

from ide_research_core.softmax_tool.upload import _read_build


def test_missing_manifest_launches_no_agent(tmp_path, monkeypatch):
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def forbidden(*args, **kwargs):
        raise AssertionError('no subprocess allowed before manifest validation')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(sys, 'argv', ['build', str(tmp_path), '--out', str(tmp_path / 'out'), '--offline', '--json'])
    monkeypatch.delenv('GOTA_MANIFEST', raising=False)
    assert module.main() == 2
    assert json.loads((tmp_path / 'out/version.json').read_text())['status'] == 'rejected'


def test_wrong_manifest_pin_launches_no_agent(tmp_path, monkeypatch):
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'game': {'runnable': {'source_url': 'https://example/tree/wrong/game'}}}))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('unexpected process'))
    with pytest.raises(ValueError, match='must pin'):
        module.build(tmp_path, tmp_path / 'out', manifest, offline=True)


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


@pytest.mark.parametrize('status,timeout,environment,expected,budget,budget_env,expected_budget,copy_fails', [
    ('passed', None, None, 1200, None, None, 1800, False),
    ('failed', None, None, 1200, None, None, 1800, False),
    ('errored', None, None, 1200, None, None, 1800, False),
    ('passed', None, '1234', 1234, None, '2400', 2400, False),
    ('passed', 1400, '1234', 1400, 2400, '1800', 2400, False),
    ('passed', 3600, 'invalid', 3600, 4200, 'invalid', 4200, False),
    ('passed', None, '', 1200, None, '', 1800, False),
    # The compiler sessions cannot be copied (the profile path is a file): the build is
    # unaffected, and a compiler that raises keeps its own error.
    ('passed', None, None, 1200, None, None, 1800, True),
    ('raises', None, None, 1200, None, None, 1800, True),
    ('raises', None, None, 1200, None, None, 1800, False),
])
def test_adapter_publishes_shared_upload_envelope_with_fake_compiler(
        tmp_path, monkeypatch, capsys, status, timeout, environment, expected,
        budget, budget_env, expected_budget, copy_fails):
    import hashlib
    import shutil
    import types

    installed = tmp_path / 'distribution'
    shutil.copytree(ROOT / 'games/gota', installed / 'games/gota',
                    ignore=shutil.ignore_patterns('compiled', 'tmp', 'bin', '.cache', '__pycache__'))
    installed_spec = importlib.util.spec_from_file_location(
        'installed_gota_build', installed / 'games/gota/tools/build.py')
    module = importlib.util.module_from_spec(installed_spec)
    installed_spec.loader.exec_module(module)
    if environment is None:
        monkeypatch.delenv('GOTA_AGENT_TIMEOUT_S', raising=False)
    else:
        monkeypatch.setenv('GOTA_AGENT_TIMEOUT_S', environment)
    if budget_env is None:
        monkeypatch.delenv('GOTA_AGENT_BUDGET_S', raising=False)
    else:
        monkeypatch.setenv('GOTA_AGENT_BUDGET_S', budget_env)
    repository = tmp_path / 'repository'
    monkeypatch.setenv('RESEARCH_CANONICAL', str(repository))
    subprocess.run(['git', 'init', str(repository)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repository), '-c', 'user.name=Test',
                    '-c', 'user.email=test@invalid', 'commit', '--allow-empty', '-m', 'base'],
                   check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repository), 'worktree', 'add', '--detach',
                    str(tmp_path / 'worker')], check=True, capture_output=True)
    candidate = tmp_path / 'worker/strategy/candidates/E'
    candidate.mkdir(parents=True)
    source = candidate / 'STRATEGY.md'
    source.write_text('fake candidate')
    manifest = tmp_path / 'manifest.json'
    from games.gota.gates.limits import POLYWORLD_COMMIT
    manifest.write_text(json.dumps({'game': {'runnable': {
        'source_url': f'https://github.com/Metta-AI/polyworld/tree/{POLYWORLD_COMMIT}/examples/gods_of_the_arena'}}}))
    monkeypatch.setenv('GOTA_HERMES_PYTHON', '/fake/hermes/python')
    monkeypatch.setattr(subprocess, 'check_output', lambda argv, **kw: str(tmp_path / 'worker'))
    real_run = subprocess.run
    def fake_process(argv, **kwargs):
        if argv[0] == 'git':
            return real_run(argv, **kwargs)
        raise AssertionError(f'unexpected external process: {argv}')
    monkeypatch.setattr(subprocess, 'run', fake_process)
    compiler = types.SimpleNamespace(read=lambda p: json.loads(p.read_text()),
        dump=lambda p, data: p.write_text(json.dumps(data)))
    monkeypatch.setitem(sys.modules, 'strategy_build', compiler)
    def compile_fake(args, report, *, engine_identity=None):
        import os
        assert int(args[args.index('--agent-timeout') + 1]) == expected
        assert int(args[args.index('--agent-budget') + 1]) == expected_budget
        assert module.DEFAULT_AGENT_TIMEOUT_S < module.DEFAULT_AGENT_BUDGET_S
        analysis = Path(os.environ['GOTA_ANALYSIS_DIR'])
        assert analysis.is_relative_to(tmp_path)
        analysis.mkdir(parents=True)
        (analysis / 'evidence.json').write_text('{}')
        assert engine_identity['commit'] == POLYWORLD_COMMIT
        (compiler.STAGING / '.claims' / 'abcdef12-1').mkdir(parents=True)
        compiler_db(Path(os.environ['GOTA_COMPILER_HERMES_HOME']))
        if status == 'raises':
            raise RuntimeError('compiler crashed')
        target = compiler.BUILDS / 'abcdef12-1'
        target.mkdir(parents=True)
        (target / 'policy.bas').write_text('fake immutable policy')
        data = {'build_id': target.name, 'status': status, 'gates': {'G1': {'passed': status == 'passed'}},
                'validation_scope': 'build', 'evaluation': {'status': 'not_run'},
                'compiler': {}, 'source_commit': 'abcdef12', 'source_path': str(source),
                'source_hashes': {str(source): hashlib.sha256(source.read_bytes()).hexdigest()},
                'policy_sha256': hashlib.sha256((target / 'policy.bas').read_bytes()).hexdigest()}
        compiler.dump(target / 'version.json', data)
        return {'build': str(target), 'error': {'type': 'ValueError', 'message': 'tool fault',
                                                 'traceback': 'fault in fixture'} if status == 'errored' else None}
    parser = types.SimpleNamespace(parse_args=lambda argv: argv)
    monkeypatch.setitem(sys.modules, 'pw_strategy', types.SimpleNamespace(parser=lambda: parser, run=compile_fake))
    out = tmp_path / 'separate-output'
    profile = tmp_path / 'profile'
    if copy_fails:
        profile.write_text('a file, not a directory')
    monkeypatch.setenv('HERMES_HOME', str(profile))
    from games.gota.tools import engine_identity
    monkeypatch.setattr(engine_identity, 'provision', lambda identity: (identity, None, {}))
    if status == 'raises':
        with pytest.raises(RuntimeError, match='compiler crashed'):
            module.build(candidate, out, manifest, offline=True, timeout=timeout, budget=budget)
    else:
        result = module.build(candidate, out, manifest, offline=True, timeout=timeout, budget=budget)
    if copy_fails:
        assert 'compiler sessions not kept' in capsys.readouterr().err
    else:
        [kept] = (profile / 'gota-compiler').glob('abcdef12-1-*/state.db')
        assert compiler_session_ids(kept) == ['compiler-session']
        assert journal_mode(kept) == 'delete'  # read-only readers create no -wal/-shm files
    if status == 'raises':
        return
    if status != 'passed':
        assert not result['ok']
        assert result['status'] == status
        if status == 'errored':
            assert result['error']['message'] == 'tool fault'
        with pytest.raises(Exception, match='gates did not pass'):
            _read_build(out)
        return
    assert result['ok']
    assert (out / 'analysis/evidence.json').is_file()
    version, artifact = _read_build(out)
    assert artifact == out / 'policy.bas'
    assert version['strategy_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert version['driver']['installed_commit'] is None
    assert json.loads((out / 'coworld_manifest.json').read_text()) == json.loads(manifest.read_text())
    assert version['engine']['mode'] == 'offline'
    built = json.loads((out / 'version.json').read_text())
    assert built['started'] <= built['finished']
    with pytest.raises(ValueError, match='immutable'):
        module.build(candidate, out, manifest, offline=True, timeout=timeout, budget=budget)


def compiler_db(home):
    import sqlite3
    with sqlite3.connect(home / 'state.db') as connection:
        connection.execute('PRAGMA journal_mode=WAL')  # as Hermes runs it
        connection.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)')
        connection.execute("INSERT INTO sessions VALUES ('compiler-session', 'cli')")
    connection.close()


def compiler_session_ids(path):
    import sqlite3
    connection = sqlite3.connect(path)
    try:
        return [row[0] for row in connection.execute('SELECT id FROM sessions')]
    finally:
        connection.close()


def journal_mode(path):
    import sqlite3
    connection = sqlite3.connect(path)
    try:
        return connection.execute('PRAGMA journal_mode').fetchone()[0]
    finally:
        connection.close()


def load_build_module():
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compiler_sessions_are_kept_under_the_profile_home(tmp_path, capsys):
    module = load_build_module()
    home, scratch = tmp_path / '.gota-build-abc123/hermes-home', tmp_path / '.gota-build-abc123'
    home.mkdir(parents=True)
    compiler_db(home)
    (scratch / 'staging/.claims/abcdef12-1').mkdir(parents=True)
    module.keep_compiler_sessions(home, scratch, str(tmp_path / 'profile'))
    kept = tmp_path / 'profile/gota-compiler/abcdef12-1-abc123/state.db'
    assert compiler_session_ids(kept) == ['compiler-session']


def test_keeping_compiler_sessions_never_fails_the_build(tmp_path, capsys):
    module = load_build_module()
    home = tmp_path / '.gota-build-x/hermes-home'
    home.mkdir(parents=True)
    compiler_db(home)
    module.keep_compiler_sessions(home, home.parent, None)
    assert 'HERMES_HOME is not set' in capsys.readouterr().err
    blocked = tmp_path / 'profile'
    blocked.write_text('a file, not a directory')
    module.keep_compiler_sessions(home, home.parent, str(blocked))
    assert 'compiler sessions not kept' in capsys.readouterr().err
    module.keep_compiler_sessions(tmp_path / 'no-home', home.parent, str(tmp_path))  # no db: no-op
    assert not (tmp_path / 'gota-compiler').exists()


def test_upload_delegates_only_to_shared_cli(monkeypatch):
    upload_spec = importlib.util.spec_from_file_location('gota_upload_binding', ROOT / 'games/gota/tools/upload.py')
    upload = importlib.util.module_from_spec(upload_spec)
    upload_spec.loader.exec_module(upload)
    monkeypatch.setenv('RESEARCH_CLI', '/fake/shared-cli')
    monkeypatch.setattr(sys, 'argv', ['upload.py', 'E', '--build-dir', '/builds/E'])
    def fake_process(argv, **kwargs):
        assert argv == ['/fake/shared-cli', 'softmax', 'upload', 'E', '--build-dir', '/builds/E']
        return subprocess.CompletedProcess(argv, 7)
    monkeypatch.setattr(subprocess, 'run', fake_process)
    assert upload.main() == 7


@pytest.mark.parametrize('failure,exit_code', [(False, 1), (True, 3), ('environment', 3)])
def test_main_failure_exit_json(tmp_path, monkeypatch, capsys, failure, exit_code):
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, 'argv', ['build', str(tmp_path), '--out', str(tmp_path / 'out'), '--manifest', '/fake/manifest', '--json'])
    def fake_build(*args, **kwargs):
        if failure == 'environment':
            import pw_cli
            raise pw_cli.EnvironmentMissing('Hermes Python is not configured', 'set GOTA_HERMES_PYTHON')
        if failure:
            raise OSError('native compiler missing')
        return {'ok': False, 'gates': {'G1': {'passed': False}}}
    monkeypatch.setattr(module, 'build', fake_build)
    assert module.main() == exit_code
    assert json.loads(capsys.readouterr().out)['ok'] is False


def test_main_defaults_online_without_manifest(tmp_path, monkeypatch):
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, 'argv', ['build', str(tmp_path), '--out', str(tmp_path / 'out'), '--json'])
    monkeypatch.setenv('GOTA_MANIFEST', '/stale/historical-manifest.json')
    def fake_build(candidate, out, manifest, *, offline, timeout, budget, full_evaluation):
        assert manifest is None
        assert offline is False
        assert timeout is None
        assert budget is None
        assert full_evaluation is False
        return {'ok': True}
    monkeypatch.setattr(module, 'build', fake_build)
    assert module.main() == 0


def test_main_forwards_explicit_agent_limits(tmp_path, monkeypatch):
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('GOTA_AGENT_TIMEOUT_S', 'invalid')
    monkeypatch.setattr(sys, 'argv', ['build', str(tmp_path), '--out', str(tmp_path / 'out'),
                                     '--agent-timeout', '1400', '--agent-budget', '2400', '--json'])
    def fake_build(candidate, out, manifest, *, offline, timeout, budget, full_evaluation):
        assert timeout == '1400'
        assert budget == '2400'
        return {'ok': True}
    monkeypatch.setattr(module, 'build', fake_build)
    assert module.main() == 0


@pytest.mark.parametrize('flag', ['--agent-timeout', '--agent-budget'])
@pytest.mark.parametrize('value', ['0', '-1', 'abc', '1.5'])
def test_invalid_agent_limit_flag_retains_json_before_manifest(
        tmp_path, monkeypatch, capsys, flag, value):
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'validate_manifest',
                        lambda *a, **kw: pytest.fail('invalid timeout reached manifest lookup'))
    monkeypatch.setattr(sys, 'argv', ['build', str(tmp_path), '--out', str(tmp_path / 'out'),
                                     flag, value, '--json'])
    assert module.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'rejected'
    assert flag in result['error']['message']
    assert json.loads((tmp_path / 'out/report.json').read_text()) == result
    assert json.loads((tmp_path / 'out/version.json').read_text()) == result


@pytest.mark.parametrize('setting', ['GOTA_AGENT_TIMEOUT_S', 'GOTA_AGENT_BUDGET_S'])
@pytest.mark.parametrize('environment', ['0', '-1', 'abc', '1.5'])
def test_invalid_agent_limit_env_retains_error_before_manifest(
        tmp_path, monkeypatch, capsys, environment, setting):
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv(setting, environment)
    monkeypatch.setattr(module, 'validate_manifest',
                        lambda *a, **kw: pytest.fail('invalid timeout reached manifest lookup'))
    monkeypatch.setattr(sys, 'argv', ['build', str(tmp_path), '--out', str(tmp_path / 'out')])
    assert module.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'rejected'
    assert setting in result['error']['message']
    assert 'positive integer' in result['error']['message']
    assert json.loads((tmp_path / 'out/report.json').read_text()) == result


@pytest.mark.parametrize('setting', ['timeout', 'budget'])
@pytest.mark.parametrize('value', [0, -1, True, 1.5])
def test_invalid_explicit_agent_limit_stops_before_manifest(tmp_path, monkeypatch, value, setting):
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'validate_manifest',
                        lambda *a, **kw: pytest.fail('invalid timeout reached manifest lookup'))
    with pytest.raises(ValueError, match='positive integer'):
        module.build(tmp_path, tmp_path / 'out', **{setting: value})


def test_prebuilt_ignores_agent_timeout_env(tmp_path, monkeypatch):
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('GOTA_AGENT_TIMEOUT_S', 'invalid')
    monkeypatch.setenv('GOTA_AGENT_BUDGET_S', 'invalid')
    monkeypatch.setattr(module, 'validate_manifest', lambda *a, **kw: {'identity': 'fixture'})
    from games.gota.tools import prebuilt
    def fake_build(candidate, out, artifact, identity, *, started, full_evaluation):
        assert started
        assert identity == {'identity': 'fixture'}
        return {'ok': True}
    monkeypatch.setattr(prebuilt, 'build', fake_build)
    assert module.build(tmp_path, tmp_path / 'out', prebuilt=tmp_path / 'policy.bas',
                        timeout='invalid', budget='invalid')['ok']


def test_installed_compiler_gate_import_with_separate_worker_and_output(tmp_path):
    """Installed compiler import and G5 reader must not assume candidate ancestry."""
    import os
    import shutil

    installed = tmp_path / 'distribution'
    tools = installed / 'games/gota/compiler/tools'
    shutil.copytree(ROOT / 'games/gota/compiler/tools', tools,
                    ignore=shutil.ignore_patterns('bin', '__pycache__'))
    repository = tmp_path / 'repository'
    repository.mkdir()
    subprocess.run(['git', 'init', str(repository)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repository), '-c', 'user.name=Test',
                    '-c', 'user.email=test@invalid', 'commit', '--allow-empty', '-m', 'base'],
                   check=True, capture_output=True)
    worker = tmp_path / 'worker'
    subprocess.run(['git', '-C', str(repository), 'worktree', 'add', '--detach', str(worker)],
                   check=True, capture_output=True)
    out = tmp_path / 'output'
    out.mkdir()
    shutil.copytree(ROOT / 'games/gota/gates', installed / 'games/gota/gates',
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'games/gota/tools', installed / 'games/gota/tools',
                    ignore=shutil.ignore_patterns('bin', '__pycache__'))
    program = '''
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parents[3]))
sys.path.insert(0, sys.argv[1])
import pw_intent, pw_cli
assert pw_cli.default_out('pw_intent', 'episodes') == Path(sys.argv[2]) / 'analysis/pw_intent/episodes'
mapping = {'codes': {}, 'rules': [], 'flag_words': 1}
line = 'PWD v=2 t=0 r=0 c=0 i=0,0,0 h=0 p=0 f=0'
assert pw_intent.parse_telemetry_line(line, mapping)
from games.gota.gates import GateContext, g5_telemetry
out = Path(sys.argv[2])
(out / 'logs').mkdir()
for seat in range(10):
    (out / 'logs' / f'policy_agent_{seat}.log').write_text(line + '\\n')
result = g5_telemetry.compiler_logs(GateContext(out, episode_dir=out, compiler_map=mapping))
assert result.ok, result.details
assert len(result.details['seats']) == 10
'''
    environment = dict(os.environ, GOTA_CANDIDATE_REPO=str(worker),
                       GOTA_ANALYSIS_DIR=str(out / 'analysis'))
    result = subprocess.run([sys.executable, '-c', program, str(tools), str(out)],
                            cwd=worker, env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert not (installed / 'games/gota/compiler/analysis').exists()
