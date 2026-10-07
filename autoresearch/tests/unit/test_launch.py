import pytest

from autoresearch import launch


def test_sync_runs_once_per_lock(tmp_path):
    (tmp_path / "uv.lock").write_text("a")
    calls = []
    run = lambda argv, env, check: calls.append(argv)
    assert launch.ensure_venv(tmp_path, run=run) is True
    (tmp_path / "local" / "venv").mkdir(parents=True, exist_ok=True)
    assert launch.ensure_venv(tmp_path, run=run) is False and len(calls) == 1
    (tmp_path / "uv.lock").write_text("b")
    assert launch.ensure_venv(tmp_path, run=run) is True


def test_missing_uv_is_actionable(tmp_path):
    (tmp_path / "uv.lock").write_text("a")

    def run(*a, **k):
        raise FileNotFoundError("uv")

    with pytest.raises(launch.LaunchError, match="install uv"):
        launch.ensure_venv(tmp_path, run=run)


def test_concurrent_launchers_sync_once(tmp_path):
    import threading
    import time

    (tmp_path / "uv.lock").write_text("a")
    calls = []

    def run(argv, env, check):
        time.sleep(0.3)
        calls.append(argv)
        (tmp_path / "local" / "venv").mkdir(parents=True, exist_ok=True)

    ts = [
        threading.Thread(target=launch.ensure_venv, args=(tmp_path,), kwargs={"run": run})
        for _ in range(2)
    ]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1
