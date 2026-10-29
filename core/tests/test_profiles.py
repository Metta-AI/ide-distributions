"""The two profile shells: manifests, configs, plugin shims and the watcher job."""

import ast
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
PROFILES = {"orchestrator": REPO / "orchestrator", "researcher": REPO / "researcher"}
STDLIB = set(sys.stdlib_module_names) | {"research_core", "hermes_constants"}


@pytest.mark.parametrize("role", sorted(PROFILES))
def test_distribution_manifest(role):
    manifest = yaml.safe_load((PROFILES[role] / "distribution.yaml").read_text())
    assert manifest["name"] == role
    assert manifest["hermes_requires"] == ">=0.21.6"
    names = {entry["name"] for entry in manifest["env_requires"]}
    assert {"SOFTMAX_PLAYER_ID", "SOFTMAX_PLAYER_CREDENTIAL", "IDE_WORKSPACE"} <= names
    assert ("SOFTMAX_USER_CREDENTIAL" in names) == (role == "orchestrator")
    for entry in manifest["env_requires"]:
        assert entry["description"].strip()


@pytest.mark.parametrize("role", sorted(PROFILES))
def test_config_has_protected_keys_and_no_gates(role):
    config = yaml.safe_load((PROFILES[role] / "config.yaml").read_text())
    assert config["approvals"]["mode"] == "off"
    assert "deny" not in config["approvals"]
    assert config["memory"] == {"memory_enabled": False, "user_profile_enabled": False}
    assert config["tools"]["tool_search"]["enabled"] == "off"
    assert config["plugins"]["enabled"] == [role]
    assert config["platform_toolsets"]["api_server"] == ["hermes-api-server", f"ide-{role}"]
    assert config["known_plugin_toolsets"]["cron"] == [f"ide-{role}"]
    assert "agent" not in config or "max_turns" not in config["agent"]
    assert "timeouts" not in config
    assert "kanban" not in config


def test_orchestrator_config_compaction_and_catch_up():
    config = yaml.safe_load((PROFILES["orchestrator"] / "config.yaml").read_text())
    assert config["compression"] == {
        "protect_first_n": 6,
        "protect_last_n": 30,
        "threshold": 0.6,
        "target_ratio": 0.3,
    }
    assert config["cron"]["catch_up_missed"] is True


@pytest.mark.parametrize("role", sorted(PROFILES))
def test_plugin_manifest(role):
    manifest = yaml.safe_load((PROFILES[role] / "plugins" / role / "plugin.yaml").read_text())
    assert manifest["name"] == role
    assert manifest["kind"] == "standalone"
    assert "transform_tool_result" in manifest["provides_hooks"]
    assert ("pre_llm_call" in manifest["provides_hooks"]) == (role == "orchestrator")


@pytest.mark.parametrize("role", sorted(PROFILES))
def test_plugin_shim_is_stdlib_only(role):
    shim = PROFILES[role] / "plugins" / role / "__init__.py"
    tree = ast.parse(shim.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in STDLIB, alias.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] in STDLIB, node.module
    assert f'ROLE = "{role}"' in shim.read_text()


@pytest.mark.parametrize("role", sorted(PROFILES))
def test_prepare_script_is_stdlib_only_and_takes_the_hosting_flags(role):
    text = (PROFILES[role] / "scripts" / "prepare.py").read_text()
    for flag in ("--source", "--hermes", "--activate-supervisor"):
        assert flag in text
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] in STDLIB


def test_watcher_cron_job_shape():
    jobs = json.loads((PROFILES["orchestrator"] / "cron" / "jobs.json").read_text())["jobs"]
    (job,) = jobs
    assert job["id"] == "watcher" and job["no_agent"] is True and job["script"] == "watcher.py"
    assert job["schedule"] == {"kind": "interval", "minutes": 1, "display": "every 1m"}
    assert job["enabled"] is False and job["state"] == "paused"
    assert job["enabled_toolsets"] == []
    assert (PROFILES["orchestrator"] / "scripts" / "watcher.py").is_file()
    assert not (PROFILES["researcher"] / "cron").exists()


@pytest.mark.parametrize("role", sorted(PROFILES))
def test_no_bundled_skills_marker(role):
    assert (PROFILES[role] / ".no-bundled-skills").is_file()
    assert (PROFILES[role] / "SOUL.md").is_file()
