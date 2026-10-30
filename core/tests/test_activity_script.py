"""The host's autosleep probe, ``orchestrator/scripts/activity.py``, against this environment."""

import json
import subprocess
import sys
from pathlib import Path

from research_core import layout
from research_core.store.actors import Actor
from research_core.store.db import Store

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "orchestrator" / "scripts" / "activity.py"


def run_activity(tmp_path, *, env_extra=None) -> subprocess.CompletedProcess:
    # This test environment stands in for the workspace environment: it has research_core.
    env = {
        "PATH": "/usr/bin:/bin",
        "IDE_WORKSPACE": str(tmp_path / "ws"),
        "IDE_WORKSPACE_ENV": sys.prefix,
        **(env_extra or {}),
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, check=False
    )


def test_an_empty_workspace_permits_sleep(tmp_path):
    result = run_activity(tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"waiting_on": []}


def test_reports_the_watchers_reasons_and_honours_pause(tmp_path):
    ws = layout.from_env({"IDE_WORKSPACE": str(tmp_path / "ws")})
    ws.ensure_dirs()
    with Store.open(ws.store_path, kind="workspace") as wstore:
        wstore.set_meta("heartbeat", "2026-10-29T18:00:00Z")
        wstore.set_meta("has_work", "1")
        wstore.set_meta("has_work_reasons", json.dumps(["task:T-3:running", "wake:queued"]))
    result = run_activity(tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "waiting_on": ["task:T-3:running", "wake:queued"]
    }
    from research_core.store import settings

    with Store.open(ws.store_path, kind="workspace") as wstore:
        settings.update(wstore, Actor.user("u1"), "paused", True, cites="msg:1")
    result = run_activity(tmp_path)
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"waiting_on": []}


def test_a_missing_workspace_environment_is_an_empty_workspace(tmp_path):
    result = run_activity(tmp_path, env_extra={"IDE_WORKSPACE_ENV": str(tmp_path / "nowhere")})
    assert result.returncode == 0
    assert json.loads(result.stdout.strip()) == {"waiting_on": []}
