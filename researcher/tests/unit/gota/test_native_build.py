"""Native receipts fail closed on changed inputs or executable bytes."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from games.gota.tools import native_build


def commit(directory, files):
    directory.mkdir(parents=True)
    for name, value in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    for argv in [('init',), ('add', '.'), ('-c', 'user.name=Test', '-c', 'user.email=test@invalid',
                                         'commit', '-m', 'fixture')]:
        subprocess.run(['git', '-C', str(directory), *argv], check=True, capture_output=True)
    return subprocess.check_output(['git', '-C', str(directory), 'rev-parse', 'HEAD'], text=True).strip()


@pytest.fixture
def cached(tmp_path, monkeypatch):
    tools = tmp_path / 'tools'
    engine = tools / '.cache/polyworld'
    dep = tmp_path / 'dependency'
    dep_pin = commit(dep, {'src/library.nim': 'discard\n'})
    engine_pin = commit(engine, {'config.nims': '# config\n', 'src/engine.nim': 'discard\n',
                                 'coworld/dependencies.lock': f'library 1 local {dep_pin}\n'})
    deps = engine / 'tmp/coworld/deps'
    deps.mkdir(parents=True)
    (deps / 'library').symlink_to(dep, target_is_directory=True)
    for name in ('gate_probe.nim', 'build_nim.sh', 'native_build.py', 'replay_outcome.nim', 'engine.env'):
        (tools / name).write_text('fixture\n')
    copied = engine / 'examples/gods_of_the_arena/tools'
    copied.mkdir(parents=True)
    (copied / 'lab_gate_probe.nim').write_text('fixture\n')
    (copied / 'replay_outcome.nim').write_text('fixture\n')
    config = tools / 'effective.nims'
    config.write_text('effective config')
    monkeypatch.setattr(native_build, 'toolchain', lambda *args: {'nim': 'test compiler', 'configs': {str(config): native_build.digest(config)}})
    snapshot = native_build.inputs('gate_probe', engine_pin, tools=tools)
    binary = tools / 'bin' / f'gate_probe-{engine_pin[:8]}'
    binary.parent.mkdir()
    binary.write_bytes(b'fake local executable bytes')
    binary.chmod(0o755)
    native_build.write_receipt(binary, snapshot)
    return tools, engine, dep, engine_pin, binary, snapshot


def test_complete_receipt_accepts_exact_cache(cached):
    tools, _, _, pin, binary, _ = cached
    result = native_build.validate(binary, 'gate_probe', pin, tools=tools)
    assert result['binary_sha256'] == native_build.digest(binary)


@pytest.mark.parametrize('change', ['source', 'config', 'dependency', 'binary', 'receipt', 'extra_source', 'effective_config', 'copied_helper', 'local_helper', 'dependency_pin'])
def test_cache_rejects_changed_closure(cached, change):
    tools, engine, dep, pin, binary, _ = cached
    if change == 'effective_config': (tools / 'effective.nims').write_text('changed')
    if change == 'copied_helper': (engine / 'examples/gods_of_the_arena/tools/lab_gate_probe.nim').write_text('changed')
    if change == 'local_helper': (tools / 'gate_probe.nim').write_text('changed')
    if change == 'dependency_pin':
        subprocess.run(['git', '-C', str(dep), '-c', 'user.name=Test', '-c', 'user.email=test@invalid',
                        'commit', '--allow-empty', '-m', 'different pin'], check=True, capture_output=True)
    if change == 'source': (engine / 'src/engine.nim').write_text('changed')
    if change == 'config': (engine / 'config.nims').write_text('changed')
    if change == 'dependency': (dep / 'src/library.nim').write_text('changed')
    if change == 'binary': binary.write_bytes(b'replaced executable')
    if change == 'extra_source': (engine / 'src/new.nim').write_text('discard')
    if change == 'receipt':
        receipt = json.loads(native_build.receipt_path(binary).read_text())
        receipt['inputs']['engine']['commit'] = '0' * 40
        native_build.receipt_path(binary).write_text(json.dumps(receipt))
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        native_build.validate(binary, 'gate_probe', pin, tools=tools)


def test_post_compile_change_cannot_get_receipt(cached):
    tools, engine, _, pin, binary, before = cached
    (engine / 'src/extra.nim').write_text('discard')
    with pytest.raises(ValueError, match='changed during compilation'):
        native_build.finish_build(binary, before, 'gate_probe', pin, tools=tools)


def test_prepare_populates_empty_cache_at_exact_pin(tmp_path, monkeypatch):
    monkeypatch.delenv('GOTA_DEPENDENCY_ROOT', raising=False)
    upstream = tmp_path / 'upstream'
    dependency = tmp_path / 'dependency'
    dep_pin = commit(dependency, {'src/library.nim': 'discard\n'})
    pin = commit(upstream, {'config.nims': '# config\n',
                          'coworld/dependencies.lock': f'library 1 {dependency} {dep_pin}\n'})
    tools = tmp_path / 'relocated install/tools'
    clone = native_build.prepare(pin, tools=tools, engine_source=str(upstream))
    assert native_build.repository(clone, pin)['commit'] == pin
    assert native_build.repository(clone / 'tmp/coworld/deps/library', dep_pin)['commit'] == dep_pin
    assert native_build.prepare(pin, tools=tools, engine_source=str(upstream)) == clone
    (clone / 'config.nims').write_text('changed')
    with pytest.raises(subprocess.CalledProcessError):
        native_build.prepare(pin, tools=tools, engine_source=str(upstream))


def test_prepare_supports_distinct_pins_and_refuses_changed_dependency(tmp_path, monkeypatch):
    monkeypatch.delenv('GOTA_DEPENDENCY_ROOT', raising=False)
    upstream = tmp_path / 'upstream'
    dependency = tmp_path / 'dependency'
    dep_pin = commit(dependency, {'src/library.nim': 'discard\n'})
    first = commit(upstream, {'config.nims': '# old config\n',
                            'coworld/dependencies.lock': f'library 1 {dependency} {dep_pin}\n'})
    (upstream / 'config.nims').write_text('# new config\n')
    subprocess.run(['git', '-C', str(upstream), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(upstream), '-c', 'user.name=Test',
                    '-c', 'user.email=test@invalid', 'commit', '-m', 'next pin'],
                   check=True, capture_output=True)
    second = native_build.git(upstream, 'rev-parse', 'HEAD')
    tools = tmp_path / 'install/tools'
    old = native_build.prepare(first, tools=tools, engine_source=str(upstream))
    new = native_build.prepare(second, tools=tools, engine_source=str(upstream))
    assert old != new
    assert native_build.git(old, 'rev-parse', 'HEAD') == first
    assert native_build.git(new, 'rev-parse', 'HEAD') == second
    (new / 'tmp/coworld/deps/library/src/library.nim').write_text('changed\n')
    with pytest.raises(subprocess.CalledProcessError):
        native_build.prepare(second, tools=tools, engine_source=str(upstream))


def test_offline_build_does_not_prepare_missing_sources(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('offline build attempted source preparation')

    monkeypatch.setattr(native_build, 'prepare', forbidden)
    with pytest.raises(FileNotFoundError):
        native_build.build('gate_probe', '0' * 40, tools=tmp_path / 'tools')


@pytest.mark.skipif(os.geteuid() == 0, reason='root ignores file permissions')
def test_read_only_install_builds_in_the_profile(tmp_path, monkeypatch):
    """A hosted profile is installed read-only, so sources and builds go to its own state."""
    monkeypatch.delenv('GOTA_DEPENDENCY_ROOT', raising=False)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'profile'))
    dependency = tmp_path / 'dependency'
    dep_pin = commit(dependency, {'src/library.nim': 'discard\n'})
    pin = commit(tmp_path / 'upstream', {'config.nims': '# config\n',
                                         'coworld/dependencies.lock': f'library 1 {dependency} {dep_pin}\n'})
    tools = tmp_path / 'install/tools'
    tools.mkdir(parents=True)
    tools.chmod(0o555)
    try:
        clone = native_build.prepare(pin, tools=tools, engine_source=str(tmp_path / 'upstream'))
        assert clone.is_relative_to(tmp_path / 'profile/cache/gota/.cache')
        assert not any(tools.iterdir())
    finally:
        tools.chmod(0o755)
    assert native_build.build_root(tools) == tools  # a writable checkout keeps its own folders
