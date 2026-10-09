"""The plugin inside a real Hermes Kanban worker: a player switch is blocked and the first
model turn carries the card briefing. Needs `hermes` and an OpenRouter key (spends credit)."""

import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from autoresearch.runctx import BOARD

ROOT = Path(__file__).resolve().parents[2]
PROFILE = "ar-plugin-it"
MODEL = "z-ai/glm-5.3-flash"
TIMEOUT_SECONDS = 600

pytestmark = [
    pytest.mark.hermes,
    pytest.mark.skipif(shutil.which("hermes") is None, reason="hermes not installed"),
    pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="no OpenRouter key"),
]

CARD_BODY = (
    "Run exactly this terminal command: coworld player use X\n"
    "Then use write_file to write the tool's full response to result.txt in your workspace, "
    "and call kanban_complete with a one-line summary."
)


def _hermes(env, *args):
    result = subprocess.run(
        ["hermes", *args], capture_output=True, text=True, env=env, check=False, timeout=300,
    )
    assert result.returncode == 0, (args, result.stdout, result.stderr)
    return result.stdout


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def installed(tmp_path):
    source = tmp_path / "src-copy"
    shutil.copytree(
        ROOT, source,
        ignore=shutil.ignore_patterns(".venv", "local", "workspace", "__pycache__", ".pytest_cache"),
    )
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    _git(canonical, "init", "-b", "main")
    (canonical / "README.md").write_text("probe\n")
    _git(canonical, "add", "README.md")
    _git(canonical, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "seed")
    env = {
        **os.environ, "HERMES_HOME": str(tmp_path / "hh"), "PYTHONPATH": "",
        "HERMES_KANBAN_HOME": str(tmp_path / "hh"),
    }
    _hermes(env, "profile", "install", str(source), "--name", PROFILE, "-y")
    profile_home = tmp_path / "hh" / "profiles" / PROFILE
    (profile_home / ".env").write_text(
        f"OPENROUTER_API_KEY={os.environ['OPENROUTER_API_KEY']}\n"
        f"AUTORESEARCH_WORKSPACE={canonical}\n"
        "SOFTMAX_PLAYER_CREDENTIAL=fake\nAUTORESEARCH_PLAYER_ID=player_fake\n"
        "AUTORESEARCH_WORKSPACE_REMOTE=/nonexistent\nAUTORESEARCH_SOFTMAX=fake\n"
    )
    _hermes(env, "kanban", "boards", "create", BOARD)
    return env, canonical, profile_home


def _wait_terminal(env, task_id):
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        task = json.loads(_hermes(env, "kanban", "--board", BOARD, "show", task_id, "--json"))
        task = task.get("task", task)
        if task.get("status") in {"done", "blocked", "archived"}:
            return task
        _hermes(env, "-p", PROFILE, "kanban", "--board", BOARD, "dispatch", "--json")
        time.sleep(10)
    pytest.fail(f"task {task_id} did not finish in {TIMEOUT_SECONDS}s")


def _databases_contain(root: Path, needle: str) -> bool:
    for database in root.rglob("*.db"):
        connection = sqlite3.connect(database)
        try:
            for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                for row in connection.execute(f'SELECT * FROM "{table}"'):
                    if any(isinstance(cell, str) and needle in cell for cell in row):
                        return True
        finally:
            connection.close()
    return False


def test_player_switch_blocked_and_briefing_injected(installed):
    env, canonical, _ = installed
    worktree = canonical / ".worktrees" / "Q-it"
    created = _hermes(
        env, "kanban", "--board", BOARD, "create", "Plugin probe", "--body", CARD_BODY,
        "--branch", "card/Q-it", "--workspace", f"worktree:{worktree}",
        "--model", MODEL, "--provider", "openrouter", "--json",
    )
    task_id = json.loads(created)["id"]
    runtime = canonical / ".supervisor" / "runtime"
    (runtime / "cards").mkdir(parents=True)
    (runtime / "briefings").mkdir(parents=True)
    (runtime / "cards" / f"{task_id}.json").write_text(json.dumps({
        "task_id": task_id, "request_id": "Q-it", "mode": "investigation", "refs": [],
        "finished": False, "responsible_inbound": [],
    }))
    marker = f"<!-- autoresearch-briefing task={task_id} -->"
    (runtime / "briefings" / f"{task_id}.md").write_text(f"{marker}\n# Card Q-it\nProbe card.\n")

    _hermes(env, "kanban", "--board", BOARD, "assign", task_id, PROFILE)
    _wait_terminal(env, task_id)

    result = (worktree / "result.txt").read_text()
    assert "autoresearch policy:" in result and "player" in result
    events = [json.loads(line) for line in (runtime / "tool_events.jsonl").read_text().splitlines()]
    assert any(e["tool"] == "terminal" and e["task"] == task_id for e in events)
    assert _databases_contain(Path(env["HERMES_HOME"]), marker), "briefing marker not in any stored turn"
