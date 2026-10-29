"""The plugin reads its environment through the routed profile's secret scope."""

import sys
import types

from research_core.plugin import executor


def test_profile_env_merges_the_secret_scope_over_the_process_env(monkeypatch):
    monkeypatch.setenv("SOFTMAX_PLAYER_ID", "ply_host")
    monkeypatch.setenv("ONLY_IN_PROCESS", "1")
    fake = types.ModuleType("agent.secret_scope")
    fake.current_secret_scope = lambda: {"SOFTMAX_PLAYER_ID": "ply_profile", "FROM_DOTENV": "x"}
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.secret_scope", fake)

    env = executor.profile_env(IDE_SESSION_ID="s1")
    assert env["SOFTMAX_PLAYER_ID"] == "ply_profile"  # the profile's .env wins
    assert env["FROM_DOTENV"] == "x" and env["ONLY_IN_PROCESS"] == "1"
    assert env["IDE_SESSION_ID"] == "s1"
    assert executor.tool_env("orchestrator", "s1")["SOFTMAX_PLAYER_ID"] == "ply_profile"


def test_profile_env_without_hermes_or_without_a_scope(monkeypatch):
    monkeypatch.setenv("SOFTMAX_PLAYER_ID", "ply_host")
    monkeypatch.delitem(sys.modules, "agent.secret_scope", raising=False)
    monkeypatch.setitem(sys.modules, "agent", None)  # import fails cleanly
    assert executor.profile_env()["SOFTMAX_PLAYER_ID"] == "ply_host"
    fake = types.ModuleType("agent.secret_scope")
    fake.current_secret_scope = lambda: None
    monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.secret_scope", fake)
    assert executor.profile_env()["SOFTMAX_PLAYER_ID"] == "ply_host"


def test_workspace_process_env_drops_hermes_interpreter_variables(tmp_path):
    from research_core import layout

    ws = layout.from_env({"IDE_WORKSPACE": str(tmp_path / "ws")})
    env = {
        "PYTHONPATH": "/opt/hermes/.venv/lib/python3.14/site-packages",
        "VIRTUAL_ENV": "/opt/hermes/.venv",
        "PATH": "/opt/hermes/.venv/bin:/usr/bin",
        "IDE_WORKSPACE": str(tmp_path / "ws"),
        "OPENROUTER_API_KEY": "k",
    }
    clean = executor.workspace_process_env(env, ws)
    assert "PYTHONPATH" not in clean and "VIRTUAL_ENV" not in clean
    assert clean["PATH"].split(":")[0] == str(ws.venv / "bin")
    assert clean["PATH"].endswith("/opt/hermes/.venv/bin:/usr/bin")
    assert clean["OPENROUTER_API_KEY"] == "k" and clean["IDE_WORKSPACE"] == env["IDE_WORKSPACE"]
