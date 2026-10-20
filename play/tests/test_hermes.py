import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from support.hermes_python import hermes_python

DISTRIBUTION = Path(__file__).resolve().parents[1]


def clean_env(home):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("HERMES_", "RESEARCH_", "PLAY_", "OPENROUTER_"))
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


def run(argv, env, timeout=120):
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, check=False, timeout=timeout
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.hermes
def test_clean_export_registers_exactly_the_play_tools(tmp_path):
    # A clean export: the folder as committed, without the test run's own tool output (the CI
    # container's checkout is not a git repository that `git archive` can read).
    exported = tmp_path / "export"
    ignore = shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".ruff_cache")
    shutil.copytree(DISTRIBUTION, exported / "play", ignore=ignore)
    home = tmp_path / "host"
    home.mkdir()
    env = clean_env(home)
    for source, name in ((exported / "play", "play"), (exported / "play/builder", "builder")):
        run(["hermes", "profile", "install", str(source), "--name", name, "--yes"], env)
    profile = home / "profiles/play"
    assert (home / "profiles/builder/SOUL.md").read_text().startswith("# Builder")

    # Real discovery and registration in Hermes; no models or API calls.
    code = """
import json, sys
sys.path.insert(0, sys.argv[1] + '/src')
from hermes_cli.plugins import get_plugin_manager
from tools.registry import registry
from play.plugin import TOOLS, schema
manager = get_plugin_manager()
manager.discover_and_load()
info = next(p for p in manager.list_plugins() if p['name'] == 'play')
assert not info['error'] and info['enabled'], info
loaded = next(p for p in manager._plugins.values() if p.manifest.name == 'play')
assert loaded.tools_registered == list(TOOLS), loaded.tools_registered
for name in TOOLS:
    entry = registry.get_entry(name)
    assert entry.toolset == 'play' and entry.schema == schema(name), name
print(json.dumps({'ok': True}))
"""
    output = run([hermes_python(), "-c", code, str(profile)], {**env, "HERMES_HOME": str(profile)})
    assert json.loads(output.splitlines()[-1])["ok"]
    # The builder profile does not load the play tools.
    code = """
from hermes_cli.plugins import get_plugin_manager
manager = get_plugin_manager()
manager.discover_and_load()
assert not any(p['name'] == 'play' and p['enabled'] for p in manager.list_plugins())
"""
    builder = home / "profiles/builder"
    run([hermes_python(), "-c", code], {**env, "HERMES_HOME": str(builder)})
