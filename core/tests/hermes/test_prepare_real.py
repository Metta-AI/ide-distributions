"""prepare.py end to end against a real Hermes: install, prepare, verify through ``hermes``.

Builds the workspace environment with ``uv sync`` in a temporary workspace, so the first run
takes a few minutes. Run locally with
``HERMES_BIN=<hermes> uv run pytest -m hermes core/tests/hermes/test_prepare_real.py``.
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CORE_SRC = REPO / "core" / "src"


@dataclass(frozen=True)
class Host:
    home: Path  # HERMES_REAL_HOME: the host home that holds profiles/
    workspace: Path  # IDE_WORKSPACE, shared by both profiles

    def profile(self, role: str) -> Path:
        return self.home / "profiles" / role

    def env(self, role: str) -> dict[str, str]:
        return {
            **os.environ,
            "HERMES_HOME": str(self.profile(role)),
            "HERMES_REAL_HOME": str(self.home),
            "IDE_WORKSPACE": str(self.workspace),
        }


def run(argv, env, timeout=120):
    return subprocess.run(
        [str(part) for part in argv], env=env, capture_output=True, text=True, timeout=timeout
    )


def hermes_python(hermes_bin: Path) -> str:
    """The interpreter Hermes runs under: the sibling ``python`` of the executable."""
    sibling = hermes_bin.parent / "python"
    return str(sibling) if sibling.exists() else sys.executable


def config_get(hermes_bin, host: Host, role, key):
    result = run([hermes_bin, "config", "get", key, "--json"], host.env(role))
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def prepare(hermes_bin, host: Host, role, *flags):
    script = host.profile(role) / "scripts" / "prepare.py"
    argv = [hermes_python(hermes_bin), script, "--source", REPO / role, "--hermes", hermes_bin]
    result = run([*argv, *flags], host.env(role), timeout=600)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def watcher_job(host: Host):
    jobs = json.loads((host.profile("orchestrator") / "cron" / "jobs.json").read_text())["jobs"]
    return next(job for job in jobs if job["id"] == "watcher")


@pytest.fixture(scope="module")
def host(tmp_path_factory, hermes_bin) -> Host:
    root = tmp_path_factory.mktemp("hermes")
    host = Host(home=root / "host", workspace=root / "workspace")
    host.home.mkdir()
    install_env = {**os.environ, "HERMES_HOME": str(host.home)}
    for role in ("orchestrator", "researcher"):
        argv = [hermes_bin, "profile", "install", REPO / role, "--name", role, "--yes"]
        result = run(argv, install_env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (host.profile(role) / "config.yaml").is_file()
    return host


def test_prepare_orchestrator_end_to_end(hermes_bin, host):
    summary = prepare(hermes_bin, host, "orchestrator", "--activate-supervisor")
    assert summary["role"] == "orchestrator"
    assert summary["workspace"] == str(host.workspace)
    profile = host.profile("orchestrator")

    assert (profile / "local" / "core-path.txt").read_text().strip() == str(CORE_SRC)
    assert (profile / ".softmax-prepared").is_file()

    assert config_get(hermes_bin, host, "orchestrator", "approvals.mode") == "off"
    assert config_get(hermes_bin, host, "orchestrator", "approvals.deny") == []
    assert config_get(hermes_bin, host, "orchestrator", "plugins.enabled") == ["orchestrator"]
    assert config_get(hermes_bin, host, "orchestrator", "known_plugin_toolsets.cron") == [
        "ide-orchestrator"
    ]
    assert config_get(hermes_bin, host, "orchestrator", "compression.protect_first_n") == 6
    assert config_get(hermes_bin, host, "orchestrator", "skills.external_dirs") == [
        str(CORE_SRC / "research_core" / "skills" / "common"),
        str(CORE_SRC / "research_core" / "skills" / "orchestrator"),
    ]

    venv_python = host.workspace / "tools" / "venv" / "bin" / "python"
    assert venv_python.is_file(), summary
    probe = run([venv_python, "-c", "import research_core, coworld; print('ok')"], os.environ)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "ok"

    job = watcher_job(host)
    assert job["enabled"] is True and job["state"] == "scheduled"
    assert job["script"] == "watcher.py" and job["no_agent"] is True


def test_prepare_again_keeps_the_environment_and_the_pause_state(hermes_bin, host):
    prepare(hermes_bin, host, "orchestrator", "--activate-supervisor")
    stamp = host.workspace / "tools" / "venv" / ".lock-sha"
    stamp_before = stamp.read_text()
    paused = run([hermes_bin, "cron", "pause", "watcher"], host.env("orchestrator"))
    assert paused.returncode == 0, paused.stdout + paused.stderr

    summary = prepare(hermes_bin, host, "orchestrator", "--activate-supervisor")

    assert stamp.read_text() == stamp_before
    assert not any("synced" in action for action in summary["actions"]), summary
    job = watcher_job(host)
    assert job["enabled"] is False and job["state"] == "paused", "activation happens once"


def test_prepare_researcher_shares_the_environment(hermes_bin, host):
    summary = prepare(hermes_bin, host, "researcher")
    assert summary["role"] == "researcher"
    assert summary["venv"] == str(host.workspace / "tools" / "venv")
    assert config_get(hermes_bin, host, "researcher", "plugins.enabled") == ["researcher"]
    assert config_get(hermes_bin, host, "researcher", "platform_toolsets.api_server") == [
        "hermes-api-server",
        "ide-researcher",
    ]
    assert config_get(hermes_bin, host, "researcher", "compression.protect_first_n") != 6
    assert not (host.profile("researcher") / "cron" / "jobs.json").exists()
    assert (host.profile("researcher") / "local" / "core-path.txt").is_file()


def test_the_kimi_pin_yields_exactly_where_hermes_applies_an_owner_entry(hermes_bin):
    """unpinned_routing defers to Hermes's own per-model matching, run on Hermes's Python."""
    code = (
        "import json, sys; sys.path.insert(0, sys.argv[1]);"
        "from research_core.prepare import unpinned_routing;"
        "keys = json.loads(sys.argv[2]);"
        "print(json.dumps({k: sorted(unpinned_routing({'models': {k: e}})) "
        "for k in keys for e in [{} if k.endswith(' (empty)') else {'only': ['x']}] "
        "for k in [k.removesuffix(' (empty)')]}))"
    )
    keys = [
        "kimi-k3.1-pro",
        "moonshotai/kimi-k3-1-pro",
        "custom/kimi-k3.1-pro",
        "MOONSHOTAI/KIMI-K3.1-PRO",
        "kimi-k3 (empty)",  # an empty entry is still the owner's: no pin for kimi-k3
    ]
    result = run([hermes_python(hermes_bin), "-c", code, CORE_SRC, json.dumps(keys)], os.environ)
    assert result.returncode == 0, result.stderr
    k3, k31 = "moonshotai/kimi-k3", "moonshotai/kimi-k3.1-pro"
    # The pins still added: an owner entry Hermes would not apply to the model does not count.
    assert json.loads(result.stdout) == {
        "kimi-k3.1-pro": [k3],
        "moonshotai/kimi-k3-1-pro": [k3],
        "custom/kimi-k3.1-pro": [k3, k31],
        "MOONSHOTAI/KIMI-K3.1-PRO": [k3, k31],
        "kimi-k3": [k31],
    }


@pytest.mark.parametrize("existing_history", [False, True])
def test_installed_agent_text_chat_and_resume(hermes_bin, host, existing_history):
    """Real pinned agent and persistence, with only the external model replaced by HTTP."""
    prepare(hermes_bin, host, "orchestrator")
    script = """
import json, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from hermes_cli.plugins import has_middleware
from hermes_state_registry import acquire, release
from run_agent import AIAgent
assert not has_middleware("llm_request"), "Text chat must not need content middleware"
captured = []
class Provider(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        captured.append(request)
        message = {"role": "assistant", "content": "Chat received"}
        choice = {"index": 0, "finish_reason": "stop", "message": message}
        body = json.dumps({"id": "proof", "object": "chat.completion", "created": 1,
            "model": "proof", "choices": [choice],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}).encode()
        if request.get("stream"):
            chunk = {"id": "proof", "object": "chat.completion.chunk", "created": 1,
                "model": "proof", "choices": [
                    {"index": 0, "finish_reason": "stop", "delta": message}]}
            body = ("data: " + json.dumps(chunk) + "\\n\\ndata: [DONE]\\n\\n").encode()
        self.send_response(200)
        mime = "text/event-stream" if request.get("stream") else "application/json"
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass
server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
threading.Thread(target=server.serve_forever, daemon=True).start()
db = acquire()
sid = "openai-text-proof-" + sys.argv[1]
prior = []
if sys.argv[1] == "1":
    db.create_session(sid, "api_server", model="proof")
    db.append_message(sid, "user", "A message from before the protocol upgrade")
    db.append_message(sid, "assistant", "A saved answer from before the upgrade")
    prior = [(m["id"], m["role"], m["content"]) for m in db.get_messages(sid)]
try:
    for text in ("Hello from the OpenAI message contract", "Continue this same chat"):
        initial = db.get_messages_as_conversation(sid, include_row_ids=True)
        agent = AIAgent(model="proof", provider="custom", api_mode="chat_completions",
            base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="fixture",
            enabled_toolsets=[], quiet_mode=True, skip_context_files=True,
            skip_memory=True, skip_background_review=True, session_id=sid,
            session_db=db, max_iterations=2)
        result = agent.run_conversation(text, conversation_history=initial)
        assert result["final_response"] == "Chat received", result
        assert any(m["role"] == "user" and isinstance(m["content"], str)
            and m["content"].startswith(text) for m in captured[-1]["messages"]), captured[-1]
    saved = db.get_messages(sid)
    assert [(m["id"], m["role"], m["content"]) for m in saved[:len(prior)]] == prior
    assert [(m["role"], m["content"]) for m in saved[len(prior):]] == [
        ("user", "Hello from the OpenAI message contract"), ("assistant", "Chat received"),
        ("user", "Continue this same chat"), ("assistant", "Chat received"),
    ], saved
    print("PASS pinned native text chat and resume without content middleware or history rewrites")
finally:
    server.shutdown()
    server.server_close()
    release(db)
"""
    result = run(
        [hermes_python(hermes_bin), "-c", script, str(int(existing_history))],
        host.env("orchestrator"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS pinned native text chat and resume" in result.stdout
