import pytest

from ide_research_core import launch


def test_sync_runs_once_per_lock(tmp_path):
    (tmp_path / "uv.lock").write_text("a")
    calls = []
    run = lambda argv, env, check: calls.append(argv)
    assert launch.ensure_venv(tmp_path, run=run) is True
    (tmp_path / "local" / "venv").mkdir(parents=True, exist_ok=True)
    assert launch.ensure_venv(tmp_path, run=run) is False and len(calls) == 1
    assert calls[0] == ["uv", "sync", "--frozen", "--no-dev", "--project", str(tmp_path)]
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


def test_sync_failure_becomes_launch_error(tmp_path):
    import subprocess

    (tmp_path / "uv.lock").write_text("a")

    def run(argv, env, check):
        raise subprocess.CalledProcessError(2, argv)

    with pytest.raises(launch.LaunchError, match="uv sync failed"):
        launch.ensure_venv(tmp_path, run=run)


def test_main_returns_1_on_launch_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    def fail(home):
        raise launch.LaunchError("install uv")

    monkeypatch.setattr(launch, "ensure_venv", fail)
    assert launch.main([]) == 1


def test_main_execs_venv_python(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    python = tmp_path / "local" / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    monkeypatch.setattr(launch, "ensure_venv", lambda home: False)
    calls = []
    monkeypatch.setattr(launch.os, "execv", lambda path, args: calls.append((path, args)))
    launch.main(["--x"])
    assert calls == [(str(python), [str(python), "-m", "ide_research_core", "--x"])]


def test_main_returns_1_when_venv_python_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(launch, "ensure_venv", lambda home: False)
    assert launch.main([]) == 1


def test_image_linked_venv_is_never_resynced(tmp_path):
    """Hosting links local/venv to the image's read-only environment and owns it: an agent's
    edited uv.lock never syncs into it or stops the launcher."""
    (tmp_path / "uv.lock").write_text("lock-v2")
    image = tmp_path / "image-venv"
    image.mkdir()
    (image / ".lock-sha").write_text("an older lock")
    (tmp_path / "local").mkdir()
    (tmp_path / "local/venv").symlink_to(image)
    calls = []
    assert launch.ensure_venv(tmp_path, run=lambda *a, **k: calls.append(a)) is False
    assert calls == []
