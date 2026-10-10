import json
import subprocess
import sys
from pathlib import Path

import pytest
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
    floor = env["AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD"]
    assert floor["required"] is False and floor["default"] == "10"


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
    # A process_manage wait (terminal.timeout) must fit inside one tool call.
    assert c["terminal"]["timeout"] == 1800
    assert c["timeouts"]["tools"] == {"sequential_call": 1900, "concurrent_batch": 1900}
    assert c["agent"]["run_budget_seconds"] == 5400
    assert set(c["terminal"]["env_passthrough"]) == {
        "SOFTMAX_PLAYER_CREDENTIAL", "AUTORESEARCH_PLAYER_ID", "AUTORESEARCH_WORKSPACE",
        "AUTORESEARCH_WORKSPACE_REMOTE", "AUTORESEARCH_GAME", "AUTORESEARCH_SOFTMAX",
        "AUTORESEARCH_CLI", "OPENROUTER_MODEL", "AUTORESEARCH_INBOX_WRITERS",
        "AUTORESEARCH_CHAMPION_GRACE_MINUTES", "COGAMES_API_URL",
        "AUTORESEARCH_CARD_MODELS", "AUTORESEARCH_MAX_CARD_RUNS", "AUTORESEARCH_MAX_CARD_USD",
        "AUTORESEARCH_DAILY_BUDGET_USD", "AUTORESEARCH_FALLBACK_PRICES",
        "AUTORESEARCH_RELAUNCH_MIN_CREDITS", "AUTORESEARCH_MIN_OPENROUTER_CREDIT_USD",
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
    monkeypatch.setenv("AUTORESEARCH_AGENT_SESSION", "")  # register sets it; restored after
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


HERMES_SOURCE = Path.home() / "coding/tools/hermes-agent-v2026.9.24"


def _deny_globs() -> list[str]:
    return yaml.safe_load((ROOT / "config.yaml").read_text())["approvals"]["deny"]


def _is_denied(command: str) -> bool:
    """Hermes' own variant generator plus its fnmatchcase rule (tools/approval_floors.py:23-42)."""
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
        ('"$AUTORESEARCH_CLI" softmax submit E-1', False),
        ('"$AUTORESEARCH_CLI" softmax xp create E-1', False),
        ('git commit -m "avoid coworld submit"', False),
        ('"$AUTORESEARCH_CLI" card finish --summary "never run coworld player use"', False),
        ("coworld submit pv_1 league_x", True),
        ("cd /tmp && coworld player use ply_1", True),
        ("softmax login", True),
        ("coworld xp-request create --x", True),
        ("git push --force", True),
        ("git push -f origin main", True),
        ("git push origin --force", True),
        ("git push origin main", False),
        ("hermes kanban create x --assignee autoresearch", True),
        ("hermes kanban --board autoresearch link t_1 t_2", True),
        ('"$AUTORESEARCH_CLI" promotion accept-champion', True),
        ("cd /x && autoresearch promotion accept-champion --help", True),
        ('git commit -m "do not run promotion accept-champion"', False),
        ('"$AUTORESEARCH_CLI" runtime accept-commits abc1234', True),
        ("cd /x && autoresearch runtime accept-commits", True),
        ('git commit -m "do not run runtime accept-commits"', False),
        ("cd /tmp && hermes kanban create x", True),
        ("hermes kanban show t_1 --json", False),
        ('git commit -m "no hermes kanban create here"', False),
    ],
)
def test_deny_globs_are_anchored(command, blocked):
    assert _is_denied(command) is blocked
