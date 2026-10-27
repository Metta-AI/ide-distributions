import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from support.hermes_python import hermes_python

DISTRIBUTION = Path(__file__).resolve().parents[1]


def clean_env(home):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("HERMES_", "RESEARCH_", "AUTORESEARCH_", "OPENROUTER_"))
        and k
        not in {
            "VIRTUAL_ENV",
            "UV_PROJECT_ENVIRONMENT",
            "PYTHONHOME",
            "PYTHONPATH",
            "SOFTMAX_PLAYER_CREDENTIAL",
        }
    }
    return {**env, "HERMES_HOME": str(home)}


@pytest.mark.hermes
def test_native_board_behaviour(tmp_path):
    env = clean_env(tmp_path / "host/profiles/orchestrator")
    env["PYTHONPATH"] = str(DISTRIBUTION / "src")
    result = subprocess.run(
        [hermes_python(), str(DISTRIBUTION / "tests/lifecycle_scenarios.py")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["ok"]


@pytest.mark.hermes
def test_clean_export_installs_paused(tmp_path):
    exported = tmp_path / "export"
    exported.mkdir()
    archive = subprocess.run(
        ["git", "-C", str(DISTRIBUTION.parent), "archive", "HEAD", "orchestrator"],
        check=True,
        capture_output=True,
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(exported)], input=archive, check=True)
    home = tmp_path / "host"
    home.mkdir()
    env = clean_env(home)
    result = subprocess.run(
        [
            "hermes",
            "profile",
            "install",
            str(exported / "orchestrator"),
            "--name",
            "orchestrator",
            "--yes",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    profile = home / "profiles/orchestrator"
    jobs = json.loads((profile / "cron/jobs.json").read_text())["jobs"]
    assert len(jobs) == 1 and jobs[0]["name"] == "supervisor"
    assert not jobs[0]["enabled"] and jobs[0]["state"] == "paused"
    assert jobs[0]["no_agent"] and jobs[0]["script"] == "supervisor_gate.py"
    expected_tools = yaml.safe_load((profile / "plugins/orchestrator/plugin.yaml").read_text())[
        "provides_tools"
    ]
    # Real discovery/registration in Hermes; no models or API calls.
    code = """
import json
import sys
import hashlib
import subprocess
from contextlib import closing
from pathlib import Path
from tools.registry import registry
from hermes_cli.plugins import get_plugin_manager, get_plugin_toolsets
from hermes_cli import kanban_db as kb
from hermes_cli.kanban_db_boards import create_board
from hermes_cli.kanban_db_workspace import resolve_workspace, set_workspace_path
manager = get_plugin_manager()
manager.discover_and_load()
from orchestrator.board import connection
plugin = next(p for p in manager.list_plugins() if p['name'] == 'orchestrator')
assert not plugin['error'], plugin
expected = json.loads(sys.argv[2])
assert plugin['enabled'] and plugin['tools'] == len(expected), plugin
assert set(registry.get_tool_names_for_toolset('orchestrator')) == set(expected)
assert registry.get_schema('ide_workspace')['parameters']['required'] == ['request_id', 'operation']
schema = registry.get_schema('submit_requested_version')
assert set(schema['parameters']['required']) == {'id', 'run_id', 'version', 'strategy', 'request', 'league_id'}, schema
assert any(toolset[0] == 'orchestrator' for toolset in get_plugin_toolsets())
# capabilities resolves the api_server session's toolsets from config.yaml.
listing = json.loads(registry.dispatch('capabilities', {'kind': 'tools'}))
assert {'orchestrator', 'terminal', 'file', 'code_execution'} <= set(listing['tools']), listing
assert 'skills' not in listing

# Complete a real native task, let Hermes clean its worktree, then review its
# committed text and binary evidence through the installed agent tools.
home = Path(sys.argv[1])
root = home.parent.parent
seed = root / 'evidence-seed'
seed.mkdir()
def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], stderr=subprocess.PIPE).decode().strip()
git(seed, 'init', '-b', 'main')
git(seed, 'config', 'user.name', 'Artifact system test')
git(seed, 'config', 'user.email', 'test@example.invalid')
(seed / 'seed.txt').write_text('initial')
git(seed, 'add', '.')
git(seed, 'commit', '-m', 'seed')
remote = root / 'evidence.git'
git(root, 'clone', '--bare', str(seed), str(remote))
worker = root / 'profiles/researcher-1'
worker.mkdir()
clone = worker / 'workspace'
git(root, 'clone', str(remote), str(clone))
create_board('autoresearch')
created = json.loads(registry.dispatch('card_create', {
    'command_id': 'artifact-system:create', 'assignee': 'researcher-1',
    'title': 'Produce verifiable evidence', 'body': 'Commit the report and binary artifact',
}))
assert created['ok'], created
task_id = created['data']['card']['id']
with closing(connection()) as conn:
    task = kb.claim_task(conn, task_id)
    worktree = resolve_workspace(task, board='autoresearch')
    set_workspace_path(conn, task_id, worktree)
payload = bytes(range(256)) * 4096
(worktree / 'evidence/nested').mkdir(parents=True)
(worktree / 'evidence/nested/replay.bin').write_bytes(payload)
results = ''.join(json.dumps({'tick': tick, 'errors': 0}) + '\\n' for tick in range(12000))
(worktree / 'evidence/results.jsonl').write_text(results)
(worktree / 'report.md').write_text('Recorded evidence')
(worktree / 'policy.bas').write_text('PRINT "candidate"')
(worktree / 'link').symlink_to('evidence/nested/replay.bin')
git(worktree, 'config', 'user.name', 'Artifact system test')
git(worktree, 'config', 'user.email', 'test@example.invalid')
git(worktree, 'add', '.')
git(worktree, 'commit', '-m', 'record evidence')
git(worktree, 'push', 'origin', task.branch_name)
commit = git(worktree, 'rev-parse', 'HEAD')
identity = {'task_id': task_id, 'run_id': task.current_run_id, 'commit': commit}
request = {**identity, 'paths': ['evidence/nested/replay.bin', 'evidence/results.jsonl', 'policy.bas'],
           'expected_sha256': {'evidence/nested/replay.bin': hashlib.sha256(payload).hexdigest()}}
assert not json.loads(registry.dispatch('card_artifacts', request))['ok']
assert not json.loads(registry.dispatch('card_files', identity))['ok']
with closing(connection()) as conn:
    assert kb.complete_task(conn, task_id, summary='Evidence committed', expected_run_id=task.current_run_id)
assert not worktree.exists(), 'Native completion must exercise cleaned-worktree recovery'
listing = json.loads(registry.dispatch('card_files', {**identity, 'limit': 2}))
assert listing['ok'], listing
files = listing['data']['files']
while listing['data']['next_offset'] is not None:
    listing = json.loads(registry.dispatch('card_files', {
        **identity, 'limit': 2, 'offset': listing['data']['next_offset'],
    }))
    assert listing['ok'], listing
    files.extend(listing['data']['files'])
assert {item['path']: item['kind'] for item in files} == {
    'seed.txt': 'blob', 'evidence': 'tree', 'link': 'symlink', 'policy.bas': 'blob', 'report.md': 'blob',
}, files
nested = json.loads(registry.dispatch('card_files', {**identity, 'directory': 'evidence/nested'}))
assert nested['ok'] and nested['data']['files'][0]['path'] == request['paths'][0], nested
assert worktree.exists()
(worktree / 'evidence/nested/replay.bin').write_bytes(b'uncommitted change')
inspected = json.loads(registry.dispatch('card_artifacts', request))
assert inspected['ok'] and inspected['data']['status'] == 'inspected', inspected
assert inspected['data']['artifacts'][0]['matches'] is True
assert inspected['data']['artifacts'][0]['bytes'] == len(payload)
assert inspected['data']['artifacts'][1]['matches'] is None
mismatch = json.loads(registry.dispatch('card_artifacts', {
    **request, 'export': True, 'expected_sha256': {'evidence/nested/replay.bin': '0' * 64},
}))
assert mismatch['ok'] and mismatch['data']['status'] == 'mismatch', mismatch
assert not (home / 'cache/card-artifacts').exists(), 'Mismatch must not publish any files'
exported = json.loads(registry.dispatch('card_artifacts', {**request, 'export': True}))
assert exported['ok'] and exported['data']['status'] == 'exported', exported
directory = Path(exported['data']['directory'])
assert directory.is_relative_to(home / 'cache/card-artifacts')
assert (directory / request['paths'][0]).read_bytes() == payload
assert (directory / 'policy.bas').read_text() == 'PRINT "candidate"'
rows = [json.loads(line) for line in (directory / 'evidence/results.jsonl').read_text().splitlines()]
assert len(rows) == 12000 and all(row['errors'] == 0 for row in rows)
manifest = json.loads(Path(exported['data']['manifest']).read_text())
assert all(manifest[key] == value for key, value in identity.items())
for item in manifest['artifacts']:
    copied = directory / item['path']
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == item['sha256']
    assert copied.stat().st_mode & 0o777 == 0o600
(directory / 'policy.bas').write_text('local analysis copy')
again = json.loads(registry.dispatch('card_artifacts', {**request, 'export': True}))
assert again['ok'] and again['data']['directory'] != str(directory), again
assert (directory / 'policy.bas').read_text() == 'local analysis copy'
assert (Path(again['data']['directory']) / 'policy.bas').read_text() == 'PRINT "candidate"'
for path in ['../report.md', 'link', '/etc/passwd', 'evidence']:
    rejected = json.loads(registry.dispatch('card_artifacts', {
        **identity, 'paths': [path], 'export': True,
    }))
    assert not rejected['ok'], rejected
assert not json.loads(registry.dispatch('card_files', {**identity, 'directory': '../'}))['ok']
report = json.loads(registry.dispatch('card_report', {**identity, 'path': 'report.md'}))
assert report['ok'] and report['data']['content'] == 'Recorded evidence', report
assert not json.loads(registry.dispatch('card_report', {**identity, 'path': 'evidence/results.jsonl'}))['ok']
assert (worktree / 'evidence/nested/replay.bin').read_bytes() == b'uncommitted change'
print(json.dumps({'ok': True}))
"""
    result = subprocess.run(
        [hermes_python(), "-c", code, str(profile), json.dumps(expected_tools)],
        env={**env, "HERMES_HOME": str(profile), "HERMES_SESSION_PLATFORM": "api_server"},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    # Provision the installed distribution using its real frozen lock, independent of the source tree.
    bootstrap = """
import sys
from pathlib import Path
home = Path(sys.argv[1])
sys.path.insert(0, str(home / 'src'))
from orchestrator.launch import ensure_venv
assert ensure_venv(home)
assert not ensure_venv(home)
"""
    result = subprocess.run(
        [hermes_python(), "-c", bootstrap, str(profile)],
        env={**env, "HERMES_HOME": str(profile)},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    subprocess.run(
        [str(profile / "local/venv/bin/python"), "-c", "import orchestrator.gate"],
        cwd=profile,
        env={**env, "HERMES_HOME": str(profile)},
        check=True,
    )


@pytest.mark.hermes
@pytest.mark.parametrize("capacity", [1, 3])
def test_native_dispatch_capacity(tmp_path, capacity):
    result = subprocess.run(
        [hermes_python(), str(DISTRIBUTION / "tests/capacity_scenarios.py"), str(capacity)],
        env=clean_env(tmp_path / "host"),
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["ok"]
