"""Fresh install of the researcher distribution on real Hermes (design test T1).

Proves, from a copy outside the source tree: the payload arrives; the bootstrap script syncs
the venv, writes the CLI shim and ``RESEARCH_CLI``, and seeds the shared bare canonical
repository; a second researcher on the same host clones it instead; Hermes discovers the
guard plugin; ``hermes profile update`` keeps user state. No model is called.
"""

import json
import os
import subprocess

import pytest
import yaml
from conftest import FAKE_CREDENTIAL, write_profile_env
from support.hermes_python import hermes_python

pytestmark = pytest.mark.hermes


def bootstrap(install, profile_dir=None) -> subprocess.CompletedProcess:
    profile_dir = profile_dir or install.profile_dir
    scripts = profile_dir / "scripts"
    env = install.profile_env()
    env["HERMES_HOME"] = str(profile_dir)
    result = subprocess.run(
        [hermes_python(), str(scripts / "bootstrap.py")],
        check=False, cwd=scripts, env=env, capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return result


def git(repo, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_payload_present(install):
    profile = install.profile_dir
    for relative in (
        "scripts/bootstrap.py",
        "src/ide_research_core/launch.py",
        "src/ide_research_core/schemas/entity/claim.json",
        "uv.lock",
        "plugins/researcher_guard/plugin.yaml",
        "doctrine/VERSION",
    ):
        assert (profile / relative).is_file(), relative
    assert not (profile / "local").exists()
    assert not (profile / "bin" / "research").exists()
    assert json.loads((profile / "cron" / "jobs.json").read_text())["jobs"] == []


def test_bootstrap_seeds_the_shared_canonical_and_writes_the_cli(install):
    result = bootstrap(install)
    data = json.loads(result.stdout.strip().splitlines()[-1])["data"]
    assert any("seeded" in action for action in data["actions"]), data

    profile = install.profile_dir
    assert (profile / "local" / "venv" / ".lock-sha").is_file()
    assert not (profile / "local" / "venv" / "bin" / "pytest").exists()  # --no-dev
    shim = profile / "bin" / "research"
    assert shim.is_file() and os.access(shim, os.X_OK)
    assert f"RESEARCH_CLI={shim}" in (profile / ".env").read_text().splitlines()
    clone = profile / "workspace"
    assert (clone / "AGENTS.md").is_file()
    assert git(install.remote, "rev-parse", "refs/heads/main") == git(clone, "rev-parse", "HEAD")

    summary = subprocess.run(
        [str(shim), "records", "summary"], env=install.profile_env(HERMES_HOME=str(profile)),
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert json.loads(summary.stdout)["ok"], summary.stdout + summary.stderr

    stamp = (profile / "local" / "venv" / ".lock-sha").stat().st_mtime
    bootstrap(install)  # idempotent: no resync, no new commit
    assert (profile / "local" / "venv" / ".lock-sha").stat().st_mtime == stamp


def test_a_second_researcher_clones_the_same_canonical(install):
    bootstrap(install)
    install.hermes("profile", "install", str(install.source), "--name", "researcher-2", "-y")
    second = install.hermes_home / "profiles" / "researcher-2"
    write_profile_env(second, install.remote)
    data = json.loads(bootstrap(install, second).stdout.strip().splitlines()[-1])["data"]
    assert any("cloned" in action for action in data["actions"]), data
    assert git(second / "workspace", "rev-parse", "HEAD") == git(
        install.remote, "rev-parse", "main"
    )


def test_hermes_discovers_the_guard_plugin(install):
    listed = install.hermes("-p", install.profile, "plugins", "list").stdout
    assert "researcher_guard" in listed, listed


def test_update_preserves(install):
    profile = install.profile_dir
    bootstrap(install)
    shim = profile / "bin" / "research"
    (profile / "workspace" / "x").write_text("user file\n")
    (profile / "memories").mkdir(exist_ok=True)
    (profile / "memories" / "MEMORY.md").write_text("remember this\n")
    install.hermes("-p", install.profile, "config", "set", "agent.max_turns", "77")

    marker = "# update marker\n"
    script = install.source / "scripts" / "bootstrap.py"
    script.write_text(script.read_text() + marker)

    install.hermes("profile", "update", install.profile, "-y")

    assert (profile / "workspace" / "x").read_text() == "user file\n"
    assert (profile / "memories" / "MEMORY.md").read_text() == "remember this\n"
    assert yaml.safe_load((profile / "config.yaml").read_text())["agent"]["max_turns"] == 77
    assert (profile / "scripts" / "bootstrap.py").read_text().endswith(marker)
    assert f"RESEARCH_CLI={shim}" in (profile / ".env").read_text().splitlines()
    assert shim.is_file() and os.access(shim, os.X_OK)
    assert FAKE_CREDENTIAL  # the profile .env written by the fixture survives as user state


def test_guard_preserves_native_completion_after_retained_update(install):
    profile = install.profile_dir
    config_path = profile / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["plugins"]["entries"] = {"researcher_guard": {"allow_tool_override": True}}
    config["agent"]["max_turns"] = 77
    config_path.write_text(yaml.safe_dump(config))
    (profile / "memories").mkdir(exist_ok=True)
    (profile / "memories/MEMORY.md").write_text("Retain this memory.\n")
    (profile / "workspace").mkdir(exist_ok=True)
    (profile / "workspace/user.txt").write_text("Retain this file.\n")
    (profile / "sessions").mkdir(exist_ok=True)
    (profile / "sessions/history.jsonl").write_text('{"role":"user","content":"Retain chat"}\n')
    install.hermes("profile", "update", install.profile, "-y")
    assert yaml.safe_load(config_path.read_text()) == config
    code = """
import json
import tools.kanban_tools
from hermes_cli.plugins import get_plugin_manager
from tools.registry import registry
native = registry.get_entry('kanban_complete')
manager = get_plugin_manager()
manager.discover_and_load()
plugin = next(p for p in manager.list_plugins() if p['name'] == 'researcher_guard')
assert not plugin['error'], plugin
entry = registry.get_entry('kanban_complete')
assert entry.handler is native.handler and entry.check_fn is native.check_fn
assert entry.schema == native.schema
print(json.dumps({'ok': True}))
"""
    result = subprocess.run(
        [hermes_python(), "-c", code],
        env=install.profile_env(HERMES_HOME=str(profile)),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert yaml.safe_load(config_path.read_text())["agent"]["max_turns"] == 77
    assert (profile / "memories/MEMORY.md").read_text() == "Retain this memory.\n"
    assert (profile / "workspace/user.txt").read_text() == "Retain this file.\n"
    assert (
        profile / "sessions/history.jsonl"
    ).read_text() == '{"role":"user","content":"Retain chat"}\n'
