"""The plugin inside real Hermes: in a Kanban worker a player switch is blocked and the first
model turn carries the card briefing; in the IDE coach chat (the multiplexed gateway's API
server) only the coach tools run and a proposal is sent only on the coach's confirm line.
Needs `hermes` and an OpenRouter key (spends credit)."""

import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from support.hermes_python import hermes_python

from autoresearch.plugin import coach
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


# --- the IDE coach chat through the multiplexed gateway's API server (plan Task 5 Step 4) ---

CHAT_MODEL = "z-ai/glm-5.3"
SERVICE_KEY = secrets.token_hex(16)  # the API server refuses keys under 16 characters
CHAT_SESSION = "it-coach-chat"
BRIDGE_INSTRUCTIONS = (  # what the Softmax bridge sends with every run (metta bridge.py)
    "This IDE session acts as Softmax player ply_fake. Use --server on softmax and coworld "
    "commands. Never print credentials or request them in chat."
)
CONFIRM_LINE = re.compile(r"To send exactly this, reply: confirm (P-[0-9a-f]{4,})\s*$")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _http(method, url, body=None):
    request = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {SERVICE_KEY}", "Content-Type": "application/json",
                 "Idempotency-Key": f"it-{time.time_ns()}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _chat(base, line):
    """One coach line as the bridge sends it; the run's final output."""
    created = _http("POST", f"{base}v1/runs", {
        "input": line, "instructions": BRIDGE_INSTRUCTIONS, "session_id": CHAT_SESSION,
        "model": CHAT_MODEL, "provider": "openrouter",
    })
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        result = _http("GET", f"{base}v1/runs/{created['run_id']}")
        if result["status"] == "completed":
            return result.get("output") or ""
        assert result["status"] in {"queued", "running"}, result
        time.sleep(2)
    pytest.fail(f"chat run for {line!r} did not finish")


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _wait_inbound(canonical: Path, kind: str) -> list[dict]:
    """The committed inbound messages of ``kind``, waiting for the plugin's background send."""
    deadline = time.monotonic() + 120
    while not (found := _inbound(canonical, kind)) and time.monotonic() < deadline:
        time.sleep(2)
    return found


def _inbound(canonical: Path, kind: str) -> list[dict]:
    listed = subprocess.run(
        ["git", "-C", str(canonical), "ls-tree", "--name-only", "HEAD", "comms/inbox/"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    messages = []
    for name in listed:
        shown = subprocess.run(["git", "-C", str(canonical), "show", f"HEAD:{name}"],
                               capture_output=True, text=True, check=True).stdout
        messages.append(json.loads(shown))
    return [m for m in messages if m.get("kind") == kind]


def _offered(install, platform: str) -> set[str]:
    """The toolsets Hermes resolves for ``platform`` from the installed profile's config."""
    code = (
        "import json; from hermes_cli.plugins import discover_plugins; discover_plugins(); "
        "from hermes_cli.config import load_config; "
        "from hermes_cli.tools_config import _get_platform_tools; "
        f"print(json.dumps(sorted(_get_platform_tools(load_config(), {platform!r}))))"
    )
    result = subprocess.run(
        [hermes_python(), "-c", code], env=install.env(HERMES_HOME=str(install.profile_dir)),
        capture_output=True, text=True, check=True, timeout=120,
    )
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def _transform_hook_owners(install) -> list[str]:
    code = (
        "import json; from hermes_cli.plugins import discover_plugins, get_plugin_manager; "
        "discover_plugins(); hooks = get_plugin_manager()._hooks.get('transform_llm_output', []); "
        "print(json.dumps([getattr(h, '__module__', '?') for h in hooks]))"
    )
    result = subprocess.run(
        [hermes_python(), "-c", code], env=install.env(HERMES_HOME=str(install.profile_dir)),
        capture_output=True, text=True, check=True, timeout=120,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture
def chat_gateway(optimizer_install):
    """The profile installed as ``optimizer`` and bootstrapped, behind one multiplexed
    ``-p default`` gateway with its API server on, the way the Softmax runtime runs it."""
    install = optimizer_install
    port = _free_port()
    with (install.profile_dir / ".env").open("a") as handle:
        handle.write(
            f"API_SERVER_KEY={SERVICE_KEY}\nAPI_SERVER_ENABLED=true\nAPI_SERVER_HOST=127.0.0.1\n"
            f"API_SERVER_PORT={port}\nAUTORESEARCH_SKIP_OPENROUTER_CHECK=1\n"
        )
    booted = install.run_launcher("queue_keeper.py", "--bootstrap-only")
    assert booted.returncode == 0, booted.stdout + booted.stderr
    server = {
        "API_SERVER_ENABLED": "true", "API_SERVER_HOST": "127.0.0.1",
        "API_SERVER_PORT": str(port), "API_SERVER_KEY": SERVICE_KEY,
        "OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"],
    }
    # The Softmax runtime writes these to the host root's .env and the profile's
    # (runtime.py configure_profile_credentials) and sets them on the gateway process.
    (install.hermes_home / ".env").write_text("".join(f"{k}={v}\n" for k, v in server.items()))
    env = install.env(GATEWAY_MULTIPLEX_PROFILES="true", **server)
    log = (install.root / "gateway.log").open("w")
    gateway = subprocess.Popen(["hermes", "-p", "default", "gateway", "run"], env=env,
                               stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}/p/{install.profile}/"
    try:
        deadline = time.monotonic() + 120
        while True:
            try:
                _http("GET", f"http://127.0.0.1:{port}/v1/capabilities")
                break
            except (OSError, urllib.error.URLError):
                assert gateway.poll() is None, "gateway exited (see gateway.log)"
                assert time.monotonic() < deadline, "gateway did not start"
                time.sleep(2)
        yield install, base
    finally:
        gateway.terminate()
        gateway.wait(timeout=60)
        log.close()
    spent = 0.0
    for database in install.hermes_home.rglob("state.db"):
        connection = sqlite3.connect(database)
        try:
            spent += connection.execute(
                "SELECT COALESCE(SUM(COALESCE(actual_cost_usd, estimated_cost_usd, 0)), 0) "
                "FROM sessions"
            ).fetchone()[0]
        finally:
            connection.close()
    print(f"\ncoach chat integration: estimated model spend ${spent:.4f}")


def test_coach_chat_routes_by_platform(chat_gateway):
    install, base = chat_gateway
    canonical = install.profile_dir / "workspace"
    runtime = canonical / ".supervisor" / "runtime"
    assert _offered(install, "api_server") == {"autoresearch_coach"}
    cli_tools = _offered(install, "cli")
    assert "autoresearch_coach" not in cli_tools and {"terminal", "file"} <= cli_tools
    assert len(_transform_hook_owners(install)) == 1, "another plugin defines transform_llm_output"

    # (a) status runs in the routed profile's environment
    _chat(base, "What is your status? Use your status tool.")
    chat = _events(runtime / "chat_events.jsonl")
    assert any(e["event"] == "chat_turn" and e["platform"] == "api_server" for e in chat)
    assert any(e["event"] == "tool" and e["tool"] == "status" and e["ok"] for e in chat), chat
    # Only coach tools and Hermes's tool-search bridge ran; nothing else got past the policy.
    events = _events(runtime / "tool_events.jsonl")
    ran = {e["tool"] for e in events if e["status"] != "blocked"}
    assert "status" in ran and ran <= coach.TOOL_NAMES | {"tool_search", "tool_describe", "tool_call"}

    # (b) a proposal ends the reply with the plugin's own rendering
    output = _chat(base, "Tell the agent to stop working on kill-steals for now.")
    found = CONFIRM_LINE.search(output)
    assert found, output
    proposal = found.group(1)
    assert "kill" in output.split(f"Proposal {proposal}:")[-1].lower()

    # (c) the coach's confirm line sends it, as the coach
    _chat(base, f"confirm {proposal}")
    [steer] = _wait_inbound(canonical, "steer")
    assert steer["author"] == "coach:ply_fake" and "kill" in steer["text"].lower()

    # (d) /pause-research reaches the run and becomes a control inbound
    _chat(base, "/pause-research")
    [control] = _wait_inbound(canonical, "control")
    assert control["action"] == "pause" and control["author"] == "coach:ply_fake"

    # A Kanban card's worker keeps the research toolset: it can run the terminal.
    created = install.hermes(
        "kanban", "--board", install.board, "create", "Coach chat probe card", "--body",
        "Run exactly this terminal command: echo worker-ok\nThen call kanban_complete with a "
        "one-line summary.", "--assignee", install.profile, "--model", CHAT_MODEL, "--max-retries", "1",
        "--provider", "openrouter", "--json",
    )
    task_id = json.loads(created.stdout)["id"]
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        shown = json.loads(install.hermes("kanban", "--board", install.board, "show", task_id,
                                          "--json").stdout)
        if shown.get("task", shown).get("status") in {"done", "blocked", "archived"}:
            break
        time.sleep(10)
    worker_tools = {e["tool"] for e in _events(runtime / "tool_events.jsonl") if e.get("task") == task_id}
    assert "terminal" in worker_tools, worker_tools
