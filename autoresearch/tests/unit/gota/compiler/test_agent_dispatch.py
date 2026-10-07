"""Unknown compiler agents must never fall through to a real agent executable."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'games/gota/compiler/tools'))
import pw_cli
import strategy_build as sb
from strategy_format import parse_strategy


@pytest.mark.parametrize('agent', ['hermes', 'unknown-backend', '', 'CODEX'])
def test_unknown_agent_command_fails_closed(agent, tmp_path):
    with pytest.raises(pw_cli.UsageError, match='unknown compiler agent'):
        sb.agent_command(agent, 'unused', tmp_path)


@pytest.mark.parametrize('agent', ['claude', 'codex'])
def test_supported_agent_command_is_explicit(agent, tmp_path):
    command = sb.agent_command(agent, 'test-model', tmp_path)
    assert command[0] == agent
    assert command[command.index('--model') + 1] == 'test-model'


@pytest.fixture
def isolated_generation(tmp_path, monkeypatch):
    """A PATH and process allowlist prevent even failing tests from calling an agent."""
    binary_dir = tmp_path / 'bin'
    binary_dir.mkdir()
    git_executable = shutil.which('git')
    assert git_executable is not None
    (binary_dir / 'git').symlink_to(git_executable)
    (binary_dir / 'python3').symlink_to(sys.executable)
    fake_hermes = binary_dir / 'hermes'
    fake_hermes.write_text('#!/usr/bin/env python3\nraise SystemExit("unexpected fake Hermes call")\n')
    fake_hermes.chmod(0o755)
    monkeypatch.setenv('PATH', str(binary_dir))
    assert shutil.which('codex') is None
    assert shutil.which('claude') is None
    calls = []
    real_run = subprocess.run

    def local_only(command, **kwargs):
        assert command[0] in ('git', 'hermes'), f'forbidden test subprocess: {command[0]}'
        calls.append(command)
        return real_run(command, **kwargs)

    monkeypatch.setattr(sb.subprocess, 'run', local_only)
    source = tmp_path / 'STRATEGY.md'
    fixture = Path(__file__).parent / 'fixtures/strategy_trivial/STRATEGY.md'
    source.write_text(fixture.read_text().replace(',10,11,12,13,14,15', ''))
    stage = tmp_path / 'stages/abcdef12-1'
    (stage / 'units').mkdir(parents=True)
    monkeypatch.setattr(sb, 'REPO', tmp_path)
    monkeypatch.setattr(sb, 'STAGING', stage.parent)
    order = {'build_id': stage.name, 'source_path': source.name, 'source_hashes': {},
             'previous_build': None, 'components': sb.component_map(parse_strategy(source, backend='gota')),
             'units': {'K.position': 'new', 'C.idle': 'new'},
             'compiler': {'agent': 'hermes', 'model': 'unused'}}
    return order, stage, calls


@pytest.mark.parametrize('agent', ['hermes', 'unknown-backend'])
def test_generate_rejects_unknown_agent_without_launch(isolated_generation, agent):
    order, stage, calls = isolated_generation
    order['compiler']['agent'] = agent
    with pytest.raises(pw_cli.UsageError, match='unknown compiler agent'):
        sb.generate(order)
    assert all(command[0] == 'git' for command in calls)
    assert not (stage / 'agent.log').exists()
    assert not list((stage / 'units').iterdir())


def test_prepare_rejects_unknown_agent_before_model_lookup(tmp_path, monkeypatch):
    source = tmp_path / 'STRATEGY.md'
    fixture = Path(__file__).parent / 'fixtures/strategy_trivial/STRATEGY.md'
    source.write_text(fixture.read_text().replace(',10,11,12,13,14,15', ''))
    monkeypatch.setattr(sb, 'committed_inputs', lambda _: {})
    monkeypatch.setattr(sb, 'git', lambda *args: 'abcdef12')
    monkeypatch.setattr(sb, 'previous_build', lambda *args: None)

    def no_process(*args, **kwargs):
        pytest.fail('unknown agent must fail before any subprocess')

    monkeypatch.setattr(sb.subprocess, 'run', no_process)
    with pytest.raises(pw_cli.UsageError, match='unknown compiler agent'):
        sb.prepare(source, agent='unknown-backend')
