"""Spike S1 (design H "Validation before and during the build"): the dispatch model on a real
Hermes gateway.

Several Researcher Sessions in one ``researcher`` profile, started, steered, stopped and resumed
through ``/p/<profile>/v1/runs`` with ``session_id``, including after the gateway is killed
under a live run. The test asserts the one thing the design hangs on: Sessions in one profile
are isolated (each session's history holds only its own messages) and one run's end does not
affect the others. If that fails, stop: the Kanban fallback (B.13) is a design change for James.

Needs ``HERMES_BIN`` (a real Hermes executable, see ``conftest.py``) and ``OPENROUTER_API_KEY``
(the turns call a model). It installs both profiles into a temporary home, prepares them, and
starts ``hermes gateway run`` with the API server enabled on a free port. The outcome, with
timings, is written into ``docs/spikes/s1-dispatch.md`` between the ``s1:begin`` and
``s1:end`` markers. Run with::

    HERMES_BIN=<hermes> OPENROUTER_API_KEY=<key> \\
        uv run pytest -m hermes core/tests/hermes/test_spike_s1.py
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research_core.watcher.hermes_api import HermesRuns, RunsError, RunStatus, mint_session_id

REPO = Path(__file__).resolve().parents[3]
REPORT = REPO / "docs" / "spikes" / "s1-dispatch.md"
BEGIN, END = "<!-- s1:begin -->", "<!-- s1:end -->"
PROMPT = "Reply with the word READY and stop."
STEER = "Also include the word STEERED in your reply."
RESUME = "Continue the same conversation: reply with READY AGAIN and stop."
READY_TIMEOUT_S = 180
RUN_TIMEOUT_S = 300


@pytest.fixture(scope="module")
def openrouter_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        pytest.skip("OPENROUTER_API_KEY is not set; the spike runs real model turns")
    return key


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run(argv, env, timeout=120):
    return subprocess.run(
        [str(part) for part in argv], env=env, capture_output=True, text=True, timeout=timeout
    )


def hermes_python(hermes_bin: Path) -> str:
    sibling = hermes_bin.parent / "python"
    return str(sibling) if sibling.exists() else sys.executable


@dataclass
class Host:
    """A temporary host home with both profiles installed and one API key for all."""

    hermes_bin: Path
    home: Path
    workspace: Path
    port: int
    key: str
    openrouter_key: str
    log: Path
    process: subprocess.Popen | None = None
    timings: dict[str, float] = field(default_factory=dict)

    def profile(self, role: str) -> Path:
        return self.home / "profiles" / role

    def base_env(self) -> dict[str, str]:
        return {
            **os.environ,
            "HERMES_HOME": str(self.home),
            "HERMES_REAL_HOME": str(self.home),
            "IDE_WORKSPACE": str(self.workspace),
            "OPENROUTER_API_KEY": self.openrouter_key,
            "API_SERVER_ENABLED": "true",
            "API_SERVER_HOST": "127.0.0.1",
            "API_SERVER_PORT": str(self.port),
            "API_SERVER_KEY": self.key,
            "GATEWAY_MULTIPLEX_PROFILES": "true",
        }

    def profile_env(self, role: str) -> dict[str, str]:
        return {**self.base_env(), "HERMES_HOME": str(self.profile(role))}

    def write_host_dotenv(self) -> None:
        """The host home's ``.env``: the gateway enables its API server from the default home's
        ``.env``, not from the process environment, and multiplexes the profiles from there."""
        lines = {
            "API_SERVER_ENABLED": "true",
            "API_SERVER_HOST": "127.0.0.1",
            "API_SERVER_PORT": str(self.port),
            "API_SERVER_KEY": self.key,
            "OPENROUTER_API_KEY": self.openrouter_key,
            "GATEWAY_MULTIPLEX_PROFILES": "true",
            "IDE_WORKSPACE": str(self.workspace),
            "IDE_PLATFORM": "fake",
        }
        (self.home / ".env").write_text("\n".join(f"{k}={v}" for k, v in lines.items()) + "\n")

    def write_dotenv(self, role: str) -> None:
        """The same API key into every profile's ``.env`` (A5.8), plus the model key."""
        lines = {
            "API_SERVER_ENABLED": "true",
            "API_SERVER_HOST": "127.0.0.1",
            "API_SERVER_PORT": str(self.port),
            "API_SERVER_KEY": self.key,
            "OPENROUTER_API_KEY": self.openrouter_key,
            "IDE_WORKSPACE": str(self.workspace),
            "IDE_PLATFORM": "fake",
        }
        path = self.profile(role) / ".env"
        existing = path.read_text() if path.exists() else ""
        kept = [
            line
            for line in existing.splitlines()
            if line.strip() and line.split("=", 1)[0].strip() not in lines
        ]
        path.write_text("\n".join([*kept, *(f"{k}={v}" for k, v in lines.items())]) + "\n")

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start_gateway(self) -> float:
        started = time.monotonic()
        with self.log.open("ab") as log:
            self.process = subprocess.Popen(
                [str(self.hermes_bin), "gateway", "run"],
                env=self.base_env(),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        self.wait_ready()
        return time.monotonic() - started

    def wait_ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_S
        request = urllib.request.Request(
            f"{self.base_url}/v1/capabilities", headers={"Authorization": f"Bearer {self.key}"}
        )
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError(f"gateway exited {self.process.returncode}; see {self.log}")
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.5)
        raise RuntimeError(f"gateway not ready within {READY_TIMEOUT_S} s; see {self.log}")

    def kill_gateway(self) -> None:
        if self.process is not None:
            self.process.kill()
            self.process.wait(timeout=30)
            self.process = None

    def stop_gateway(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=30)
            self.process = None


@pytest.fixture(scope="module")
def host(tmp_path_factory, hermes_bin, openrouter_key) -> Host:
    root = tmp_path_factory.mktemp("s1")
    host = Host(
        hermes_bin=hermes_bin,
        home=root / "host",
        workspace=root / "workspace",
        port=free_port(),
        key=secrets.token_hex(16),
        openrouter_key=openrouter_key,
        log=root / "gateway.log",
    )
    host.home.mkdir()
    host.write_host_dotenv()
    install_env = {**os.environ, "HERMES_HOME": str(host.home)}
    started = time.monotonic()
    for role in ("orchestrator", "researcher"):
        argv = [hermes_bin, "profile", "install", REPO / role, "--name", role, "--yes"]
        result = run(argv, install_env)
        assert result.returncode == 0, result.stdout + result.stderr
        host.write_dotenv(role)
        script = host.profile(role) / "scripts" / "prepare.py"
        argv = [hermes_python(hermes_bin), script, "--source", REPO / role, "--hermes", hermes_bin]
        result = run(argv, host.profile_env(role), timeout=900)
        assert result.returncode == 0, f"prepare {role}:\n{result.stdout}\n{result.stderr}"
    host.timings["install_and_prepare_s"] = round(time.monotonic() - started, 1)
    yield host
    host.stop_gateway()


def wait_terminal(runs: HermesRuns, run_id: str, *, profile: str = "researcher") -> RunStatus:
    deadline = time.monotonic() + RUN_TIMEOUT_S
    while time.monotonic() < deadline:
        status = runs.status(run_id, profile=profile)
        if status.terminal:
            return status
        time.sleep(1)
    raise AssertionError(f"run {run_id} did not end within {RUN_TIMEOUT_S} s")


def wait_running(runs: HermesRuns, run_id: str, *, profile: str = "researcher") -> RunStatus:
    """The first status at or past ``running``: the steer and the kill need a live run."""
    deadline = time.monotonic() + RUN_TIMEOUT_S
    while time.monotonic() < deadline:
        status = runs.status(run_id, profile=profile)
        if status.status != "queued" or status.terminal:
            return status
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} stayed queued for {RUN_TIMEOUT_S} s")


def user_texts(messages: list[dict]) -> list[str]:
    return [m.get("content") for m in messages if m.get("role") == "user"]


def test_s1_three_sessions_steer_stop_kill_resume_and_isolation(host: Host):
    outcome: dict = {"ok": False, "timings": host.timings, "steps": []}
    try:
        _spike(host, outcome)
        outcome["ok"] = True
    finally:
        write_report(outcome)


def _spike(host: Host, outcome: dict) -> None:
    steps, timings = outcome["steps"], host.timings
    timings["gateway_ready_s"] = round(host.start_gateway(), 1)
    runs = HermesRuns(
        base_url=host.base_url,
        keys={"orchestrator": host.key, "researcher": host.key},
        default_profile="orchestrator",
    )
    sids = [mint_session_id() for _ in range(3)]
    t0 = time.monotonic()
    run_ids = [
        runs.start(profile="researcher", session_id=sid, text=PROMPT, idempotency_key=f"s1:{sid}:1")
        for sid in sids
    ]
    timings["three_starts_s"] = round(time.monotonic() - t0, 2)
    steps.append(f"started three runs in profile researcher: {run_ids}")
    assert len(set(run_ids)) == 3

    # Steer the first while it runs.
    first = wait_running(runs, run_ids[0])
    steered = runs.steer(run_ids[0], STEER, profile="researcher") if not first.terminal else False
    steps.append(f"steer of run 1 while {first.status}: accepted={steered}")

    # Stop the second.
    stopped = runs.stop(run_ids[1], profile="researcher")
    second = wait_terminal(runs, run_ids[1])
    steps.append(f"stop of run 2 answered {stopped.status}; it ended {second.status}")
    assert second.status in ("cancelled", "completed"), second

    # Run 1 must end before the gateway dies: a kill interrupts every live run, and the steer
    # is checked on its output.
    first_final = wait_terminal(runs, run_ids[0])
    steps.append(f"run 1 ended {first_final.status}")
    assert first_final.status == "completed", first_final
    if steered:
        # A steer accepted while the model is already writing its one-step reply is kept as
        # ``pending_steer`` on the run instead of being applied; either is the gateway honouring
        # the steer (the watcher steers Sessions mid-task, where the next step picks it up).
        applied = "STEERED" in (first_final.output or "").upper()
        pending = first_final.raw.get("pending_steer")
        steps.append(f"steer of run 1 was {'applied' if applied else 'left pending'}")
        assert applied or pending == STEER, first_final

    # Kill the gateway while the third runs, restart, read the status back, resume the session.
    third = wait_running(runs, run_ids[2])
    steps.append(f"run 3 was {third.status} when the gateway was killed")
    t0 = time.monotonic()
    host.kill_gateway()
    timings["gateway_restart_s"] = round(host.start_gateway(), 1)
    after = runs.status(run_ids[2], profile="researcher")
    steps.append(f"after the restart run 3 reads {after.status} (durable through the key)")
    assert after.terminal, after
    resumed_id = runs.start(
        profile="researcher", session_id=sids[2], text=RESUME, idempotency_key=f"s1:{sids[2]}:2"
    )
    timings["kill_to_resume_s"] = round(time.monotonic() - t0, 1)
    resumed = wait_terminal(runs, resumed_id)
    steps.append(f"resumed session 3 as run {resumed_id}: {resumed.status}")
    assert resumed.status == "completed", resumed

    # Isolation: each session's history holds its own user turns and nothing of the others.
    histories = {sid: runs.session_messages(sid, profile="researcher") for sid in sids}
    for sid, messages in histories.items():
        texts = user_texts(messages)
        assert PROMPT in texts, (sid, texts)
        outcome.setdefault("history_sizes", {})[sid] = len(messages)
    assert STEER in user_texts(histories[sids[0]]) if steered else True
    assert STEER not in user_texts(histories[sids[1]]) + user_texts(histories[sids[2]])
    assert RESUME in user_texts(histories[sids[2]])
    assert RESUME not in user_texts(histories[sids[0]]) + user_texts(histories[sids[1]])
    assistant_turns = [m for m in histories[sids[2]] if m.get("role") == "assistant"]
    assert len(assistant_turns) >= 1, histories[sids[2]]
    steps.append("isolation held: no session saw another's steer or resume text")

    # The stale key replays, it does not start a second run (A5.1).
    replay = runs.start(
        profile="researcher", session_id=sids[2], text=RESUME, idempotency_key=f"s1:{sids[2]}:2"
    )
    assert replay == resumed_id, (replay, resumed_id)
    steps.append("reusing an idempotency key with the same body replayed the run id")
    try:
        runs.start(
            profile="researcher",
            session_id=sids[2],
            text="other",
            idempotency_key=f"s1:{sids[2]}:2",
        )
    except RunsError as error:
        steps.append(f"reusing it with another body answered {error.status} {error.code}")
        assert error.status == 409, error
    else:
        raise AssertionError("a reused key with another body should be refused with 409")


def write_report(outcome: dict) -> None:
    now = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        BEGIN,
        f"Last run: {now} — **{'PASS' if outcome['ok'] else 'FAIL'}**",
        "",
        "Timings (seconds):",
        "",
        *(f"- {name}: {value}" for name, value in outcome["timings"].items()),
        "",
        "Steps:",
        "",
        *(f"- {step}" for step in outcome["steps"]),
        "",
        f"History sizes: `{json.dumps(outcome.get('history_sizes', {}))}`",
        END,
    ]
    text = REPORT.read_text(encoding="utf-8")
    head, _, rest = text.partition(BEGIN)
    _, _, tail = rest.partition(END)
    REPORT.write_text(head + "\n".join(lines) + tail, encoding="utf-8")
