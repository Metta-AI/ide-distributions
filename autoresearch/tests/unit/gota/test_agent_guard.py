"""Exercise the process guard without ever attempting a real agent launch."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

# The session conftest audit hook remains active; load a separate checker for
# controlled path cases without changing the session's protection state.
_spec = importlib.util.spec_from_file_location('guard_under_test', Path(__file__).with_name('conftest.py'))
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)
# Only this duplicate checker is inactive; pytest's conftest hook stays active.
guard._state['active'] = False


@pytest.mark.parametrize('name', ['codex', 'claude', 'hermes'])
def test_actual_spawn_blocked_before_exec(name):
    with pytest.raises(RuntimeError, match='test blocked real agent executable'):
        subprocess.run(['/not-a-real-agent-install/' + name], check=True)


def test_fake_allowed_and_symlink_escape_blocked(tmp_path):
    guard._state['root'] = tmp_path.resolve()
    fake = tmp_path / 'hermes'
    fake.write_text('fake')
    guard.check_spawn(str(fake), [str(fake)])
    fake.unlink()
    fake.symlink_to('/not-a-real-agent-install/hermes')
    with pytest.raises(RuntimeError, match='real agent'):
        guard.check_spawn(str(fake), [str(fake)])


@pytest.mark.parametrize('argv', [
    ['/bin/sh', '-c', '/not-a-real-agent-install/codex --version'],
    ['/bin/sh', '-c', 'env claude --version'],
    ['/bin/sh', '-c', '$AGENT --version'],
    ['/usr/bin/env', '/not-a-real-agent-install/hermes', '--version'],
])
def test_shell_and_env_wrappers_blocked(argv):
    guard._state['root'] = None
    with pytest.raises(RuntimeError, match='test blocked'):
        guard.check_spawn(argv[0], argv)


def test_symlink_chain_cannot_hide_agent_name(tmp_path):
    guard._state['root'] = tmp_path / 'allowed'
    agent = tmp_path / 'codex'
    agent.symlink_to('/outside/install/codex.js')
    alias = tmp_path / 'alias'
    alias.symlink_to(agent)
    with pytest.raises(RuntimeError, match='real agent'):
        guard.check_spawn(str(alias), [str(alias)])


@pytest.mark.parametrize('arguments', [
    ['env', '/bin/sh', '-c', '/not-a-real-agent-install/hermes'],
    ['env', 'PATH=/outside-root', 'hermes', '--version'],
])
def test_env_wrappers_fail_closed_with_full_argv(tmp_path, arguments):
    guard._state['root'] = tmp_path.resolve()
    fake = tmp_path / 'hermes'
    fake.write_text('fake')
    fake.chmod(0o755)
    with pytest.raises(RuntimeError, match='test blocked'):
        guard.check_spawn('/usr/bin/env', arguments, {'PATH': str(tmp_path)})


@pytest.mark.parametrize('target,arguments', [('/bin/sh', ['alias', '-c', 'hermes']),
                                           ('/usr/bin/env', ['alias', 'hermes'])])
def test_resolved_wrapper_names_fail_closed(tmp_path, target, arguments):
    alias = tmp_path / 'alias'
    alias.symlink_to(target)
    with pytest.raises(RuntimeError, match='test blocked'):
        guard.check_spawn(str(alias), arguments)
