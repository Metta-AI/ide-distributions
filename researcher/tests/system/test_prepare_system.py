"""Real Hermes installation/update plus distribution-owned retained policy migration."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from support.hermes_python import hermes_python

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.hermes


def test_retained_profile_upgrade(tmp_path):
    home = tmp_path / "host"
    home.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("HERMES_", "RESEARCH_", "SOFTMAX_", "OPENROUTER_"))
        and key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}
    }
    env.update(HERMES_HOME=str(home), HERMES_REAL_HOME=str(home))
    remote = home / "canonical.git"
    for kind, name in [("researcher", "researcher-1"), ("orchestrator", "orchestrator")]:
        source = tmp_path / "sources" / kind
        shutil.copytree(
            ROOT / kind,
            source,
            ignore=shutil.ignore_patterns(
                "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "local", ".cache"
            ),
        )
        # Retained source and installed config predate the new policy.
        config_path = source / "config.yaml"
        defaults = yaml.safe_load(config_path.read_text())
        old = yaml.safe_load(config_path.read_text())
        if kind == "researcher":
            old["approvals"]["deny"] = ["user-prohibited-command"]
            doctrine = source / "doctrine/research-agent.md"
            doctrine.write_text(
                doctrine.read_text().replace("# Research agent doctrine", "# Old research doctrine")
            )
        else:
            old["platform_toolsets"]["api_server"] = ["orchestrator"]
            old["platform_toolsets"]["cron"] = ["orchestrator"]
        old["agent"]["max_turns"] = 23
        config_path.write_text(yaml.safe_dump(old))
        subprocess.run(
            ["hermes", "profile", "install", str(source), "--name", name, "--yes"],
            env=env,
            check=True,
        )
        profile = home / "profiles" / name
        profile_env = env | {
            "HERMES_HOME": str(profile),
            "RESEARCH_WORKSPACE": str(profile / "workspace"),
            "RESEARCH_CANONICAL": str(remote),
            "RESEARCH_EPISODE_LIMIT": "200",
            "RESEARCH_PLAYER_ID": "ply_fixture",
            "SOFTMAX_PLAYER_CREDENTIAL": "cga_fixture",
        }
        (profile / ".env").write_text("USER_SETTING='preserve-me'\n")
        command = [
            hermes_python(),
            str(profile / "scripts/prepare.py"),
            "--source",
            str(source),
            "--hermes",
            shutil.which("hermes"),
            "--activate-supervisor",
        ]
        subprocess.run(command, env=profile_env, check=True, timeout=300)
        workspace = profile / "workspace"
        (workspace / "notes.txt").write_text("uncommitted notes")
        if kind == "orchestrator":
            subprocess.run(["hermes", "cron", "pause", "supervisor"], env=profile_env, check=True)
            jobs_path = profile / "cron/jobs.json"
            jobs = json.loads(jobs_path.read_text())
            supervisor = next(job for job in jobs["jobs"] if job["id"] == "supervisor")
            supervisor.update(no_agent=False, script=None, prompt="Obsolete planner prompt")
            retained_job = dict(supervisor)
            custom_job = {
                "id": "user-script",
                "prompt": None,
                "script": "custom.py",
                "enabled": False,
            }
            jobs["jobs"].append(custom_job)
            jobs_path.write_text(json.dumps(jobs))
        config_path.write_text(yaml.safe_dump(defaults))
        if kind == "researcher":
            shutil.copyfile(ROOT / kind / "doctrine/research-agent.md", doctrine)
        subprocess.run(["hermes", "profile", "update", name, "--yes"], env=env, check=True)
        # Native update preserves installed config; our operation must reconcile owned fields.
        assert yaml.safe_load((profile / "config.yaml").read_text())["agent"]["max_turns"] == 23
        subprocess.run(command, env=profile_env, check=True, timeout=300)
        actual = yaml.safe_load((profile / "config.yaml").read_text())
        assert actual["agent"]["max_turns"] == 23
        assert (workspace / "notes.txt").read_text() == "uncommitted notes"
        assert "USER_SETTING='preserve-me'" in (profile / ".env").read_text()
        if kind == "researcher":
            assert set(defaults["approvals"]["deny"]) <= set(actual["approvals"]["deny"])
            assert "user-prohibited-command" in actual["approvals"]["deny"]
            assert "# Old research doctrine" not in (workspace / "AGENTS.md").read_text()
        else:
            assert set(defaults["platform_toolsets"]["api_server"]) <= set(
                actual["platform_toolsets"]["api_server"]
            )
            assert actual["platform_toolsets"]["cron"] == []
            supervisor = next(
                job
                for job in json.loads(jobs_path.read_text())["jobs"]
                if job["id"] == "supervisor"
            )
            assert supervisor["no_agent"] and supervisor["script"] == "supervisor_gate.py"
            for key, value in retained_job.items():
                if key not in {"no_agent", "script", "prompt"}:
                    assert supervisor[key] == value, key
            assert supervisor["state"] == "paused" and not supervisor["enabled"]
            assert (
                next(
                    job
                    for job in json.loads(jobs_path.read_text())["jobs"]
                    if job["id"] == "user-script"
                )
                == custom_job
            )
        before = (profile / "config.yaml").read_bytes()
        subprocess.run(command, env=profile_env, check=True, timeout=300)
        assert (profile / "config.yaml").read_bytes() == before
    assert yaml.safe_load((home / "config.yaml").read_text())["kanban"]["auto_decompose"] is False
