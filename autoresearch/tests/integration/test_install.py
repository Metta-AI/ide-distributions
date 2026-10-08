"""Fresh install of the distribution on real Hermes (Plan 01 Task 1.3; Overview C1, C9).

Proves, from a copy outside the source tree: the payload arrives; the cron launcher
bootstraps (venv sync, CLI shim, canonical workspace, pushed ``main``); a worker shell finds
``$AUTORESEARCH_CLI``; the plugin loads in a worker; ``hermes profile update`` keeps user
state. The two worker tests call a model and skip without ``OPENROUTER_API_KEY``.
"""

import json
import os
import subprocess
import time

import pytest
import yaml

pytestmark = pytest.mark.hermes

WORKER_MODEL = "z-ai/glm-5.3-flash"
WORKER_TIMEOUT_S = 600
TERMINAL_STATUSES = {"done", "blocked", "archived"}


def bootstrap(install) -> subprocess.CompletedProcess:
    result = install.run_launcher("queue_keeper.py", "--bootstrap-only")
    assert result.returncode == 0, (
        f"launcher exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


def git(repo, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_payload_present(install):
    profile = install.profile_dir
    for relative in (
        "scripts/queue_keeper.py",
        "src/autoresearch/launch.py",
        "uv.lock",
        "plugins/autoresearch/plugin.yaml",
    ):
        assert (profile / relative).is_file(), relative
    # User-owned paths are not part of the payload (A6: bin/ is ours and Hermes's, later).
    assert not (profile / "local").exists()
    assert not (profile / "bin" / "autoresearch").exists()
    jobs = json.loads((profile / "cron" / "jobs.json").read_text())["jobs"]
    assert {job["id"]: job["state"] for job in jobs} == {
        "queue-keeper": "paused",
        "xp-poll": "paused",
        "health": "paused",
    }


def test_launcher_bootstraps(install):
    result = bootstrap(install)
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary, result.stdout

    profile = install.profile_dir
    venv_python = profile / "local" / "venv" / "bin" / "python"
    assert venv_python.exists()
    assert (profile / "local" / "venv" / ".lock-sha").is_file()

    shim = profile / "bin" / "autoresearch"
    assert shim.is_file() and os.access(shim, os.X_OK)
    version = subprocess.run(
        [str(shim), "--version"], env=install.env(), capture_output=True, text=True, check=True
    )
    assert version.stdout.strip() == "autoresearch 0.1.0"
    env_lines = (profile / ".env").read_text().splitlines()
    assert f"AUTORESEARCH_CLI={shim}" in env_lines

    canonical = profile / "workspace"
    assert (canonical / ".git").is_dir()
    assert (canonical / ".supervisor" / "ledger").is_dir()
    assert (canonical / "AGENTS.md").is_file()
    assert git(install.remote, "rev-parse", "refs/heads/main") == git(
        canonical, "rev-parse", "HEAD"
    )

    config = yaml.safe_load((profile / "config.yaml").read_text())
    assert config["plugins"]["enabled"] == ["autoresearch"]
    boards = install.hermes("kanban", "boards", "list").stdout
    assert install.profile in boards
    # Boards are host-wide (A2): under the throwaway root, not the profile or ~/.hermes.
    assert (install.hermes_home / "kanban" / "boards" / install.profile / "kanban.db").is_file()

    # A second run is a no-op for the venv (lock hash unchanged).
    stamp_mtime = (profile / "local" / "venv" / ".lock-sha").stat().st_mtime
    bootstrap(install)
    assert (profile / "local" / "venv" / ".lock-sha").stat().st_mtime == stamp_mtime


# -- worker tests: one real worker run shared by both ------------------------------------

CARD_BODY = (
    'In the terminal, run exactly this command: "$AUTORESEARCH_CLI" --version\n'
    "Then call kanban_complete with a summary that contains the command's output verbatim. "
    "Do nothing else."
)


def _wait_for_terminal(install, task_id: str) -> dict:
    deadline = time.monotonic() + WORKER_TIMEOUT_S
    while True:
        shown = json.loads(
            install.hermes("kanban", "--board", install.profile, "show", task_id, "--json").stdout
        )
        if shown["task"]["status"] in TERMINAL_STATUSES:
            return shown
        if time.monotonic() > deadline:
            raise AssertionError(f"worker did not finish in {WORKER_TIMEOUT_S}s: {shown}")
        time.sleep(5)


@pytest.fixture(scope="module")
def worker_run(request):
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("needs OPENROUTER_API_KEY (one worker run with a real model)")
    install = request.getfixturevalue("module_install")
    board = install.profile
    bootstrap(install)
    canonical = install.profile_dir / "workspace"
    created = install.hermes(
        "kanban",
        "--board",
        board,
        "create",
        "Install probe: CLI version",
        "--body",
        CARD_BODY,
        "--assignee",
        board,
        "--workspace",
        f"worktree:{canonical}/.worktrees/it-cli",
        "--branch",
        "card/it-cli",
        "--model",
        WORKER_MODEL,
        "--provider",
        "openrouter",
        "--max-retries",
        "1",
        "--json",
    )
    task_id = json.loads(created.stdout)["id"]
    dispatched = json.loads(
        install.hermes("-p", board, "kanban", "--board", board, "dispatch", "--json").stdout
    )
    assert [s["task_id"] for s in dispatched["spawned"]] == [task_id], dispatched
    shown = _wait_for_terminal(install, task_id)
    log = install.hermes("kanban", "--board", board, "log", task_id, check=False)
    return {
        "install": install,
        "shown": shown,
        "log": log.stdout + log.stderr,
        "canonical": canonical,
    }


def test_worker_shell_finds_cli(worker_run):
    shown = worker_run["shown"]
    assert shown["task"]["status"] == "done", shown
    # The worker log is the run's transcript; the summary is the model's report of the
    # command output. Either proves the worker shell resolved $AUTORESEARCH_CLI.
    evidence = worker_run["log"] + json.dumps(shown["runs"])
    assert "autoresearch 0.1.0" in evidence, evidence


def test_plugin_loads_in_worker(worker_run):
    events = worker_run["canonical"] / ".supervisor" / "runtime" / "tool_events.jsonl"
    assert events.is_file()
    tools = [json.loads(line)["tool"] for line in events.read_text().splitlines()]
    assert "terminal" in tools, tools


# -- update ---------------------------------------------------------------------------------


def test_update_preserves(install):
    profile = install.profile_dir
    (profile / "workspace").mkdir(exist_ok=True)
    (profile / "workspace" / "x").write_text("user file\n")
    (profile / "memories").mkdir(exist_ok=True)
    (profile / "memories" / "MEMORY.md").write_text("remember this\n")
    install.hermes("-p", install.profile, "config", "set", "kanban.max_in_progress", "3")

    marker = "# task-1.3 update marker\n"
    script = install.source / "scripts" / "queue_keeper.py"
    script.write_text(script.read_text() + marker)

    install.hermes("profile", "update", install.profile, "-y")

    assert (profile / "workspace" / "x").read_text() == "user file\n"
    assert (profile / "memories" / "MEMORY.md").read_text() == "remember this\n"
    config = yaml.safe_load((profile / "config.yaml").read_text())
    assert config["kanban"]["max_in_progress"] == 3
    assert (profile / "scripts" / "queue_keeper.py").read_text().endswith(marker)
