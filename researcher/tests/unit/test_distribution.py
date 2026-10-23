"""The shipped distribution: manifest, config, cron, the guard plugin and the deny list."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "plugins/researcher_guard/__init__.py"


def test_manifest():
    manifest = yaml.safe_load((ROOT / "distribution.yaml").read_text())
    assert manifest["name"] == "researcher" and manifest["hermes_requires"] == ">=0.21.6"
    env = {entry["name"]: entry for entry in manifest["env_requires"]}
    for name in ("OPENROUTER_API_KEY", "SOFTMAX_PLAYER_CREDENTIAL", "RESEARCH_PLAYER_ID",
                 "RESEARCH_CANONICAL"):  # fmt: skip
        assert env[name]["required"] is True, name
    assert env["RESEARCH_GAME"]["default"] == "gota"
    assert not [name for name in env if name.startswith("AUTORESEARCH_")]
    assert "distribution_owned" not in manifest  # the whole folder is owned


def test_config():
    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    assert config["plugins"]["enabled"] == ["researcher_guard"]
    assert config["memory"]["memory_enabled"] is False
    assert "platform_toolsets" not in config and "kanban" not in config
    passthrough = set(config["terminal"]["env_passthrough"])
    assert {"RESEARCH_CLI", "RESEARCH_WORKSPACE", "RESEARCH_CANONICAL", "RESEARCH_PLAYER_ID",
            "RESEARCH_GAME", "SOFTMAX_PLAYER_CREDENTIAL"} <= passthrough  # fmt: skip
    assert not [name for name in passthrough if name.startswith("AUTORESEARCH_")]
    # Installed games' skills: <hermes root>/games, relative to this profile's HERMES_HOME.
    assert config["skills"]["external_dirs"] == ["../../games"]


def test_no_cron_jobs():
    assert json.loads((ROOT / "cron/jobs.json").read_text())["jobs"] == []


def test_bootstrap_script_and_doctrine_ship():
    assert (ROOT / "scripts/bootstrap.py").is_file()
    assert (ROOT / "doctrine/VERSION").is_file()


def test_guard_imports_only_the_standard_library():
    code = f"import runpy; runpy.run_path({str(GUARD)!r})"
    run = subprocess.run([sys.executable, "-S", "-I", "-c", code],
                         capture_output=True, text=True, check=False)  # fmt: skip
    assert run.returncode == 0, run.stderr


def _guard():
    spec = importlib.util.spec_from_file_location("researcher_guard", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guard_manifest_names_its_hook():
    manifest = yaml.safe_load((GUARD.parent / "plugin.yaml").read_text())
    assert manifest["name"] == "researcher_guard" and manifest["provides_hooks"] == [
        "pre_tool_call"
    ]
    assert "provides_tools" not in manifest


@pytest.mark.parametrize("tool", ["kanban_create", "kanban_link", "kanban_schedule"])
def test_guard_blocks_card_creation(tool):
    verdict = _guard().pre_tool_call(tool_name=tool, args={})
    assert verdict["action"] == "block" and "orchestrator" in verdict["message"]


@pytest.mark.parametrize("tool", ["kanban_show", "kanban_comment", "kanban_block", "read_file"])
def test_guard_allows_the_card_lifecycle(tool):
    assert _guard().pre_tool_call(tool_name=tool, args={}) is None


@pytest.mark.parametrize(
    ("command", "blocked"),
    [
        ("git push origin main", True),
        ("cd /w && git push origin HEAD:main", True),
        ("GIT_TRACE=1 /usr/bin/git push origin +card/x:refs/heads/main", True),
        ("git push --all origin", True),
        ('git -C "$RESEARCH_WORKSPACE" push', True),
        ("git push origin HEAD", True),
        ("git -C /w push origin card/t_1", True),
        ("git -C /w status", False),
        ('"$RESEARCH_CLI" workspace integrate', False),
        ("echo git push origin main is blocked", False),
        ('git commit -m "never git push origin main"', False),
    ],
)
def test_guard_blocks_every_terminal_push(command, blocked):
    verdict = _guard().pre_tool_call(tool_name="terminal", args={"command": command})
    assert (verdict is not None and verdict["action"] == "block") is blocked


HERMES_SOURCE = Path.home() / "coding/tools/hermes-agent-v0.21.6"


def _deny_globs() -> list[str]:
    return yaml.safe_load((ROOT / "config.yaml").read_text())["approvals"]["deny"]


def _is_denied(command: str) -> bool:
    """Hermes' own variant generator plus its fnmatchcase rule (tools/approval_floors.py)."""
    import fnmatch

    sys.path.insert(0, str(HERMES_SOURCE))
    try:
        from tools.approval_detection import _deny_command_variants
    finally:
        sys.path.remove(str(HERMES_SOURCE))
    globs = [g.lower() for g in _deny_globs()]
    return any(
        fnmatch.fnmatchcase(variant.lower().strip(), glob)
        for variant in _deny_command_variants(command)
        for glob in globs
    )


@pytest.mark.skipif(not HERMES_SOURCE.is_dir(), reason="Hermes source checkout not available")
@pytest.mark.parametrize(
    ("command", "blocked"),
    [
        ('"$RESEARCH_CLI" softmax xp create E-1', False),
        ('"$RESEARCH_CLI" softmax upload E-1 --build-dir d', False),
        ('"$RESEARCH_CLI" workspace integrate', True),
        ('"$RESEARCH_CLI" game install --name x --worktree w --commit c', True),
        ('"$RESEARCH_CLI" game new --name x --league l', False),
        ('"$RESEARCH_CLI" source fetch --repo https://github.com/a/b', False),
        ('git commit -m "avoid coworld submit"', False),
        ("coworld submit pv_1 league_x", True),
        ("cd /tmp && coworld player use ply_1", True),
        ("softmax login", True),
        ("coworld xp-request create --x", True),
        ("git push --force", True),
        ("git push -f origin main", True),
        ("git push origin --force", True),
        ("hermes kanban create x --assignee researcher-1", True),
        ("hermes kanban --board autoresearch link t_1 t_2", True),
        ('"$RESEARCH_CLI" promotion submit E-1', True),
        ('"$RESEARCH_CLI" promotion submit-requested --version V-1', True),
        ('cd /tmp && research promotion submit-requested --version V-1', True),
        ('"$RESEARCH_CLI" promotion bootstrap --version V-1', True),
        ("cd /x && research promotion accept", True),
        ('git commit -m "the orchestrator runs promotion submit"', False),
        ("cd /tmp && hermes kanban create x", True),
        ("hermes kanban show t_1 --json", False),
        ('git commit -m "no hermes kanban create here"', False),
    ],
)
def test_deny_globs_are_anchored(command, blocked):
    assert _is_denied(command) is blocked
