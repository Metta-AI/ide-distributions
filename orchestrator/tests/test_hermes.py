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
from tools.registry import registry
from hermes_cli.plugins import get_plugin_manager, get_plugin_toolsets
manager = get_plugin_manager()
manager.discover_and_load()
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
