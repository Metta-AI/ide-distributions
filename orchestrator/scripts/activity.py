"""Background activity for the host's automatic sleep (stdlib only; the host runs it).

The host runs this file in the installed Orchestrator profile with the profile's environment
and reads one JSON line: ``{"waiting_on": [reason, ...]}``. A non-empty list means the
workspace has background work and must stay awake: a live Researcher run, a Task waiting on
XP, credits, a submission or a process, an autonomous-submission candidate, or a queued wake.
An empty list permits sleep, and so does an explicitly paused workspace. The reasons are the
ones the watcher recorded at its last tick (``research watcher status``); nothing here polls
the platform or writes a record. A workspace without a store yet is empty.

A failure to answer exits non-zero, which the host must treat as "do not sleep".
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def workspace_python() -> Path:
    env = os.environ
    if env.get("IDE_WORKSPACE_ENV"):
        return Path(env["IDE_WORKSPACE_ENV"]).expanduser() / "bin" / "python"
    if env.get("IDE_WORKSPACE"):
        root = Path(env["IDE_WORKSPACE"]).expanduser()
    elif env.get("HERMES_REAL_HOME"):
        root = Path(env["HERMES_REAL_HOME"]).expanduser() / "workspace"
    else:
        root = Path.home() / ".hermes" / "workspace"
    return root / "tools" / "venv" / "bin" / "python"


def waiting_on(status: dict) -> list[str]:
    """The reasons to stay awake from a ``research watcher status`` payload."""
    if not status.get("ok"):
        if "no workspace store" in str(status.get("error", "")):
            return []  # nothing was ever set up: an empty workspace
        raise RuntimeError(status.get("error") or "watcher status failed")
    if status.get("paused"):
        return []
    reasons = status.get("waiting_on")
    if isinstance(reasons, list):
        return [str(reason) for reason in reasons]
    return ["work"] if status.get("has_work") else []


def workspace_env() -> dict:
    """Hermes's interpreter variables dropped, so the workspace's Python imports its own
    packages (not Hermes's pydantic_core, built for another interpreter)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env.pop("VIRTUAL_ENV", None)
    env.pop("__PYVENV_LAUNCHER__", None)
    bin_dir = str(workspace_python().parent)
    env["PATH"] = os.pathsep.join(
        [bin_dir, *[p for p in env.get("PATH", "").split(os.pathsep) if p]]
    )
    return env


def main() -> int:
    python = workspace_python()
    if not python.exists():
        # Never prepared: there is no workspace to keep awake.
        print(json.dumps({"waiting_on": []}))
        return 0
    completed = subprocess.run(
        [str(python), "-m", "research_core.cli", "watcher", "status"],
        env=workspace_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    try:
        status = json.loads(lines[-1]) if lines else {}
        reasons = waiting_on(status)
    except (ValueError, RuntimeError) as error:
        print(completed.stderr.strip()[-2000:], file=sys.stderr)
        print(json.dumps({"error": f"watcher status unreadable: {error}"}), file=sys.stderr)
        return 1
    print(json.dumps({"waiting_on": reasons}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
