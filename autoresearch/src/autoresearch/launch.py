"""Stdlib-only launcher for cron scripts.

Keeps `$HERMES_HOME/local/venv` in sync with `uv.lock`, then replaces the process with
`python -m autoresearch.runtime.<job>` from that venv. Must import only the standard library
because it runs before the venv exists.
"""

import fcntl
import hashlib
import os
import subprocess
import sys
from pathlib import Path


class LaunchError(RuntimeError):
    pass


def lock_sha(hermes_home: Path) -> str:
    return hashlib.sha256((hermes_home / "uv.lock").read_bytes()).hexdigest()


def sync_argv(hermes_home: Path) -> list[str]:
    """``--no-dev``: the production venv gets runtime dependencies only (no pytest, ruff)."""
    return ["uv", "sync", "--frozen", "--no-dev", "--project", str(hermes_home)]


def ensure_venv(hermes_home: Path, run=subprocess.run) -> bool:
    """Sync the venv if uv.lock changed since the last sync. Returns True if it synced."""
    local_dir = hermes_home / "local"
    venv_dir = local_dir / "venv"
    stamp = venv_dir / ".lock-sha"
    wanted = lock_sha(hermes_home)
    local_dir.mkdir(parents=True, exist_ok=True)
    with open(local_dir / "venv.lock", "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        if stamp.exists() and stamp.read_text().strip() == wanted:
            return False
        env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(venv_dir)}
        try:
            run(sync_argv(hermes_home), env=env, check=True)
        except FileNotFoundError as error:
            raise LaunchError(
                "uv not found on PATH: install uv (https://docs.astral.sh/uv/)"
            ) from error
        except subprocess.CalledProcessError as error:
            raise LaunchError(f"uv sync failed with exit code {error.returncode}") from error
        venv_dir.mkdir(parents=True, exist_ok=True)
        stamp.write_text(wanted)
        return True


def main(job: str, argv: list[str] | None = None) -> int:
    hermes_home = Path(os.environ.get("HERMES_HOME") or Path(__file__).resolve().parents[2])
    try:
        ensure_venv(hermes_home)
    except LaunchError as error:
        print(f"autoresearch launcher: {error}", file=sys.stderr)
        return 1
    python = str(hermes_home / "local" / "venv" / "bin" / "python")
    if not Path(python).exists():
        print(
            f"autoresearch launcher: venv python missing at {python}; "
            "delete local/venv and .lock-sha, then rerun to resync",
            file=sys.stderr,
        )
        return 1
    args = sys.argv[1:] if argv is None else argv
    os.execv(python, [python, "-m", f"autoresearch.runtime.{job}", *args])
