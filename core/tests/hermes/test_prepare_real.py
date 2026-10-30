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
