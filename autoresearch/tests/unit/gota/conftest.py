"""Prevent owned tests from accidentally launching installed model-agent CLIs.

This process guard is a test safety boundary, not an operating-system sandbox.
Dispatch tests must also use a strict PATH/process allowlist. Fake executables
must resolve inside pytest's temporary root, including through symlinks.
"""
import os
import shlex
import shutil
import sys
from pathlib import Path

import pytest

AGENTS = {'codex', 'claude', 'hermes'}
SHELLS = {'sh', 'bash', 'zsh', 'dash', 'fish'}
_state = {'active': True, 'root': None}


def check_spawn(executable, arguments, environment=None, cwd=None):
    """Fail before exec for installed agents, including shell and symlink paths."""
    env = os.environ if environment is None else environment
    cwd = Path(cwd or os.getcwd())
    executable = os.fsdecode(executable)
    candidate = Path(executable)
    if '/' not in executable:
        located = shutil.which(executable, path=env.get('PATH', os.defpath))
        candidate = Path(located) if located else cwd / executable
    elif not candidate.is_absolute():
        candidate = cwd / candidate
    resolved = candidate.resolve()
    link = candidate
    names = {candidate.name, resolved.name}
    visited = set()
    while link.is_symlink() and link not in visited:
        visited.add(link)
        target = link.readlink()
        link = target if target.is_absolute() else link.parent / target
        names.add(link.name)
    if names & AGENTS:
        root = _state['root']
        if root is None or not resolved.is_relative_to(root):
            raise RuntimeError(f'test blocked real agent executable: {candidate}')
    args = shlex.split(arguments) if isinstance(arguments, str) else list(arguments)
    if names & SHELLS and any(arg.startswith('-') and 'c' in arg[1:] for arg in args[1:]):
        # A shell can alter PATH, expand aliases, or conceal a command after a
        # separator. Owned tests have no need for shell expressions.
        raise RuntimeError('test blocked shell command string; use direct argv')
    if 'env' in names:
        raise RuntimeError('test blocked env wrapper; use direct argv and explicit env')


def _audit(event, args):
    if not _state['active']:
        return
    if event == 'subprocess.Popen':
        executable, arguments, cwd, env = args
        check_spawn(executable, arguments, env, cwd)
    elif event in ('os.exec', 'os.posix_spawn'):
        executable, arguments, env = args
        check_spawn(executable, arguments, env)
    elif event == 'os.system':
        check_spawn('/bin/sh', ['sh', '-c', os.fsdecode(args[0])])


sys.addaudithook(_audit)


@pytest.fixture(scope='session', autouse=True)
def prevent_real_agents(tmp_path_factory):
    _state['root'] = tmp_path_factory.getbasetemp().resolve()
    _state['active'] = True
    yield
    _state['active'] = False
