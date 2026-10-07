import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_manifest():
    m = yaml.safe_load((ROOT / "distribution.yaml").read_text())
    assert m["name"] == "autoresearch" and m["hermes_requires"] == ">=0.21.5"
    assert "distribution_owned" not in m
    env = {e["name"]: e for e in m["env_requires"]}
    for k in [
        "OPENROUTER_API_KEY",
        "SOFTMAX_PLAYER_CREDENTIAL",
        "AUTORESEARCH_PLAYER_ID",
        "AUTORESEARCH_WORKSPACE_REMOTE",
    ]:
        assert env[k]["required"] is True
    assert env["OPENROUTER_MODEL"]["default"] == "moonshotai/kimi-k3"
    assert env["AUTORESEARCH_GAME"]["default"] == "gota"


def test_config():
    c = yaml.safe_load((ROOT / "config.yaml").read_text())
    assert "autoresearch" in c["plugins"]["enabled"]
    assert c["model"]["default"] == "moonshotai/kimi-k3"
    assert "z-ai/glm-5.3" in json.dumps(c["fallback_providers"])
    k = c["kanban"]
    assert (k["max_in_progress"], k["auto_decompose"], k["review_dispatch"],
            k["auto_subscribe_on_create"]) == (2, False, False, False)
    assert "default_assignee" not in k
    assert c["memory"] == {
        "memory_enabled": True, "user_profile_enabled": False, "write_approval": False,
    }
    assert set(c["terminal"]["env_passthrough"]) == {
        "SOFTMAX_PLAYER_CREDENTIAL", "AUTORESEARCH_PLAYER_ID", "AUTORESEARCH_WORKSPACE",
        "AUTORESEARCH_WORKSPACE_REMOTE", "AUTORESEARCH_GAME", "AUTORESEARCH_SOFTMAX",
        "AUTORESEARCH_CLI",
    }


def test_cron_jobs():
    jobs = json.loads((ROOT / "cron/jobs.json").read_text())["jobs"]
    by = {j["id"]: j for j in jobs}
    assert set(by) == {"queue-keeper", "xp-poll", "health"}
    for j in jobs:
        assert j["no_agent"] is True and j["enabled"] is False and j["state"] == "paused"
        assert isinstance(j["schedule"], dict)
        assert (ROOT / "scripts" / j["script"]).is_file()


def test_plugin_import_graph_is_stdlib_only():
    entry = ROOT / "plugins/autoresearch/__init__.py"
    code = f"import runpy,sys; sys.argv=['x']; runpy.run_path({str(entry)!r})"
    r = subprocess.run(
        [sys.executable, "-S", "-I", "-c", code],
        capture_output=True, text=True, check=False, env={"HERMES_HOME": str(ROOT)},
    )
    assert r.returncode == 0, r.stderr


def test_plugin_stub_registers_five_hooks_and_logs_tool_events(tmp_path, monkeypatch):
    from autoresearch.plugin import adapters

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("AUTORESEARCH_WORKSPACE", raising=False)
    hooks = {}

    class FakeCtx:
        def register_hook(self, name, callback):
            hooks[name] = callback

    adapters.register(FakeCtx())
    assert set(hooks) == {
        "pre_tool_call", "post_tool_call", "on_session_end", "pre_llm_call",
        "kanban_task_completed",
    }
    hooks["post_tool_call"](tool_name="terminal", status="ok", surprise_kwarg=1)
    log = tmp_path / "workspace/.supervisor/runtime/tool_events.jsonl"
    assert json.loads(log.read_text())["tool"] == "terminal"
