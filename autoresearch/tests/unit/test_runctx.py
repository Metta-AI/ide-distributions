import json

import pytest

from autoresearch import runctx
from autoresearch.runctx import from_env, ledger_dir, record_worker_identity, runtime_dir


def test_defaults_canonical_under_hermes_home(tmp_path):
    ctx = from_env({"HERMES_HOME": str(tmp_path)})
    assert ctx.hermes_home == tmp_path
    assert ctx.canonical == tmp_path / "workspace"
    assert ctx.worker is None and ctx.card_id is None and ctx.run_id is None


def test_override_and_worker_env(tmp_path):
    env = {
        "HERMES_HOME": str(tmp_path),
        "AUTORESEARCH_WORKSPACE": str(tmp_path / "elsewhere"),
        "HERMES_KANBAN_WORKSPACE": str(tmp_path / "wt"),
        "HERMES_KANBAN_TASK": "t_1",
        "HERMES_KANBAN_RUN_ID": "7",
    }
    ctx = from_env(env)
    assert ctx.canonical == tmp_path / "elsewhere"
    assert ctx.worker == tmp_path / "wt"
    assert (ctx.card_id, ctx.run_id) == ("t_1", "7")


def test_dirs(tmp_path):
    ctx = from_env({"HERMES_HOME": str(tmp_path)})
    assert runtime_dir(ctx) == tmp_path / "workspace/.supervisor/runtime"
    assert ledger_dir(ctx) == tmp_path / "workspace/.supervisor/ledger"






def test_worker_identity_record_fills_task_and_run(tmp_path):
    """Hermes scrubs HERMES_KANBAN_TASK and _RUN_ID from a worker's subprocesses (v0.21.5
    ``scrub_kanban_env``); HERMES_KANBAN_WORKSPACE survives, and the plugin records the
    identity for that worktree."""
    worktree = tmp_path / "workspace" / ".worktrees" / "Q-1"
    home_env = {"HERMES_HOME": str(tmp_path)}
    record_worker_identity(from_env(home_env | {
        "HERMES_KANBAN_WORKSPACE": str(worktree), "HERMES_KANBAN_TASK": "t_1",
        "HERMES_KANBAN_RUN_ID": "7",
    }))  # fmt: skip
    ctx = from_env(home_env | {"HERMES_KANBAN_WORKSPACE": str(worktree)})
    assert (ctx.card_id, ctx.run_id, ctx.worker) == ("t_1", "7", worktree)


def test_worker_identity_record_for_another_worktree_is_ignored(tmp_path):
    runtime = tmp_path / "workspace/.supervisor/runtime/workers"
    runtime.mkdir(parents=True)
    (runtime / "Q-1.json").write_text(
        json.dumps({"task_id": "t_1", "run_id": "7", "worker": str(tmp_path / "elsewhere/Q-1")})
    )
    worktree = tmp_path / "workspace" / ".worktrees" / "Q-1"
    ctx = from_env({"HERMES_HOME": str(tmp_path), "HERMES_KANBAN_WORKSPACE": str(worktree)})
    assert ctx.card_id is None and ctx.run_id is None


def test_a_retry_whose_record_write_fails_never_inherits_the_old_run(tmp_path, monkeypatch):
    worktree = tmp_path / "workspace" / ".worktrees" / "Q-1"
    home_env = {"HERMES_HOME": str(tmp_path), "HERMES_KANBAN_WORKSPACE": str(worktree)}
    first = from_env(home_env | {"HERMES_KANBAN_TASK": "t_1", "HERMES_KANBAN_RUN_ID": "7"})
    record_worker_identity(first)
    record = json.loads((tmp_path / "workspace/.supervisor/runtime/workers/Q-1.json").read_text())
    assert record["run_id"] == "7" and isinstance(record["started_at"], float)

    def broken_replace(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(runctx.os, "replace", broken_replace)
    retry = from_env(home_env | {"HERMES_KANBAN_TASK": "t_1", "HERMES_KANBAN_RUN_ID": "9"})
    with pytest.raises(OSError):
        record_worker_identity(retry)
    ctx = from_env(home_env)
    assert ctx.card_id is None and ctx.run_id is None  # commands fail clearly, not as run 7

